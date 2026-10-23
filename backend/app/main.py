from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .comfy_client import ComfyClient
from .config import Settings, settings as default_settings
from .db import Database, Job
from .events import EventBus
from .templates import ParamError, load_templates
from .worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("easycomfy")


class JobCreate(BaseModel):
    template_id: str
    params: dict[str, Any] = {}


def create_app(settings: Settings = default_settings) -> FastAPI:
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    templates = load_templates(settings.templates_dir)
    db = Database(settings.db_url)
    bus = EventBus()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init()
        if n := db.fail_interrupted():
            log.info("將 %d 個上次中斷的任務標記為失敗", n)
        comfy = ComfyClient(settings.comfy_url, client_id=f"easycomfy-{uuid.uuid4().hex[:8]}")
        worker = Worker(db, comfy, bus, templates, settings.outputs_dir, settings.job_timeout)
        app.state.comfy, app.state.worker = comfy, worker
        if settings.start_worker:
            worker.start()
        log.info("已載入模板：%s；ComfyUI：%s", ", ".join(templates), settings.comfy_url)
        yield
        await worker.stop()
        await comfy.close()

    app = FastAPI(title="EasyComfy", lifespan=lifespan)
    app.mount("/outputs", StaticFiles(directory=settings.outputs_dir), name="outputs")

    def with_position(job: Job) -> dict[str, Any]:
        position = db.queue_position(job) if job.status == "queued" else None
        return job.model_dump(mode="json") | {"queue_position": position}

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        try:
            stats = await app.state.comfy.ping()
            devices = stats.get("devices", [])
            return {"ok": True, "comfy": True, "device": devices[0]["name"] if devices else None}
        except (httpx.HTTPError, OSError):
            return {"ok": True, "comfy": False, "device": None}

    @app.get("/api/templates")
    def list_templates() -> list[dict[str, Any]]:
        return [t.public() for t in templates.values()]

    @app.post("/api/jobs", status_code=201)
    async def create_job(body: JobCreate) -> dict[str, Any]:
        template = templates.get(body.template_id)
        if template is None:
            raise HTTPException(404, "找不到這個模板")
        try:
            resolved = template.resolve_params(body.params)
        except ParamError as e:
            raise HTTPException(422, {"field": e.field, "message": e.message}) from None

        # 送單前先確認模型、節點都在，免得排隊排了半天才失敗
        try:
            problems = await app.state.comfy.validate(template.build_workflow(resolved))
        except (httpx.HTTPError, OSError):
            raise HTTPException(503, "連不上 ComfyUI，請確認它已經啟動") from None
        if problems:
            raise HTTPException(409, {"message": "ComfyUI 缺少這個模板需要的東西", "problems": problems})

        job = db.add(Job(template_id=template.id, params=resolved))
        app.state.worker.notify()
        out = with_position(job)
        bus.publish({"type": "job", "job": out})
        return out

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
        if not await app.state.worker.cancel(job_id):
            raise HTTPException(409, "任務已結束，無法取消")
        return {"ok": True}

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
