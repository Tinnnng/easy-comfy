from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .comfy_client import ComfyClient, ComfyError
from .config import Settings, settings as default_settings
from .db import Database, Job, JobStatus
from .events import EventBus
from .templates import IMAGE_NAME, MAX_SEED, ParamError, load_templates
from .translate import TranslateError, Translator, create_translator, translate_fields
from .queue import RedisQueue, create_queue
from .worker import Worker, WorkerPool

# 執行中的任務不能直接刪
DELETABLE = {JobStatus.queued, JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}

UPLOAD_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("easycomfy")


class JobCreate(BaseModel):
    template_id: str
    params: dict[str, Any] = {}
    # 一次生成幾張；8GB 顯卡一張一張排隊跑，所以上限放小一點
    count: int = Field(default=1, ge=1, le=4)


class ClearJobs(BaseModel):
    statuses: list[JobStatus]


def create_app(settings: Settings = default_settings, translator: Translator | None = None) -> FastAPI:
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    templates = load_templates(settings.templates_dir)
    db = Database(settings.db_url)
    bus = EventBus()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init()
        if n := db.fail_interrupted():
            log.info("將 %d 個上次中斷的任務標記為失敗", n)
        session = uuid.uuid4().hex[:8]
        comfies = [ComfyClient(url, client_id=f"easycomfy-{session}-{i}")
                   for i, url in enumerate(settings.all_comfy_urls, start=1)]
        queue = create_queue(settings.queue_backend, db, settings.redis_url)
        if isinstance(queue, RedisQueue):
            await queue.resync(db.queued_ids())
        workers = [Worker(db, c, bus, templates, settings.outputs_dir, settings.job_timeout,
                          queue=queue, name=f"comfy-{i}", primary=comfies[0])
                   for i, c in enumerate(comfies, start=1)]
        pool = WorkerPool(workers, db, bus)
        # 第一台負責送單前檢查與接收上傳
        app.state.comfy, app.state.comfies, app.state.queue, app.state.pool = comfies[0], comfies, queue, pool
        translate_http = httpx.AsyncClient(timeout=15)
        app.state.translator = translator or create_translator(
            translate_http, settings.google_api_key, settings.mymemory_email)
        if settings.start_worker:
            pool.start()
        log.info("已載入模板：%s；ComfyUI：%s；佇列：%s；翻譯：%s", ", ".join(templates),
                 ", ".join(settings.all_comfy_urls), queue.name, app.state.translator.name)
        yield
        await pool.stop()
        await queue.close()
        for c in comfies:
            await c.close()
        await translate_http.aclose()

    app = FastAPI(title="EasyComfy", lifespan=lifespan)
    app.mount("/outputs", StaticFiles(directory=settings.outputs_dir), name="outputs")

    def with_position(job: Job) -> dict[str, Any]:
        position = db.queue_position(job) if job.status == "queued" else None
        return job.model_dump(mode="json") | {"queue_position": position}

    async def instance_status(comfy: ComfyClient, worker: Worker) -> dict[str, Any]:
        base = {"name": worker.name, "url": comfy.base_url, "job_id": worker.current_id}
        try:
            stats = await comfy.ping()
        except (httpx.HTTPError, OSError):
            return base | {"comfy": False, "device": None, "vram_total": None, "vram_free": None}
        device = (stats.get("devices") or [{}])[0]
        return base | {"comfy": True, "device": device.get("name"),
                       "vram_total": device.get("vram_total"), "vram_free": device.get("vram_free")}

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """頂層欄位是第一台 ComfyUI 的狀態；instances 列出每一台。"""
        instances = await asyncio.gather(*(instance_status(c, w) for c, w in
                                           zip(app.state.comfies, app.state.pool.workers)))
        primary = {k: instances[0][k] for k in ("comfy", "device", "vram_total", "vram_free")}
        return {"ok": True, **primary, "queue": app.state.queue.name, "instances": instances}

    @app.get("/api/templates")
    def list_templates() -> list[dict[str, Any]]:
        return [t.public() for t in templates.values()]

    @app.post("/api/jobs", status_code=201)
    async def create_jobs(body: JobCreate) -> list[dict[str, Any]]:
        template = templates.get(body.template_id)
        if template is None:
            raise HTTPException(404, "找不到這個模板")
        try:
            resolved = template.resolve_params(body.params)
        except ParamError as e:
            raise HTTPException(422, {"field": e.field, "message": e.message}) from None

        # 模型只看得懂英文，中文描述先翻譯；失敗就明講，不偷偷把中文送進去
        try:
            resolved, sources = await translate_fields(template, resolved, app.state.translator)
        except TranslateError as e:
            raise HTTPException(503, f"自動翻譯失敗（{e}），請改用英文描述或稍後再試") from None

        # 送單前先確認模型、節點都在，免得排隊排了半天才失敗
        try:
            problems = await app.state.comfy.validate(template.build_workflow(resolved))
        except (httpx.HTTPError, OSError):
            raise HTTPException(503, "連不上 ComfyUI，請確認它已經啟動") from None
        if problems:
            raise HTTPException(409, {"message": "ComfyUI 缺少這個模板需要的東西", "problems": problems})

        # 批次生成：每張的種子依序加 1，之後每一張都能單獨重現
        seed_key = next((f.key for f in template.fields if f.type == "seed"), None)
        created = []
        for i in range(body.count):
            params = dict(resolved)
            if seed_key and i:
                params[seed_key] = (resolved[seed_key] + i) % (MAX_SEED + 1)
            created.append(with_position(db.add(Job(template_id=template.id, params=params, sources=sources))))
        for out in created:
            await app.state.queue.push(out["id"])
        for out in created:
            bus.publish({"type": "job", "job": out})
        return created

    @app.post("/api/uploads", status_code=201)
    async def upload(file: UploadFile) -> dict[str, str]:
        """圖生圖的原圖：直接轉存到 ComfyUI 的 input 資料夾。"""
        ext = UPLOAD_TYPES.get(file.content_type or "")
        if ext is None:
            raise HTTPException(415, "只支援 PNG、JPG 或 WebP 圖片")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "圖片太大了，請小於 20 MB")
        try:
            name = await app.state.comfy.upload_image(f"easycomfy_{uuid.uuid4().hex[:12]}{ext}",
                                                      data, file.content_type)
        except (httpx.HTTPError, OSError):
            raise HTTPException(503, "連不上 ComfyUI，請確認它已經啟動") from None
        except ComfyError as e:
            raise HTTPException(502, str(e)) from None
        return {"name": name, "url": f"/api/inputs/{name}"}

    @app.post("/api/jobs/{job_id}/as-input", status_code=201)
    async def job_as_input(job_id: str) -> dict[str, str]:
        """把生成好的圖送進 ComfyUI 的 input 資料夾，讓它能直接拿去放大或圖生圖。"""
        job = db.get(job_id)
        if job is None or not job.images:
            raise HTTPException(404, "這個任務沒有圖片")
        path = settings.outputs_dir / job.images[0]
        if not path.exists():
            raise HTTPException(404, "圖片檔案已經不在了")
        try:
            name = await app.state.comfy.upload_image(f"easycomfy_{uuid.uuid4().hex[:12]}{path.suffix}",
                                                      path.read_bytes(), "image/png")
        except (httpx.HTTPError, OSError):
            raise HTTPException(503, "連不上 ComfyUI，請確認它已經啟動") from None
        except ComfyError as e:
            raise HTTPException(502, str(e)) from None
        return {"name": name, "url": f"/api/inputs/{name}"}

    @app.get("/api/inputs/{name}")
    async def input_image(name: str) -> Response:
        """顯示之前上傳的原圖（從 ComfyUI 轉過來）。"""
        if not IMAGE_NAME.match(name):
            raise HTTPException(404, "找不到這張圖")
        try:
            r = await app.state.comfy.view_input(name)
        except (httpx.HTTPError, OSError):
            raise HTTPException(503, "連不上 ComfyUI") from None
        if r.status_code != 200:
            raise HTTPException(404, "找不到這張圖")
        return Response(r.content, media_type=r.headers.get("content-type", "image/png"),
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/jobs")
    def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
        return [with_position(j) for j in db.list(min(limit, 200))]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = db.get(job_id)
        if job is None:
            raise HTTPException(404, "找不到這個任務")
        return with_position(job)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, bool]:
        if db.get(job_id) is None:
            raise HTTPException(404, "找不到這個任務")
        if not await app.state.pool.cancel(job_id):
            raise HTTPException(409, "任務已結束，無法取消")
        return {"ok": True}

    def remove(job_ids: list[str], statuses: set[JobStatus]) -> list[str]:
        """刪除任務與它的輸出圖片，並通知所有前端。"""
        deleted = db.delete(job_ids, statuses)
        for job in deleted:
            for name in job.images:
                (settings.outputs_dir / name).unlink(missing_ok=True)
        ids = [job.id for job in deleted]
        if ids:
            bus.publish({"type": "deleted", "job_ids": ids})
        return ids

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str) -> dict[str, list[str]]:
        job = db.get(job_id)
        if job is None:
            raise HTTPException(404, "找不到這個任務")
        # 正在生成的任務要先取消，免得 ComfyUI 還在畫、紀錄卻不見了
        ids = remove([job_id], DELETABLE)
        if not ids:
            raise HTTPException(409, "這張正在生成，請先取消再刪除")
        return {"deleted": ids}

    @app.post("/api/jobs/clear")
    async def clear_jobs(body: ClearJobs) -> dict[str, list[str]]:
        """一次清掉某些狀態的紀錄，例如所有失敗和已取消的任務。"""
        statuses = set(body.statuses) & DELETABLE
        return {"deleted": remove(db.ids_with_status(statuses), statuses)}

    @app.websocket("/api/ws")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        async with bus.subscribe() as queue:
            try:
                while True:
                    await ws.send_json(await queue.get())
            except WebSocketDisconnect:
                pass

    return app


app = create_app()
