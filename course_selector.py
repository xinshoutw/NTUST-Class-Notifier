"""NTUST 選課系統自動加選 + session keepalive"""

import asyncio
import logging
import re
import sqlite3
import subprocess
import sys
from functools import partial
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

COURSE_SELECTION_ROOT = "https://courseselection.ntust.edu.tw/"

# 初選加退選 (First/A06)
DEPT_SELECT_URL = "https://courseselection.ntust.edu.tw/First/A06/A06"
DEPT_SELECT_JOIN_URL = "https://courseselection.ntust.edu.tw/First/A06/ExtraJoin"
COURSE_LIST_URL = "https://courseselection.ntust.edu.tw/First/A02/A02"

# 全校加退選 (AddAndSub/B01)
OPEN_SELECT_URL = "https://courseselection.ntust.edu.tw/AddAndSub/B01/B01"
OPEN_SELECT_JOIN_URL = "https://courseselection.ntust.edu.tw/AddAndSub/B01/ExtraJoin"
OPEN_SELECT_DROP_URL = "https://courseselection.ntust.edu.tw/AddAndSub/B01/SubCourse"

# 授權碼加選 (Authorization/C01)
AUTH_CODE_URL = "https://courseselection.ntust.edu.tw/Authorization/C01/C01"
AUTH_CODE_JOIN_URL = "https://courseselection.ntust.edu.tw/Authorization/C01/ExtraJoin"

AJAX_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

# 從選課頁面 HTML 中抓取已選課程代碼
ENROLLED_COURSE_PATTERN = re.compile(r'class="table-cell">\s*([A-Z]{2}[A-Z0-9]{7})\s*<')
WISH_LIST_PATTERN = re.compile(
    r'<tr\s+class="data"[^>]*>\s*'
    r'<td[^>]*>\s*\d+\s*</td>\s*'
    r'<td[^>]*>\s*([A-Z]{2}[A-Z0-9]{7})\s*</td>',
    re.DOTALL,
)
# 全校加退選頁面的已選課程表 #cartTable，課碼在第一欄
CART_COURSE_PATTERN = re.compile(
    r'<tr\s+class="data"[^>]*>\s*<td[^>]*>\s*([A-Z]{2}[A-Z0-9]{7})\s*<'
)
# B01/C01 的加選結果不在 POST 回應（Content-Length: 0），而是下一次載入頁面時
# 單獨一段 <script> 裡的 alert()。沒有 alert 就代表成功。
FLASH_ALERT_PATTERN = re.compile(
    r'<script type="text/javascript">\s*alert\("(.*?)"\);\s*</script>', re.DOTALL
)

OIDC_MARKERS = (
    frozenset({"code", "state", "iss"}),
    frozenset({"id_token"}),
    frozenset({"SAMLResponse"}),
    frozenset({"RelayState"}),
    frozenset({"wresult"}),
    frozenset({"wctx"}),
)

DB_PATH = Path(__file__).parent / "cookies.sqlite3"


def _form_payload(form) -> dict[str, str]:
    return {
        i.get("name"): i.get("value", "")
        for i in form.find_all("input") if i.get("name")
    }


def _is_login_page(resp: httpx.Response) -> bool:
    if "ssoam2.ntust.edu.tw" not in str(resp.url):
        return False
    soup = BeautifulSoup(resp.text, "html.parser")
    if soup.find("form", id="loginForm"):
        return True
    names = {i.get("name", "") for i in soup.find_all("input")}
    return "Username" in names and "Password" in names


def _flash_alert(html: str) -> str:
    """取出頁面上的一次性提示訊息，空字串代表操作成功"""
    m = FLASH_ALERT_PATTERN.search(html)
    return m.group(1).strip() if m else ""


