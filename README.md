# EasyComfy

讓新手不用碰節點圖也能使用 ComfyUI：選一個模板、填幾個白話欄位，按下「生成」就好。
後端負責排隊、送出、追蹤進度和保存結果。

> 🚧 開發中。規劃與進度請見 [docs/PLAN.md](docs/PLAN.md)。

## 功能

- **模板化工作流**：把 ComfyUI 匯出的 API JSON 搭配一份 YAML，就能變成新手表單
- **送單前檢查**：比對 ComfyUI `/object_info`，缺模型或缺節點時直接告訴使用者，不必等排隊排到才失敗
- **持久化任務佇列**：任務存在 SQLite，服務重啟後還在；8GB 顯卡一次只跑一個任務
- **即時顯影**：解析 ComfyUI WebSocket 的二進位預覽影格，取樣的每一步都推到前端，畫面從模糊慢慢變清晰
- **看片燈箱介面**：生成紀錄排成底片印樣，可以用放大鏡檢視、用鍵盤切換、下載或帶回設定；支援深色模式與手機
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
