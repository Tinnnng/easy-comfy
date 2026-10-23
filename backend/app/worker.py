"""背景 worker：從佇列取任務送到 ComfyUI，追蹤進度直到完成。

每台 ComfyUI 配一個 worker，一次只跑一個任務（8GB 顯卡同時跑兩個很容易爆記憶體）。
多台 ComfyUI 時由 WorkerPool 管理，大家從同一個佇列搶任務，誰空下來誰接。
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
from .queue import JobQueue, SqliteQueue
from .templates import Template

log = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


class Worker:
    def __init__(self, db: Database, comfy: ComfyClient, bus: EventBus,
                 templates: dict[str, Template], outputs_dir: Path, job_timeout: float,
                 queue: JobQueue | None = None, name: str = "comfy-1",
                 primary: ComfyClient | None = None):
        self.db = db
        self.comfy = comfy
        self.bus = bus
        self.templates = templates
        self.outputs_dir = outputs_dir
        self.job_timeout = job_timeout
        self.queue = queue or SqliteQueue(db)
        self.name = name
        # 上傳的原圖只存在第一台 ComfyUI；其他台跑圖生圖前要先把圖複製過來
        self.primary = primary or comfy
        self.current_id: str | None = None
        self._cancel_requested = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever(), name=f"easycomfy-worker-{self.name}")
        self._task.add_done_callback(self._log_exit)

    def _log_exit(self, task: asyncio.Task[None]) -> None:
        # 背景任務的例外沒人 await 就會無聲無息消失，這裡一定要記下來
        if not task.cancelled() and task.exception():
            log.error("worker %s 意外結束", self.name, exc_info=task.exception())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def interrupt(self) -> None:
        """取消目前執行中的任務。"""
        self._cancel_requested = True
        await self.comfy.interrupt()

    # ---- 內部流程 ----

    def _emit(self, job: Job) -> None:
        self.bus.publish({"type": "job", "job": job.model_dump(mode="json")})

    async def _run_forever(self) -> None:
        while True:
            try:
                job_id = await self.queue.pop(timeout=5)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 例如 Redis 暫時連不上；redis 的例外不是 OSError
                # 等一下再試，不要讓 worker 整個掛掉
                log.warning("worker %s 讀不到佇列：%r", self.name, e)
                await asyncio.sleep(3)
                continue
            if job_id is None:
                continue
            job = self.db.claim(job_id, self.name)
            if job is not None:
                await self._process(job)


    async def _process(self, job: Job) -> None:
        self.current_id = job.id
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
            self.current_id = None
        self._emit(job)

    async def _execute(self, job: Job) -> list[str]:
        template = self.templates.get(job.template_id)
        if template is None:
            raise ComfyError(f"模板 {job.template_id} 已不存在")
        params = await self._sync_inputs(template, job.params)
        workflow = template.build_workflow(params)

        prompt_id: str | None = None
        failures = 0
        while True:
            try:
                async with self.comfy.connect_events() as ws:
                    if prompt_id is None:
                        prompt_id = await self.comfy.queue_prompt(workflow)
                        self.db.update(job.id, prompt_id=prompt_id)
                    elif await self._finished_while_away(prompt_id):
                        break
                    failures = 0
                    if await self._listen(job, ws, prompt_id):
                        break
                    # 事件串流自己結束了：當作斷線處理
            except (OSError, httpx.HTTPError, websockets.WebSocketException):
                if prompt_id is None:
                    raise  # 還沒送出就連不上，直接失敗
            failures += 1
            if failures > self.RECONNECT_ATTEMPTS:
                raise ComfyError("和 ComfyUI 的連線中斷，重試多次仍連不上")
            log.warning("任務 %s 與 ComfyUI 的連線中斷，第 %d 次重連", job.id, failures)
            await asyncio.sleep(self.reconnect_delay(failures))

        return await self._save_outputs(job.id, prompt_id)

    RECONNECT_ATTEMPTS = 5

    @staticmethod
    def reconnect_delay(failures: int) -> float:
        return min(2 ** failures, 10)

    async def _finished_while_away(self, prompt_id: str) -> bool:
        """重新連上後先問 ComfyUI：斷線期間任務是不是已經跑完、失敗，或因為重啟而遺失。"""
        state = await self.comfy.prompt_state(prompt_id)
        if state == "error":
            raise ComfyError("任務在連線中斷期間執行失敗")
        if state == "missing":
            raise ComfyError("ComfyUI 重新啟動過，這個任務已經遺失")
        return state == "done"

    # 這麼久沒收到任何事件，就改用 HTTP 問 ComfyUI 任務的狀態（事件可能在網路上遺失了）
    IDLE_CHECK_SECONDS = 30.0

    async def _listen(self, job: Job, ws: Any, prompt_id: str) -> bool:
        """處理 ComfyUI 事件，任務完成時回傳 True；連線中途關閉回傳 False。"""
        while True:
            try:
                async with asyncio.timeout(self.IDLE_CHECK_SECONDS):
                    event = await self.comfy.next_event(ws)
            except TimeoutError:
                if await self._finished_while_away(prompt_id):
                    log.warning("任務 %s 的完成事件沒收到，靠輪詢補上", job.id)
                    return True
                continue
            except websockets.ConnectionClosed:
                return False
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
                self._emit(self.db.update(job.id, progress=data["value"] / data["max"],
                                          total_steps=data["max"]))
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
                return True

    async def _sync_inputs(self, template: Template, params: dict[str, Any]) -> dict[str, Any]:
        """圖片欄位指向的檔案只上傳到第一台 ComfyUI；這台沒有的話先複製過來。"""
        if self.comfy is self.primary:
            return params
        params = dict(params)
        for f in template.fields:
            name = params.get(f.key)
            if f.type != "image" or not name:
                continue
            if (await self.comfy.view_input(name)).status_code == 200:
                continue
            source = await self.primary.view_input(name)
            if source.status_code != 200:
                raise ComfyError("找不到上傳的原圖，請重新上傳")
            content_type = source.headers.get("content-type", "image/png")
            params[f.key] = await self.comfy.upload_image(name, source.content, content_type)
        return params

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


class WorkerPool:
    """一台 ComfyUI 一個 worker，共用同一個佇列。"""

    def __init__(self, workers: list[Worker], db: Database, bus: EventBus):
        self.workers = workers
        self.db = db
        self.bus = bus

    def start(self) -> None:
        for w in self.workers:
            w.start()

    async def stop(self) -> None:
        for w in self.workers:
            await w.stop()

    async def cancel(self, job_id: str) -> bool:
        """取消任務。queued 直接標記；running 則請負責的那台 ComfyUI 中斷。回傳是否有效。"""
        job = self.db.get(job_id)
        if job is None:
            return False
        if job.status == JobStatus.queued:
            job = self.db.update(job_id, status=JobStatus.cancelled, finished_at=utcnow())
            self.bus.publish({"type": "job", "job": job.model_dump(mode="json")})
            return True
        for w in self.workers:
            if job.status == JobStatus.running and w.current_id == job_id:
                await w.interrupt()
                return True
        return False


def _friendly_error(e: Exception) -> str:
    if isinstance(e, ComfyError):
        return str(e)
    if isinstance(e, (OSError, httpx.ConnectError)):
        return "連不上 ComfyUI，請確認它已經啟動"
    return f"與 ComfyUI 通訊失敗：{e}"