def _find_bridge_form(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.find_all("form"):
        action = (form.get("action") or "").strip().lower()
        if not action or "logout" in action:
            continue
        names = {(i.get("name") or "").strip() for i in form.find_all("input")}
        if not names or {"Username", "Password"} & names:
            continue
        if any(marker <= names for marker in OIDC_MARKERS):
            return form
    return None


class CookieStore:
    def __init__(self, db_path: Path = DB_PATH):
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cookies "
            "(account TEXT, name TEXT, value TEXT, domain TEXT, path TEXT, "
            "PRIMARY KEY (account, name, domain, path))"
        )
        self._db.commit()

    def load_into(self, account: str, client: httpx.Client) -> bool:
        rows = self._db.execute(
            "SELECT name, value, domain, path FROM cookies WHERE account=?", (account,)
        ).fetchall()
        if not rows:
            return False
        client.cookies.clear()
        for name, value, domain, path in rows:
            client.cookies.set(name, value, domain=domain, path=path)
        return True

    def save_from(self, account: str, client: httpx.Client):
        self._db.execute("DELETE FROM cookies WHERE account=?", (account,))
        self._db.executemany(
            "INSERT INTO cookies VALUES (?,?,?,?,?)",
            [(account, c.name, c.value, c.domain or "", c.path or "/")
             for c in client.cookies.jar],
        )
        self._db.commit()

    def delete(self, account: str):
        self._db.execute("DELETE FROM cookies WHERE account=?", (account,))
        self._db.commit()

    def close(self):
        self._db.close()


