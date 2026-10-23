"""ComfyUI HTTP / WebSocket API 的薄封裝。

用到的端點：
  POST /prompt            送出 workflow，回傳 prompt_id
  WS   /ws?clientId=...   執行進度（progress、executing、execution_error...）
  GET  /history/{id}      執行結果（輸出圖片的檔名）
  GET  /view              下載圖片
  GET  /object_info       每種節點可用的選項（模型清單、取樣器...）
  POST /interrupt         中斷目前執行
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str, client_id: str):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.http = httpx.AsyncClient(base_url=self.base_url, timeout=30)
        self._object_info: dict[str, Any] | None = None

    async def close(self) -> None:
        await self.http.aclose()

    async def ping(self) -> dict[str, Any]:
        r = await self.http.get("/system_stats", timeout=3)
        r.raise_for_status()
        return r.json()

    async def object_info(self, refresh: bool = False) -> dict[str, Any]:
        if self._object_info is None or refresh:
            r = await self.http.get("/object_info")
            r.raise_for_status()
            self._object_info = r.json()
        return self._object_info

    async def validate(self, workflow: dict[str, Any]) -> list[str]:
        """檢查 workflow 用到的節點和下拉選項（模型檔、取樣器...）在這台 ComfyUI 上是否存在。

        回傳問題清單，空清單代表沒問題。模型清單會先用快取，有缺時再重新抓一次，
        這樣使用者剛放進新模型也不用重啟服務。
        """
        problems = self._find_problems(workflow, await self.object_info())
        if problems:
            problems = self._find_problems(workflow, await self.object_info(refresh=True))
        return problems

    @staticmethod
    def _find_problems(workflow: dict[str, Any], info: dict[str, Any]) -> list[str]:
        problems = []
        for node_id, node in workflow.items():
            cls = node["class_type"]
            if cls not in info:
                problems.append(f"ComfyUI 缺少節點 {cls}（可能要安裝自訂節點）")
                continue
            spec = info[cls].get("input", {})
            inputs = spec.get("required", {}) | spec.get("optional", {})
            for name, value in node["inputs"].items():
                choices = inputs.get(name, [None])[0]
                if isinstance(choices, list) and isinstance(value, str) and value not in choices:
                    problems.append(f"節點 {node_id}（{cls}）的 {name} 找不到：{value}")
        return problems

    async def queue_prompt(self, workflow: dict[str, Any]) -> str:
        r = await self.http.post("/prompt", json={"prompt": workflow, "client_id": self.client_id})
        if r.status_code != 200:
            raise ComfyError(f"ComfyUI 拒絕這個 workflow：{r.text[:500]}")
        return r.json()["prompt_id"]

    async def interrupt(self) -> None:
        await self.http.post("/interrupt")

    def connect_events(self) -> websockets.connect:
        """開啟事件 WebSocket。要在 queue_prompt 之前連上，才不會漏掉開頭的事件。"""
        ws_url = self.base_url.replace("http", "ws", 1) + f"/ws?clientId={self.client_id}"
        return websockets.connect(ws_url, max_size=None)

    @staticmethod
    async def iter_events(ws: Any) -> AsyncIterator[dict[str, Any]]:
        """產出 JSON 事件。取樣中的預覽圖（二進位訊息）轉成 type="preview" 的事件。"""
        async for message in ws:
            if isinstance(message, str):
                yield json.loads(message)
            elif preview := parse_preview(message):
                yield preview

    async def output_images(self, prompt_id: str) -> list[dict[str, str]]:
        r = await self.http.get(f"/history/{prompt_id}")
        r.raise_for_status()
        entry = r.json().get(prompt_id)
        if not entry:
            raise ComfyError("ComfyUI 沒有這個任務的執行紀錄")
        images = []
        for output in entry.get("outputs", {}).values():
            images += [img for img in output.get("images", []) if img.get("type") == "output"]
        return images

    async def download(self, image: dict[str, str]) -> bytes:
        params = {k: image.get(k, "") for k in ("filename", "subfolder", "type")}
        r = await self.http.get("/view", params=params)
        r.raise_for_status()
        return r.content


# ComfyUI 二進位訊息的前 4 個位元組是事件類型
PREVIEW_IMAGE = 1                  # 接著 4 位元組圖片格式（1=JPEG、2=PNG），然後是圖片
PREVIEW_IMAGE_WITH_METADATA = 4    # 接著 4 位元組 JSON 長度、JSON（含 prompt_id），然後是圖片


def parse_preview(message: bytes) -> dict[str, Any] | None:
    if len(message) < 8:
        return None
    event = int.from_bytes(message[:4], "big")
    if event == PREVIEW_IMAGE:
        mime = "image/png" if int.from_bytes(message[4:8], "big") == 2 else "image/jpeg"
        return {"type": "preview", "data": {"mime": mime, "image": message[8:]}}
    if event == PREVIEW_IMAGE_WITH_METADATA:
        size = int.from_bytes(message[4:8], "big")
        try:
            meta = json.loads(message[8:8 + size])
        except ValueError:
            return None
        return {"type": "preview", "data": {"mime": meta.get("image_type", "image/jpeg"),
                                            "image": message[8 + size:],
                                            "prompt_id": meta.get("prompt_id")}}
    return None
