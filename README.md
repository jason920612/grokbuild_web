# grokbuild_web

一個 grok.com 風格的網頁介面，讓你在**終端機建立/開啟 [Grok CLI](https://github.com/xai-org)（grok build）對話後，在網頁（含手機）上繼續對話**。終端機的 TUI 與網頁透過 grok 內建的 **leader** 共享同一個活的 session，因此雙向即時鏡像；代理程式跳出的**權限/選項**也會在網頁上以按鈕呈現。

> ⚠️ 非官方專案，與 xAI 無任何關聯。這是社群為 Grok CLI 打造的第三方網頁客戶端，透過 CLI 公開的 ACP（Agent Client Protocol）介面運作。使用前你需要自行安裝並登入 Grok CLI。

## 需求

- 已安裝並登入的 **Grok CLI**（`grok`，本專案針對 v0.2.93 開發），預設在 `~/.grok/bin/`
- **Python 3.11+**
- 選用：`cloudflared`（外網存取，`--tunnel` 會自動下載）

## 功能

- grok.com 風格聊天介面，手機優先 RWD，串流回覆 / 思考 / 工具卡片 / 計畫
- 在網頁直接**開新對話**（選工作目錄與模型），也可續接終端機建立的 session
- 終端機 TUI 與網頁**同時即時鏡像**同一個 session（透過 leader）
- 選項/權限彈窗雙向同步，網頁上以按鈕作答
- 圖片與檔案上傳（＋ / 拖放 / 貼上），對話中內嵌顯示圖片、摺疊顯示文字檔
- 設定與用量面板：訂閱方案、額度用量、context 使用量、模型/推理力度切換、config.toml 開關、斜線指令
- Cloudflare Tunnel 外網存取，帶 token 的連結才可進（否則 403）

## 運作原理

```
終端機 grok TUI ─┐
                 ├─► grok leader（共享後端 / 同一 session）◄── grok agent stdio ◄─► FastAPI 橋接 ◄─WS─► 瀏覽器
手機/網頁 ───────┘
```

- 後端 spawn `grok agent --leader stdio`，透過 ACP（JSON-RPC over stdio）連到 leader。
- 歷史對話由 `~/.grok/sessions/<cwd>/<id>/updates.jsonl` 重播；即時事件來自 leader 廣播的 `session/update`——兩者格式相同，共用同一套渲染。
- `session/request_permission` 轉成網頁上的選項按鈕，選擇回傳給 agent。

## 安裝

```powershell
git clone https://github.com/jason920612/grokbuild_web.git
cd grokbuild_web
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 啟動

```powershell
python launcher.py            # 啟動 leader + 網頁，印出手機網址與 QR
```

然後：
1. （選用，要鏡像才需要）在另一個終端機開 `grok` TUI，開啟你要的對話。**TUI 必須是在 `~/.grok/config.toml` 加入 `[cli] use_leader = true` 之後才啟動的**（已由本專案設定）；設定前就開著的 TUI 不會同步，要重開。
2. 手機/電腦掃 QR 或開 `http://<你的IP>:8787/?key=<token>`，選擇 session 即可對話。

## 選項/權限彈窗

- 模型的 **ask-user-question 選單**（單選/多選）會同時出現在 TUI 與網頁；任一端作答即生效，另一端自動標示「已在其他裝置回答」。網頁上也可輸入自訂回答或跳過。
- **工具權限請求**只在 `permission_mode` 不是 `always-approve` 時出現（你目前是 always-approve，所以不會跳）；出現時同樣會轉成網頁按鈕。

參數：`--port 8787`、`--host 0.0.0.0`、`--no-leader`（網頁獨立運作，不與終端機共享）。

## 環境變數

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `GROK_HOME` | `~/.grok` | grok 資料目錄 |
| `GROK_EXE` | `~/.grok/bin/grok.exe` | grok 執行檔 |
| `GROKWEB_PORT` | `8787` | 網頁埠 |
| `GROKWEB_USE_LEADER` | `1` | 是否透過 leader 共享 session |

## 外網存取（Cloudflare Tunnel）

```powershell
python launcher.py --tunnel     # 非同內網也能連
```

- 自動偵測/下載 `cloudflared`，開一條 Cloudflare quick tunnel（免帳號），印出 `https://xxx.trycloudflare.com/?key=<token>` 的網址與 QR。
- **只有帶 token 的連結（掃 QR）能進**；任何人拿到公開網址但沒有 token 會直接被 **403** 擋下。掃一次後 token 存成 cookie，之後免帶。
- 門禁判斷：經隧道進來的流量帶 Cloudflare 標頭 → 需要 token；本機 loopback 直連（`127.0.0.1`，無 CF 標頭）→ 免 token，方便本機自己用。
- token 每次啟動隨機產生；要固定網址可設環境變數 `GROKWEB_TOKEN`。WebSocket 握手同樣驗證 token。

> 不加 `--tunnel` 就是原本的區網模式，但網址現在也帶 `?key=`，同網段其他人沒有連結一樣進不來。

## 圖片與檔案上傳（＋ / 拖放 / 貼上）

- 輸入列左側 **＋** 選檔（可多選；手機會提供相機/相簿）、把檔案**拖進對話區**、或直接在輸入框 **Ctrl+V 貼上截圖**。
- 附件先上傳到該對話工作目錄的 `.grokweb/uploads/`（agent 的檔案工具讀得到），送出時：
  - **圖片** → 以 ACP image block 直接送給模型（grok-4.5 有視覺，實測能正確描述圖片內容；注意 API 要求圖片至少 512 像素）。
  - **其他檔案** → 訊息附上檔案路徑註記，agent 會用 read_file 等工具讀取（任何大小/類型都可）。
- 送出前可按 ✕ 移除附件；圖片有縮圖預覽，聊天泡泡也會顯示附件。
- **對話中的檔案顯示**：訊息裡出現的檔案路徑會自動渲染 — 圖片內嵌顯示、文字檔（程式碼/JSON/log…）用**可摺疊**面板展開內容（>512KB 或二進位改為下載連結）。檔案服務限制在該對話工作目錄內（防目錄穿越）。

## 設定與用量面板（⚙）

點右上角 ⚙ 開啟設定面板：

- **用量與限制** — 方案（X Premium+ 等）、本期額度已用 %、on-demand 已用/上限、額度重置倒數；對話內另有上下文使用量條（黃線 = 自動壓縮門檻）。
- **此對話** — 切換模型、推理力度（low/medium/high，即時生效，TUI 同步）、always-approve 與活動狀態顯示。切到需要不同 agent 的模型（如 Composer）會被 grok 拒絕並提示開新 session。
- **全域設定** — config.toml 的權限模式、YOLO、leader 共享、自動更新等，顯示目前值、即改即存（已開啟的 TUI 需重啟套用）。
- **斜線指令** — 全部指令列表；輸入框打 `/` 也會出現自動完成選單，指令會被 CLI 攔截執行（如 `/compact`、`/always-approve on`）。

## API（Phase 1–2）

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| GET | `/api/sessions` | 列出所有 session |
| POST | `/api/sessions/new` | `{cwd, model?}` 建立新 session（在網頁開新對話） |
| GET | `/api/recent-dirs` | 最近用過的工作目錄（開新對話快選） |
| GET | `/api/sessions/{id}` | 單一 session 資訊 |
| GET | `/api/sessions/{id}/history` | 正規化歷史對話項目 |
| GET | `/api/status` | 訂閱方案、billing 用量、agent 版本、活 session 列表 |
| GET | `/api/sessions/{id}/settings` | 模型/力度/context 用量/指令列表/live 狀態 |
| POST | `/api/sessions/{id}/settings` | `{modelId}` 或 `{modeId}` 切換模型/力度 |
| POST | `/api/sessions/{id}/upload` | multipart 上傳附件（存入 cwd/.grokweb/uploads，上限 25MB） |
| GET | `/api/sessions/{id}/file?path=` | 服務工作目錄內的檔案（`&meta=1` 回傳資訊；限 cwd 內） |
| GET | `/api/config` | config.toml 可編輯項目與目前值 |
| PUT | `/api/config` | `{section,key,value}` 寫入 config.toml |
| WS | `/ws/{id}` | 即時對話：歷史 → 串流 → 送訊息/權限回覆/選項作答/取消 |

WebSocket 訊息格式見 `backend/main.py` 檔頭註解。

## 狀態

Phase 1：完整雙向 API + 手機聊天前端。後續：多 session 分頁、附件/圖片、認證、透過 grok WebSocket relay 對外連線。