class CourseSelector:
    """SSO 登入 + 自動加選（初選加退選 / 全校加退選 / 授權碼）"""

    def __init__(self, student_id: str, password: str):
        self._account = student_id.strip().upper()
        self._password = password
        self._store = CookieStore()
        self._client = httpx.Client(
            http2=True,
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": BROWSER_UA},
        )
        self._logged_in = False
        self.phase = "unknown"  # dept=初選加退選, open=全校加退選

    def login(self) -> bool:
        if self._logged_in:
            return True
        self._store.load_into(self._account, self._client)
        try:
            resp = self._resolve_bridges(self._client.get(COURSE_SELECTION_ROOT))
            if _is_login_page(resp):
                logger.info("Session 過期，重新 SSO 登入...")
                self._client.cookies.clear()
                self._store.delete(self._account)
                resp = self._client.get(COURSE_SELECTION_ROOT)
                if _is_login_page(resp):
                    resp = self._submit_login(resp)
                resp = self._resolve_bridges(resp)
                if _is_login_page(resp):
                    logger.error("SSO 登入失敗")
                    return False
            self._store.save_from(self._account, self._client)
            self._logged_in = True
            logger.info("選課系統登入成功")
            return True
        except Exception:
            logger.exception("登入過程發生錯誤")
            return False

    def keepalive(self) -> bool:
        """訪問選課頁面以延長 session，順便更新目前選課階段"""
        try:
            phase = self.detect_phase()
            if not self._logged_in:
                logger.warning("Session 已過期，嘗試重新登入...")
                return self.login()
            self._store.save_from(self._account, self._client)
            logger.debug(f"Session keepalive OK (phase={phase})")
            return True
        except Exception as e:
            logger.error(f"Keepalive 失敗: {e}")
            return False

    def detect_phase(self) -> str:
        """探測目前是哪個選課階段

        非該階段時伺服器會 302 導回 Home/Index，所以「最終網址還停在原頁」
        就是目前開放的階段。

        回傳 'dept'(初選加退選) / 'open'(全校加退選) / 'unknown'
        """
        if not self._logged_in and not self.login():
            return "unknown"
        for phase, url in (("dept", DEPT_SELECT_URL), ("open", OPEN_SELECT_URL)):
            resp = self._client.get(url)
            if _is_login_page(resp):
                self._logged_in = False
                return "unknown"
            if str(resp.url) == url:
                self.phase = phase
                return phase
        self.phase = "unknown"
        return "unknown"

    def _current_phase(self) -> str:
        return self.phase if self.phase != "unknown" else self.detect_phase()

    def select_course(self, course_no: str) -> tuple[bool, str]:
        """依目前階段自動加選"""
        if self._current_phase() == "open":
            return self.select_course_open(course_no)
        return self.select_course_dept(course_no)

    def select_course_dept(self, course_no: str) -> tuple[bool, str]:
        """初選加退選 (First/A06) 加選"""
        if not self._logged_in and not self.login():
            return False, "登入失敗"
        try:
            resp = self._client.post(
                DEPT_SELECT_JOIN_URL,
                data={"CourseNo": course_no, "type": "3"},
                headers=AJAX_HEADERS,
            )
            self._store.save_from(self._account, self._client)
            body = resp.text
            logger.info(f"初選加退選 {course_no} 回應: {resp.status_code} | {body[:200]}")
            return resp.is_success, body
        except Exception as e:
            logger.error(f"加選 {course_no} 發生錯誤: {e}")
            return False, str(e)

    def select_course_open(self, course_no: str) -> tuple[bool, str]:
        """全校加退選 (AddAndSub/B01) 加選"""
        return self._post_and_read_flash(
            OPEN_SELECT_JOIN_URL, OPEN_SELECT_URL,
            {"CourseNo": course_no, "type": "3"}, f"全校加退選 {course_no}",
        )

    def drop_course_open(self, course_no: str) -> tuple[bool, str]:
        """全校加退選 (AddAndSub/B01) 退選"""
        return self._post_and_read_flash(
            OPEN_SELECT_DROP_URL, OPEN_SELECT_URL,
            {"CourseNo": course_no, "type": "2"}, f"全校退選 {course_no}",
        )

    def select_course_auth(
            self,
            course_no: str,
            auth_code: str,
            category: str = "A",
            ntnu_serial_no: str = "",
            serial_no: str = "",
            class_no: str = "",
    ) -> tuple[bool, str]:
        """授權碼加選 (Authorization/C01)

        category: A=一般授權碼(只要課號+授權碼), B=另需台師大流水號, C=另需流水號
        """
        return self._post_and_read_flash(
            AUTH_CODE_JOIN_URL, AUTH_CODE_URL,
            {
                "Category": category,
                "CourseNo": course_no,
                "AuthCode": auth_code,
                "NTNUSerialNo": ntnu_serial_no,
                "SerialNo": serial_no,
                "Class": class_no,
                "type": "3",
            },
            f"授權碼加選 {course_no}",
        )

    def _post_and_read_flash(
            self, join_url: str, page_url: str, data: dict[str, str], label: str
    ) -> tuple[bool, str]:
        """送出加/退選後重新載入頁面讀取結果訊息（POST 本身回傳空 body）

        ponytail: flash 是 server 端 one-shot 的，同一 session 若有其他請求
        搶先載入頁面就會把訊息吃掉；訊息僅供參考，真正結果以 verify_enrolled 為準。
        """
        if not self._logged_in and not self.login():
            return False, "登入失敗"
        try:
            resp = self._client.post(join_url, data=data, headers=AJAX_HEADERS)
            if not resp.is_success:
                logger.error(f"{label} 失敗: HTTP {resp.status_code}")
                return False, f"HTTP {resp.status_code}"
            page = self._client.get(page_url)
            if _is_login_page(page):
                self._logged_in = False
                return False, "Session 過期"
            self._store.save_from(self._account, self._client)
            msg = _flash_alert(page.text)
            logger.info(f"{label}: {msg or '成功'}")
            return not msg, msg or "成功"
        except Exception as e:
            logger.error(f"{label} 發生錯誤: {e}")
            return False, str(e)

    def verify_enrolled(self, course_no: str) -> bool:
        """檢查課程是否已出現在已選清單中"""
        if not self._logged_in and not self.login():
            return False
        try:
            url = OPEN_SELECT_URL if self._current_phase() == "open" else COURSE_LIST_URL
            html = self._client.get(url).text
            all_courses = set(
                ENROLLED_COURSE_PATTERN.findall(html)
                + WISH_LIST_PATTERN.findall(html)
                + CART_COURSE_PATTERN.findall(html)
            )
            found = course_no.upper() in all_courses
            logger.info(f"驗證加選結果: {course_no} {'已在清單中 ✓' if found else '未在清單中 ✗'}")
            return found
        except Exception as e:
            logger.error(f"驗證加選結果時發生錯誤: {e}")
            return False

    def close(self):
        self._client.close()
        self._store.close()

    def _resolve_bridges(self, resp: httpx.Response, max_steps: int = 3) -> httpx.Response:
        current = resp
        for _ in range(max_steps):
            if _is_login_page(current):
                return current
            form = _find_bridge_form(current.text)
            if not form:
                return current
            action = urljoin(str(current.url), form.get("action"))
            payload = _form_payload(form)
            current = self._client.post(action, data=payload)
        return current

    def _submit_login(self, resp: httpx.Response) -> httpx.Response:
        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form", id="loginForm")
        if not form:
            raise RuntimeError("找不到 SSO 登入表單")
        payload = _form_payload(form)
        payload.update(Username=self._account, Password=self._password)
        payload.setdefault("captcha", "")
        action = urljoin(str(resp.url), form.get("action") or str(resp.url))
        return self._client.post(action, data=payload)


