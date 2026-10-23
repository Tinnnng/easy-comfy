# EasyComfy

讓新手不用碰節點圖也能使用 ComfyUI：選一個模板、填幾個白話欄位，按下「生成」就好。
後端負責排隊、送出、追蹤進度和保存結果。

> 🚧 開發中。規劃與進度請見 [docs/PLAN.md](docs/PLAN.md)。

## 功能

- **模板化工作流**：把 ComfyUI 匯出的 API JSON 搭配一份 YAML，就能變成新手表單
- **中文描述自動翻譯**：Flux 的文字編碼器幾乎只看得懂英文，含中文的描述會先翻成英文再送出；紀錄同時保留原文和實際送出的英文，翻譯失敗時會明確告知，不會把中文直接送進模型
- **送單前檢查**：比對 ComfyUI `/object_info`，缺模型或缺節點時直接告訴使用者，不必等排隊排到才失敗
- **持久化任務佇列**：任務存在 SQLite，服務重啟後還在；8GB 顯卡一次只跑一個任務
- **即時去雜訊預覽**：解析 ComfyUI WebSocket 的二進位預覽影格，每一步都推到前端，由 WebGL shader 呈現從熱圖雜訊溶解成畫面的過程
- **取樣遙測**：步數、經過時間、σ 雜訊強度排程，以及從 ComfyUI 讀取的即時 VRAM 用量
- **WebGL 介面特效**：domain-warped fbm 雜訊場背景（會隨生成脈動、隨游標擾動，限制在 30fps 以免和 ComfyUI 搶顯示卡）、卡片 3D 傾斜反光、View Transitions 縮圖變形；全部支援「減少動態」設定，也能在不支援 WebGL 時退回一般圖片
- **可重現**：隨機種子會寫回任務紀錄，可以一鍵套用同一組設定

## 架構

```
React (Vite) ──HTTP/WS──▶ FastAPI ──▶ SQLite（任務＝佇列）
                             │
                           Worker ──HTTP/WS──▶ ComfyUI
```

## 本機執行

需要：Python 3.11+、Node.js 20+，以及已啟動的 ComfyUI（預設 `http://127.0.0.1:8188`）。
內建的 Flux schnell 模板需要 [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) 節點和以下模型：

| 類型 | 檔案 |
|---|---|
| diffusion_models | `flux1-schnell-Q4_0.gguf` |
| clip | `t5xxl_fp8_e4m3fn.safetensors`、`clip_l.safetensors` |
| vae | `ae.safetensors` |

```bash
# 後端
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux：source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（另開一個終端機）
cd frontend
npm install
npm run dev
```

打開 http://localhost:5173 即可使用，API 文件在 http://localhost:8000/docs 。

ComfyUI 不在預設位址時，在 `backend/.env` 設定：

```
EASYCOMFY_COMFY_URL=http://127.0.0.1:8188
```

### 中文翻譯

預設使用免費的 [MyMemory](https://mymemory.translated.net/doc/spec.php) 翻譯 API，不需要註冊（匿名每天約 5,000 字元）。也可以在 `backend/.env` 改用其他設定：

```
# 改用 Google Cloud Translation（需要已啟用帳單的 Google Cloud 專案）
EASYCOMFY_GOOGLE_API_KEY=你的金鑰

# 或者提供 email 給 MyMemory，把每日額度提高到約 50,000 字元
EASYCOMFY_MYMEMORY_EMAIL=you@example.com
```

同一句話的翻譯會快取在記憶體裡，重新生成不會重複耗用額度。

## 新增模板

1. 在 ComfyUI 組好流程，選單 **Workflow → Export (API)** 匯出 JSON。
2. 建立 `backend/templates/<模板id>/`，放入 `workflow_api.json` 和 `template.yaml`。
3. 在 `template.yaml` 用 `節點ID.輸入名稱` 指定每個欄位要寫到哪裡，可參考 `flux_schnell_t2i`。
4. 重啟後端，跑 `pytest` 確認模板能正確載入。

## 測試

```bash
cd backend
pytest
```
