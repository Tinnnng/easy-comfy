"""用假的 ComfyUI（假 WebSocket + httpx MockTransport）跑完整的 worker 流程。"""

import asyncio
import json
from pathlib import Path

import httpx

from app.comfy_client import ComfyClient
from app.db import Database, Job, JobStatus
from app.events import EventBus
from app.templates import load_templates
from app.worker import Worker

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
PROMPT_ID = "p1"


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = messages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for m in self.messages:
            yield m


class FakeComfy(ComfyClient):
    def __init__(self, messages):
        super().__init__("http://comfy.test", client_id="test")
        self.messages = messages
        self.http = httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(self.handle))

    def connect_events(self):
        return FakeWebSocket(self.messages)

    async def queue_prompt(self, workflow):
        self.workflow = workflow
        return PROMPT_ID

    async def interrupt(self):
        pass

    @staticmethod
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/history/{PROMPT_ID}":
            image = {"filename": "easycomfy_00001_.png", "subfolder": "", "type": "output"}
            return httpx.Response(200, json={PROMPT_ID: {"outputs": {"9": {"images": [image]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=b"PNG")
        return httpx.Response(404)


def event(type_, **data):
    return json.dumps({"type": type_, "data": {"prompt_id": PROMPT_ID, **data}})


def run_job(tmp_path, messages):
    async def go():
        db = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
        db.init()
        templates = load_templates(TEMPLATES_DIR)
        params = templates["flux_schnell_t2i"].resolve_params({"prompt": "a cat", "seed": 7})
        job = db.add(Job(template_id="flux_schnell_t2i", params=params))
        bus = EventBus()
        async with bus.subscribe() as queue:
            worker = Worker(db, FakeComfy(messages), bus, templates, tmp_path / "out", job_timeout=5)
            await worker._process(job)
            events = [queue.get_nowait() for _ in range(queue.qsize())]
        return db.get(job.id), events

    return asyncio.run(go())


def test_successful_job_saves_image_and_streams_preview(tmp_path):
    preview = (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"JPEG"
    job, events = run_job(tmp_path, [
        event("progress", value=2, max=4),
        preview,
        event("executing", node=None),
    ])
    assert job.status == JobStatus.succeeded
    assert job.images == [f"{job.id}_0.png"]
    assert (tmp_path / "out" / job.images[0]).read_bytes() == b"PNG"
    assert any(e["type"] == "preview" and e["image"].startswith("data:image/jpeg;base64,") for e in events)
    assert any(e["type"] == "job" and e["job"]["progress"] == 0.5 for e in events)


def test_comfy_execution_error_marks_job_failed(tmp_path):
    job, _ = run_job(tmp_path, [
        event("execution_error", node_type="KSampler", exception_message="CUDA out of memory"),
    ])
    assert job.status == JobStatus.failed
    assert "CUDA out of memory" in job.error


def test_other_clients_events_are_ignored(tmp_path):
    other = json.dumps({"type": "execution_error", "data": {"prompt_id": "someone-else"}})
    job, _ = run_job(tmp_path, [other, event("execution_success")])
    assert job.status == JobStatus.succeeded
