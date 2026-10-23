"""背景 worker：一次從佇列取一個任務送到 ComfyUI，追蹤進度直到完成。

8GB 顯卡同時只適合跑一個任務，所以這裡刻意只有一個 worker 協程。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

import httpx
import websockets

from .comfy_client import ComfyClient, ComfyError
from .db import Database, Job, JobStatus, utcnow
from .events import EventBus
from .templates import Template

log = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


class Worker:
    def __init__(self, db: Database, comfy: ComfyClient, bus: EventBus,
                 templates: dict[str, Template], outputs_dir: Path, job_timeout: float):
        self.db = db
        self.comfy = comfy
        self.bus = bus
        self.templates = templates
        self.outputs_dir = outputs_dir
        self.job_timeout = job_timeout
        self._wake = asyncio.Event()
        self._current_id: str | None = None
        self._cancel_requested = False
        self._task: asyncio.Task[None] | None = None

    # ---- 給 API 呼叫 ----

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever(), name="easycomfy-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def notify(self) -> None:
        """有新任務進佇列時呼叫，讓 worker 不用等輪詢。"""
        self._wake.set()

    async def cancel(self, job_id: str) -> bool:
        """取消任務。queued 直接標記；running 則請 ComfyUI 中斷。回傳是否有效。"""
        job = self.db.get(job_id)
        if job is None:
            return False
        if job.status == JobStatus.queued:
            self._emit(self.db.update(job_id, status=JobStatus.cancelled, finished_at=utcnow()))
            return True
        if job.status == JobStatus.running and job_id == self._current_id:
            self._cancel_requested = True
            await self.comfy.interrupt()
            return True
        return False

    # ---- 內部流程 ----

    def _emit(self, job: Job) -> None:
        self.bus.publish({"type": "job", "job": job.model_dump(mode="json")})

    async def _run_forever(self) -> None:
        while True:
            job = self.db.next_queued()
            if job is None:
                self._wake.clear()
                try:
                    # 保底每 5 秒檢查一次，避免漏掉喚醒訊號
                    await asyncio.wait_for(self._wake.wait(), timeout=5)
                except TimeoutError:
                    pass
                continue
            await self._process(job)

    async def _process(self, job: Job) -> None:
        self._current_id = job.id
        self._cancel_requested = False
        job = self.db.update(job.id, status=JobStatus.running, started_at=utcnow(), progress=0.0)
        self._emit(job)
        try:
            async with asyncio.timeout(self.job_timeout):
                images = await self._execute(job)
        except JobCancelled:
            job = self.db.update(job.id, status=JobStatus.cancelled, finished_at=utcnow())
        except TimeoutError:
            await self.comfy.interrupt()
            job = self.db.update(job.id, status=JobStatus.failed, finished_at=utcnow(),
                                 error=f"超過 {self.job_timeout:.0f} 秒仍未完成，已中斷")
        except (ComfyError, OSError, httpx.HTTPError, websockets.WebSocketException) as e:
            log.warning("任務 %s 失敗：%s", job.id, e)
            job = self.db.update(job.id, status=JobStatus.failed, finished_at=utcnow(),
                                 error=_friendly_error(e))
        except Exception as e:  # noqa: BLE001 — worker 不能因為單一任務掛掉
            log.exception("任務 %s 發生未預期錯誤", job.id)
            job = self.db.update(job.id, status=JobStatus.failed, finished_at=utcnow(),
                                 error=f"未預期的錯誤：{e}")
        else:
            job = self.db.update(job.id, status=JobStatus.succeeded, finished_at=utcnow(),
                                 progress=1.0, images=images)
        finally:
            self._current_id = None
        self._emit(job)

    async def _execute(self, job: Job) -> list[str]:
        template = self.templates.get(job.template_id)
        if template is None:
            raise ComfyError(f"模板 {job.template_id} 已不存在")
        workflow = template.build_workflow(job.params)

        async with self.comfy.connect_events() as ws:
            prompt_id = await self.comfy.queue_prompt(workflow)
            self.db.update(job.id, prompt_id=prompt_id)
            async for event in self.comfy.iter_events(ws):
                data: dict[str, Any] = event.get("data") or {}
                if data.get("prompt_id") not in (None, prompt_id):
                    continue  # 別人直接在 ComfyUI 介面送的任務
                kind = event.get("type")
                if kind == "preview":
                    # 預覽圖不進資料庫，只即時推給前端
                    image = base64.b64encode(data["image"]).decode()
                    self.bus.publish({"type": "preview", "job_id": job.id,
                                      "image": f"data:{data['mime']};base64,{image}"})
                elif kind == "progress" and data.get("max"):
                    self._emit(self.db.update(job.id, progress=data["value"] / data["max"]))
                elif kind == "execution_error":
                    raise ComfyError(f"{data.get('node_type', '節點')} 執行失敗："
                                     f"{data.get('exception_message', '').strip()}")
                elif kind == "execution_interrupted":
                    if self._cancel_requested:
                        raise JobCancelled
                    raise ComfyError("任務在 ComfyUI 被中斷")
                elif kind == "execution_success" or (
                        kind == "executing" and data.get("node") is None
                        and data.get("prompt_id") == prompt_id):
                    break

        return await self._save_outputs(job.id, prompt_id)

    async def _save_outputs(self, job_id: str, prompt_id: str) -> list[str]:
        images = await self.comfy.output_images(prompt_id)
        if not images:
            raise ComfyError("ComfyUI 沒有產生任何圖片")
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for i, image in enumerate(images):
            name = f"{job_id}_{i}{Path(image['filename']).suffix or '.png'}"
            (self.outputs_dir / name).write_bytes(await self.comfy.download(image))
            saved.append(name)
        return saved


def _friendly_error(e: Exception) -> str:
    if isinstance(e, ComfyError):
        return str(e)
    if isinstance(e, (OSError, httpx.ConnectError)):
        return "連不上 ComfyUI，請確認它已經啟動"
    return f"與 ComfyUI 通訊失敗：{e}"
