# NTUST-Class-Notifier

![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)
![MIT License](https://camo.githubusercontent.com/cd878d57e2b361acc4718461dd7a9c2828f3c132dcfb18d363883883a7df60a3/68747470733a2f2f696d672e736869656c64732e696f2f6769746875622f6c6963656e73652f496c65726961796f2f6d61726b646f776e2d6261646765733f7374796c653d666f722d7468652d6261646765)

本專案旨在來查詢台科大課程空額，可選擇將結果透過 **Discord Bot** 私訊通知指定使用者。  
程式中並未包含任何**搶課**或類似之行為

## 專案結構

```
.
├── course_lookup.py       # 課程查詢
├── discord_bot.py         # Bot 的啟動與私訊功能
├── main.py                # 主程式
└── that-is-my-class.iml   # IntelliJ 專案檔 (可忽略)
```

## 功能簡述

1. **課程查詢**  
   - 透過 `querycourse.ntust.edu.tw` 的 API 查詢課程資料，如課程代碼、目前選課人數與人數上限等。  
   - 可一次查詢多門課程並並發處理，快速得知哪些課程尚有名額。

2. **Discord Bot 通知**  
   - 若有設定對應環境變數，啟動後會連線至 Discord，並在偵測到課程有空位時，自動傳送私訊給指定用戶。  
   - 若未設定與 Discord 相關的環境變數，則僅在終端顯示查詢結果，不會觸發任何機器人行為。

## 安裝與使用

### 1. 先決條件

- **Python 3.8+** (開發環境 3.12.6)

### 2. 取得程式

```bash
git clone https://github.com/xinshoutw/NTUST-Class-Notifier.git
cd NTUST-Class-Notifier
```

### 3. 安裝套件

```bash
pip install -r requirements.txt
```

### 4. 設定環境變數

在執行前，必須／可選設定下列環境變數：  

1. **必須** - 課程查詢列表  
   - `LOOK_UP_CLASSES`  
     - 格式: `{學期}&{課程代碼};{學期}&{課程代碼};...`  
     - 範例: `1132&CC1257304;1132&ETG301301;1132&ATG002301;1132&MBG214301;1132&DTG004301`  
     - 代表一次要查詢多個課程，使用分號 `;` 進行分隔。

2. **可選** - Discord 通知  
   - `DISCORD_BOT_TOKEN`  
     - 若沒有設定，程式執行時不會啟動 Discord Bot，不會傳送通知。  
   - `DISCORD_TARGET_USER_IDS`  
     - 使用分號 `;` 分隔多個 Discord User IDs。  
     - 例如 `810822763601461318;1278934756926423052`。  
     - 代表若有空位，會對這些 ID 的使用者傳送私訊通知。

#### 設定方式範例
這些變數寫入 `.env` 檔案中

或透過以下指令設定
- **Linux / macOS**:  
  ```bash
  export LOOK_UP_CLASSES="1132&CC1257304;1132&ETG301301"
  export DISCORD_BOT_TOKEN="r9mfsU..."
  export DISCORD_TARGET_USER_IDS="810822763601461318;1278934756926423052"
  ```
  
- **Windows (CMD)**:  
  ```cmd
  set LOOK_UP_CLASSES=1132&CC1257304;1132&ETG301301
  set DISCORD_BOT_TOKEN=r9mfsU...
  set DISCORD_TARGET_USER_IDS=810822763601461318;1278934756926423052
  ```
  
- **Windows (Powershell)**:  
  ```powershell
  $env:LOOK_UP_CLASSES="1132&CC1257304;1132&ETG301301"
  $env:DISCORD_BOT_TOKEN="r9mfsU..."
  $env:DISCORD_TARGET_USER_IDS="810822763601461318;1278934756926423052"
  ```



### 5. 執行

完成上述設定後，直接執行：

```bash
python main.py
```

程式會根據你的環境變數設定，  
- 讀取 `LOOK_UP_CLASSES` 裡的每門課，  
- 透過 `course_lookup.py` 進行查詢，  
- 若有 `DISCORD_BOT_TOKEN` 及 `DISCORD_TARGET_USER_IDS`，便會啟動 Discord Bot，並在偵測到課程有空位時，傳送私訊給這些用戶。

## 系統流程簡述

1. `main.py`  
   - **主入口**：解析環境變數 → 建立 `CourseClient`（或類似物件） → 啟動課程查詢  
   - 若偵測到環境變數有 `DISCORD_BOT_TOKEN`，則啟動 `DiscordBot`，並連接到 Discord。

2. `course_lookup.py`  
   - **課程查詢邏輯**：與台科大 `querycourse` API 互動，並使用非同步 HTTP 請求 (透過 `httpx`)。  
   - 可支援並發查詢多門課程，提高效能。

3. `discord_bot.py`  
   - **Discord Bot**：若有啟用，會在課程查詢到空位時，主動私訊指定的 Discord User IDs。  
   - 使用 `discord.py` SDK 連線到 Discord 伺服器。

## 常見問題

1. **為什麼沒有任何 Discord 訊息通知？**  
   - 請檢查是否已設定 `DISCORD_BOT_TOKEN`、`DISCORD_TARGET_USER_IDS`。  
   - 確保 Bot 有正確的「Developer Mode」設定與權限，且與該用戶共享伺服器且使用者允許私訊。

2. **如何更改查詢間隔或頻率？**  
   - 檢查 `main.py` 中的 `asyncio.sleep(...)` 或排程邏輯，調整或自訂需要的頻率。

3. **如何增加/減少查詢課程數量？**  
   - 只需在 `LOOK_UP_CLASSES` 中增刪 `{學期}&{課程代碼}` 的組合，並用 `;` 分隔。

4. **遇到 429 / Too Many Requests**  
   - 表示對伺服器發送的請求過多，被暫時限制 (rate limit)。  
   - 可考慮在 `course_lookup.py` 裡調整並降低查詢頻率。

## 貢獻 & 授權

- 歡迎 fork 與提交 Pull Request。  
- 本專案可自由參考使用，若有任何疑問或建議，歡迎在 Issue 中討論。  
