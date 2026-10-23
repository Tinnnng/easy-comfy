# EasyComfy 專案規劃

> 讓新手不用碰節點圖，也能用 ComfyUI 生圖的工作流排程服務。

## 1. 目標

| 對象 | 痛點 | EasyComfy 的解法 |
|---|---|---|
| 新手 | ComfyUI 節點圖看不懂、參數名詞太專業 | 選模板 → 填幾個白話欄位 → 生成 |
| 進階使用者 | 想把自己的流程分享給別人用 | 匯出 API JSON + 寫一份欄位對應 YAML，就變成模板 |
| 多人共用一張 GPU | 同時送單會互相卡住、失敗沒有紀錄 | 持久化任務佇列、進度即時推播、失敗原因記錄 |

**非目標（目前不做）**：取代 ComfyUI 的節點編輯器、帳號金流系統、雲端 GPU 叢集。

## 2. 硬體與環境前提

- GPU：RTX 4060 Ti 8GB，同一時間只跑 **1 個** 任務（worker 並行數 = 1）。
- ComfyUI：StabilityMatrix 安裝版，預設 `http://127.0.0.1:8188`，已有 ComfyUI-GGUF 節點。
- 目前模型：`flux1-schnell-Q4_0.gguf` + `t5xxl_fp8_e4m3fn` + `clip_l` + `ae`。
  - Flux schnell 只需 4 步、CFG 1.0，8GB 可跑 1024×1024。
  - 之後若要加 SD1.5 / SDXL 模板，只要新增模板資料夾即可。

## 3. 系統架構

```
┌──────────────┐  HTTP / WebSocket  ┌─────────────────────────────┐
│  前端 React   │ ─────────────────▶ │  後端 FastAPI                │
│  (Vite + TS) │ ◀───── 進度事件 ─── │  ├ 模板引擎（YAML → workflow）│
└──────────────┘                    │  ├ 任務 API                   │
                                    │  ├ 事件匯流排（推播進度）      │
                                    │  └ Worker（背景協程）          │
                                    └──────┬──────────────┬────────┘
                                           │              │
                                   SQLite（任務＝佇列）  ComfyUI
                                                      /prompt /ws /history /view
```

### 任務生命週期

```
queued ──▶ running ──▶ succeeded
   │           ├──────▶ failed      （ComfyUI 錯誤、連線中斷、模型缺少）
   └──▶ cancelled ◀─────┘            （running 時呼叫 ComfyUI /interrupt）
```

1. 前端 `POST /api/jobs`，帶 `template_id` 和欄位值。
2. 後端驗證欄位 → 套入模板 → **向 ComfyUI `/object_info` 檢查模型、取樣器等選項是否存在** → 寫入 SQLite（status=queued）→ 喚醒 worker。
3. Worker 依建立時間取出最舊的 queued 任務，送到 ComfyUI `/prompt`。
4. Worker 透過 ComfyUI WebSocket 收 `progress` / `executing` / `execution_error`，更新資料庫並經事件匯流排推給前端。
5. 完成後從 `/history/{prompt_id}` 找輸出圖，經 `/view` 下載存到 `data/outputs/`。
6. 服務重啟時，殘留的 running 任務標記為 failed（原因：服務重啟中斷），queued 任務繼續處理。

### 為什麼 MVP 用 SQLite 當佇列、不用 Redis？

- 單一 GPU、單一 worker，SQLite 足以承載，且任務**本來就要持久化**（歷史紀錄、重現）。
- Windows 上不用額外裝 Redis，`git clone` 後就能跑。
- 佇列存取包在 `queue.py` 介面後面，Phase 4 可以替換成 Redis 版本並做壓測比較（履歷素材）。

## 4. 模板格式（核心設計）

每個模板一個資料夾：

```
backend/templates/flux_schnell_t2i/
├── workflow_api.json   # 從 ComfyUI「Export (API)」匯出
└── template.yaml       # 欄位對應
```

```yaml
id: flux_schnell_t2i
name: 文生圖（Flux 快速）
fields:
  - key: prompt
    label: 畫面描述
    type: text
    required: true
    targets: ["4.text"]          # 節點 4 的 text 輸入
  - key: size
    label: 尺寸
    type: choice
    default: square
    options:
      - value: square
        label: 方形
        set: { "6.width": 1024, "6.height": 1024 }
  - key: seed
    type: seed                   # -1 = 隨機，實際用的值會寫回任務供重現
    targets: ["7.seed"]
```

欄位型別：`text`、`number`（min/max）、`choice`（一個選項可設定多個節點參數）、`seed`。
新手看到的是 `label`，進階參數用 `advanced: true` 收進「進階設定」。

## 5. API 設計

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/health` | 後端與 ComfyUI 連線狀態 |
| GET | `/api/templates` | 模板清單與欄位定義（前端用來動態產生表單） |
| POST | `/api/jobs` | 建立任務 |
| GET | `/api/jobs` | 任務歷史（新到舊） |
| GET | `/api/jobs/{id}` | 單一任務 |
| POST | `/api/jobs/{id}/cancel` | 取消任務 |
| WS | `/api/ws` | 任務事件推播 `{type, job}` |
| GET | `/outputs/*` | 生成圖片 |

## 6. 開發階段

### Phase 1：MVP（本次建立骨架）
- [x] Flux schnell 文生圖模板
- [x] 模板引擎 + 參數驗證 + seed 重現
- [x] SQLite 持久化佇列 + 單一 worker
- [x] ComfyUI 客戶端（送單、WebSocket 進度、取圖、中斷）
- [x] 前端：模板選擇、動態表單、即時進度、歷史圖庫、「用相同設定再生成」
- [x] 模板引擎單元測試
- [x] 即時預覽（ComfyUI 二進位預覽影格 → 前端顯影效果）
- [x] Worker 整合測試（假的 ComfyUI WebSocket + httpx MockTransport）
- [x] 前端「潛空間實驗室」視覺：WebGL 雜訊場與去雜訊顯影、取樣遙測與 VRAM、View Transitions、手機版

### Phase 2：穩定性
- [ ] ComfyUI 斷線自動重連、任務逾時
- [ ] 送單前模型檢查結果顯示在前端（缺哪個模型）
- [ ] GitHub Actions CI（pytest + 前端 build）

### Phase 3：新手體驗
- [x] 中文描述自動翻譯（MyMemory 免費 API，可改用 Google Cloud Translation）
- [ ] 提示詞改寫助理（把簡短描述擴寫成細節完整的 prompt）
- [ ] 風格預設（動漫／寫實／水彩 → prompt 片段 + LoRA）
- [ ] 圖生圖、高清放大模板（需上傳圖片：ComfyUI `/upload/image`）
- [ ] 任務優先權、批次生成

### Phase 4：工程亮點
- [ ] 佇列後端可切換 Redis，並做壓測比較（等待時間、吞吐量）
- [ ] 多台 ComfyUI 負載分配
- [ ] Docker Compose 一鍵啟動
- [ ] README：Demo GIF、架構圖、量測數據

## 7. 技術選型

| 部分 | 選擇 | 理由 |
|---|---|---|
| 後端 | FastAPI + SQLModel + httpx + websockets | 非同步、型別清楚、自動產生 API 文件（`/docs`） |
| 資料庫 | SQLite | 零設定，之後可換 PostgreSQL |
| 前端 | React + TypeScript + Vite | 業界主流，履歷上最通用 |
| 測試 | pytest | |
