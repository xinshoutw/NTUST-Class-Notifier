import asyncio
import logging
from dataclasses import dataclass

import httpx

# 建立 logger
logger = logging.getLogger(__name__)


@dataclass
class QueryPayload:
    """
    用於封裝查詢課程 API 時的 payload 參數。
    """
    semester: str = "1132"
    course_no: str = ""
    CourseName: str = ""
    CourseTeacher: str = ""
    Dimension: str = ""
    CourseNotes: str = ""
    CampusNotes: str = ""
    ForeignLanguage: int = 0
    OnlyGeneral: int = 0
    OnleyNTUST: int = 0
    OnlyMaster: int = 0
    OnlyUnderGraduate: int = 0
    OnlyNode: int = 0
    Language: str = "zh"


class CourseClient:
    """
    用於封裝課程相關的資料請求與日誌紀錄
    """

    def __init__(self, payloads: list[QueryPayload]):
        """
        初始化時，必須提供一個 QueryPayload 實例。
        """
        self.payloads = tuple(payloads)
        for payload in self.payloads:
            logger.debug(f"CourseClient initialized with payload={payload}")

    async def get_courses(self) -> list[tuple[str, int, int]]:
        """
        批次查詢多門課程，並行化發送請求。
        """
        url = "https://querycourse.ntust.edu.tw/querycourse/api/courses"

        # 只在此處建立一次 AsyncClient，後續在整個迴圈中重複使用
        async with httpx.AsyncClient(timeout=10.0) as client:
            tasks = [
                self.fetch_course(client, url, self.build_payload(payload))
                for payload in self.payloads
            ]
            results = await asyncio.gather(*tasks)  # 等待所有查詢完成
        return results

    @staticmethod
    def build_payload(payload: QueryPayload):
        return {
            "Semester": payload.semester,
            "CourseNo": payload.course_no,
            "CourseName": payload.CourseName,
            "CourseTeacher": payload.CourseTeacher,
            "Dimension": payload.Dimension,
            "CourseNotes": payload.CourseNotes,
            "CampusNotes": payload.CampusNotes,
            "ForeignLanguage": payload.ForeignLanguage,
            "OnlyGeneral": payload.OnlyGeneral,
            "OnleyNTUST": payload.OnleyNTUST,
            "OnlyMaster": payload.OnlyMaster,
            "OnlyUnderGraduate": payload.OnlyUnderGraduate,
            "OnlyNode": payload.OnlyNode,
            "Language": payload.Language
        }

    @staticmethod
    async def fetch_course(client: httpx.AsyncClient, url: str, payload: dict) -> tuple[str, int, int]:
        """
        單筆課程查詢函式：使用現有 client 對指定 url 發出 POST 請求。
        返回 (課程識別, 目前人數, 人數上限)。
        """
        try:
            logger.debug(f"POST URL={url} | Payload={payload}")
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            # 若沒有回傳資料，表示查無此課程
            if not data:
                logger.debug(f"課程 {payload['CourseNo']} 查無資料或回應為空。")
                return payload["CourseNo"], 0, 0

            first_course = data[0]
            course_name = first_course.get("CourseName", "未知課程名稱")
            cur_member = int(first_course.get("ChooseStudent", 0))
            member_limit = int(first_course.get("Restrict1", 0))

            logger.debug(
                f"成功取得課程 {payload['CourseNo']} 數據：{course_name}, "
                f"人數={cur_member}, 上限={member_limit}"
            )
            return f"{payload['CourseNo']} {course_name}", cur_member, member_limit

        except httpx.RequestError as e:
            logger.debug(f"課程 {payload['CourseNo']} 請求錯誤：{e}")
            return payload["CourseNo"], 0, 0
        except (ValueError, KeyError) as e:
            logger.debug(f"課程 {payload['CourseNo']} 資料解析錯誤：{e}")
            return payload["CourseNo"], 0, 0