async def run_sync(func, *args):
    """在 executor 中跑同步函式"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args))


def play_sound(sound_type: str = "vacancy"):
    """播放系統音效 (macOS)"""
    if sys.platform != "darwin":
        return
    sounds = {
        "vacancy": "Glass",       # 有空位
        "success": "Hero",        # 加選成功
        "failure": "Sosumi",      # 加選失敗
    }
    sound_name = sounds.get(sound_type, "Glass")
    subprocess.Popen(
        ["afplay", f"/System/Library/Sounds/{sound_name}.aiff"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _self_check():
    """離線自我檢查：驗證 flash 訊息與已選課表的解析"""
    ok_page = '<script type="text/javascript">\n\n\n</script>'
    fail_page = ('<script type="text/javascript">\n'
                 '    alert("課程不存在!");\n</script>')
    dup_page = ('<script type="text/javascript">\n'
                '    alert("這門課已經在您的選課表或已經修過，請勿重複選課(課碼、課名重複)。");\n'
                '</script>')
    assert _flash_alert(ok_page) == ""
    assert _flash_alert(fail_page) == "課程不存在!"
    assert "重複選課" in _flash_alert(dup_page)
    # 頁面內建的 JS（包在 $(document).ready 裡）不該被誤判成 flash
    assert _flash_alert('<script type="text/javascript">\n$(function(){alert("系統錯誤!");});\n</script>') == ""

    cart = ('<tr class="data" style="border: 1px solid"><td style="x">\n CS3051701 \n</td>'
            '<td>次世代無線網路概論</td></tr>')
    assert CART_COURSE_PATTERN.findall(cart) == ["CS3051701"]
    # A02 的願望清單第一欄是序號，不該被 cart 樣式吃掉
    assert CART_COURSE_PATTERN.findall(
        '<tr class="data"><td>1</td><td>CS3051701</td></tr>') == []
    print("self-check OK")


if __name__ == "__main__":
    # python course_selector.py check
    # python course_selector.py phase
    # python course_selector.py auth <課號> <授權碼> [A|B|C] [流水號]
    import os

    import dotenv

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        _self_check()
        sys.exit(0)

    dotenv.load_dotenv()
    _sel = CourseSelector(os.environ["STUDENT_ID"], os.environ["PASSWORD"])
    try:
        if cmd == "phase":
            print(f"目前階段: {_sel.detect_phase()}")
        elif cmd == "auth":
            _course, _code = sys.argv[2], sys.argv[3]
            _cat = sys.argv[4] if len(sys.argv) > 4 else "A"
            _serial = sys.argv[5] if len(sys.argv) > 5 else ""
            _ok, _msg = _sel.select_course_auth(
                _course, _code, _cat,
                ntnu_serial_no=_serial if _cat == "B" else "",
                serial_no=_serial if _cat == "C" else "",
            )
            print(f"{'✅' if _ok else '❌'} {_msg}")
        else:
            print(__doc__)
    finally:
        _sel.close()
