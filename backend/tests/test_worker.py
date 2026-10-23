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
    def __init__(self, messages, owner=None):
        self.messages = messages
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for m in self.messages:
            # 送出完成事件之後，ComfyUI 的 history 才會有這個任務
            if self.owner and isinstance(m, str) and "execution_success" in m:
                self.owner.history_done = True
            yield m


class FakeComfy(ComfyClient):
    """connections：每次連線依序收到的事件；用完之後再連線就當作 ComfyUI 連不上。"""

    def __init__(self, connections, history_done=True, queued_ids=()):
        super().__init__("http://comfy.test", client_id="test")
        self.connections = list(connections)
        self.history_done = history_done
        self.queued_ids = list(queued_ids)
        self.http = httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(self.handle))

    def connect_events(self):
        if not self.connections:
            raise ConnectionRefusedError("ComfyUI is down")
        return FakeWebSocket(self.connections.pop(0), self)

    async def queue_prompt(self, workflow):
        self.workflow = workflow
        return PROMPT_ID

    async def interrupt(self):
        pass

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/history/{PROMPT_ID}":
            if not self.history_done:
                return httpx.Response(200, json={})
            image = {"filename": "easycomfy_00001_.png", "subfolder": "", "type": "output"}
            return httpx.Response(200, json={PROMPT_ID: {"outputs": {"9": {"images": [image]}}}})
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [[0, pid, {}] for pid in self.queued_ids],
                                             "queue_pending": []})
        if request.url.path == "/view":
            return httpx.Response(200, content=b"PNG")
        return httpx.Response(404)


def event(type_, **data):
    return json.dumps({"type": type_, "data": {"prompt_id": PROMPT_ID, **data}})


def run_job(tmp_path, messages, comfy=None):
    async def go():
        db = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
        db.init()
        templates = load_templates(TEMPLATES_DIR)
        params = templates["flux_schnell_t2i"].resolve_params({"prompt": "a cat", "seed": 7})
        job = db.add(Job(template_id="flux_schnell_t2i", params=params))
        bus = EventBus()
        async with bus.subscribe() as queue:
            worker = Worker(db, comfy or FakeComfy([messages]), bus, templates, tmp_path / "out", job_timeout=5)
            worker.reconnect_delay = lambda failures: 0
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
    assert any(e["type"] == "job" and e["job"]["progress"] == 0.5 and e["job"]["total_steps"] == 4 for e in events)


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


def test_reconnects_and_finds_job_finished_while_away(tmp_path):
    # 第一條連線只收到一半進度就斷了；重新連上時 ComfyUI 已經跑完
    comfy = FakeComfy([[event("progress", value=1, max=4)], []])
    job, _ = run_job(tmp_path, None, comfy)
    assert job.status == JobStatus.succeeded
    assert job.images


def test_reconnects_and_keeps_listening_when_still_running(tmp_path):
    # 重新連上時任務還在 ComfyUI 佇列裡，就繼續聽事件直到完成
    comfy = FakeComfy([[event("progress", value=1, max=4)], [event("execution_success")]],
                      history_done=False, queued_ids=[PROMPT_ID])
    job, _ = run_job(tmp_path, None, comfy)
    assert job.status == JobStatus.succeeded
    assert comfy.connections == []


def test_job_lost_after_comfy_restart_fails_clearly(tmp_path):
    comfy = FakeComfy([[event("progress", value=1, max=4)], []], history_done=False, queued_ids=[])
    job, _ = run_job(tmp_path, None, comfy)
    assert job.status == JobStatus.failed
    assert "遺失" in job.error


def test_gives_up_after_repeated_disconnects(tmp_path):
    comfy = FakeComfy([[event("progress", value=1, max=4)]], history_done=False, queued_ids=[PROMPT_ID])
    job, _ = run_job(tmp_path, None, comfy)
    assert job.status == JobStatus.failed
    assert "重試" in job.error
