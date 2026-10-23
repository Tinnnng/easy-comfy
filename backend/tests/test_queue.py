import asyncio
from pathlib import Path

import fakeredis
import httpx

from app.db import Database, Job, JobStatus
from app.events import EventBus
from app.queue import RedisQueue, SqliteQueue
from app.templates import load_templates
from app.worker import Worker, WorkerPool
from tests.test_worker import FakeComfy, event

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def make_db(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'q.db').as_posix()}")
    db.init()
    return db


def add_jobs(db, n, template_id="flux_schnell_t2i", params=None):
    return [db.add(Job(template_id=template_id, params=params or {"prompt": f"job {i}", "seed": i}))
            for i in range(n)]


def test_claim_is_exclusive(tmp_path):
    db = make_db(tmp_path)
    (job,) = add_jobs(db, 1)
    assert db.claim(job.id, "a").worker == "a"
    assert db.claim(job.id, "b") is None
    assert db.get(job.id).status == JobStatus.running


def test_cancelled_job_cannot_be_claimed(tmp_path):
    db = make_db(tmp_path)
    (job,) = add_jobs(db, 1)
    db.update(job.id, status=JobStatus.cancelled)
    assert db.claim(job.id, "a") is None


def test_sqlite_queue_returns_oldest_and_waits(tmp_path):
    async def go():
        db = make_db(tmp_path)
        q = SqliteQueue(db)
        assert await q.pop(timeout=0.05) is None
        first, _ = add_jobs(db, 2)
        assert await q.pop(timeout=0.05) == first.id

    asyncio.run(go())


def test_redis_queue_is_fifo_and_resyncs(tmp_path):
    async def go():
        q = RedisQueue(fakeredis.FakeAsyncRedis(decode_responses=True))
        await q.push("a")
        await q.push("b")
        assert [await q.pop(1), await q.pop(1)] == ["a", "b"]
        await q.resync(["x", "y"])
        assert await q.pop(1) in {"x", "y"}
        await q.close()

    asyncio.run(go())


def run_pool(tmp_path, queue_factory, n_jobs=6, n_workers=3):
    """多個 worker 共用一個佇列跑完所有任務，回傳每個任務由誰執行。"""

    async def go():
        db = make_db(tmp_path)
        templates = load_templates(TEMPLATES_DIR)
        bus = EventBus()
        queue = queue_factory(db)
        jobs = add_jobs(db, n_jobs)
        for j in jobs:
            await queue.push(j.id)

        def slow_comfy():
            comfy = FakeComfy([[event("execution_success")]] * n_jobs)
            original = comfy.queue_prompt

            async def queue_prompt(workflow):
                await asyncio.sleep(0.05)  # 模擬生成時間，讓其他 worker 有機會接單
                return await original(workflow)

            comfy.queue_prompt = queue_prompt
            return comfy

        workers = [Worker(db, slow_comfy(), bus, templates, tmp_path / "out", 5, queue=queue, name=f"w{i}")
                   for i in range(n_workers)]
        for w in workers:
            w.queue.pop = _short_pop(w.queue.pop)
        pool = WorkerPool(workers, db, bus)
        pool.start()
        for _ in range(200):
            if all(db.get(j.id).status == JobStatus.succeeded for j in jobs):
                break
            await asyncio.sleep(0.02)
        await pool.stop()
        return [db.get(j.id) for j in jobs]

    return asyncio.run(go())


def _short_pop(pop):
    async def wrapper(timeout):
        return await pop(min(timeout, 0.05))

    return wrapper


def test_pool_spreads_jobs_across_instances_with_sqlite(tmp_path):
    done = run_pool(tmp_path, SqliteQueue)
    assert all(j.status == JobStatus.succeeded for j in done)
    assert len({j.worker for j in done}) == 3


def test_pool_spreads_jobs_across_instances_with_redis(tmp_path):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    done = run_pool(tmp_path, lambda db: _FakeRedisQueue(redis))
    assert all(j.status == JobStatus.succeeded for j in done)
    assert len({j.worker for j in done}) == 3


class _FakeRedisQueue(RedisQueue):
    async def pop(self, timeout):
        # fakeredis 的 BRPOP 不支援小於 1 秒的等待，測試裡改成不阻塞的 RPOP
        item = await self.redis.rpop(self.key)
        if item is None:
            await asyncio.sleep(timeout)
        return item


def test_secondary_instance_copies_uploaded_image_from_primary(tmp_path):
    async def go():
        db = make_db(tmp_path)
        templates = load_templates(TEMPLATES_DIR)
        primary = FakeComfy([])
        secondary = FakeComfy([[event("execution_success")]])
        stored = {"primary": {"cat.png": b"IMG"}, "secondary": {}}

        def input_handler(where, fallback):
            def handle(request):
                if request.url.path == "/view" and request.url.params.get("type") == "input":
                    data = stored[where].get(request.url.params["filename"])
                    return httpx.Response(200, content=data) if data else httpx.Response(404)
                if request.url.path == "/upload/image":
                    name = request.content.split(b'filename="')[1].split(b'"')[0].decode()
                    stored[where][name] = b"IMG"
                    return httpx.Response(200, json={"name": name, "subfolder": "", "type": "input"})
                return fallback(request)
            return handle

        primary.http = httpx.AsyncClient(base_url="http://p", transport=httpx.MockTransport(input_handler("primary", primary.handle)))
        secondary.http = httpx.AsyncClient(base_url="http://s", transport=httpx.MockTransport(input_handler("secondary", secondary.handle)))
        (job,) = add_jobs(db, 1, "flux_schnell_i2i", {"image": "cat.png", "prompt": "a cat", "strength": "medium",
                                                      "style": "none", "seed": 1})
        worker = Worker(db, secondary, EventBus(), templates, tmp_path / "out", 5, primary=primary)
        await worker._process(db.claim(job.id, "w2"))
        return db.get(job.id), stored, secondary.workflow

    job, stored, workflow = asyncio.run(go())
    assert job.status == JobStatus.succeeded
    assert stored["secondary"] == {"cat.png": b"IMG"}
    assert workflow["6"]["inputs"]["image"] == "cat.png"


def test_redis_push_front_is_next(tmp_path):
    async def go():
        q = RedisQueue(fakeredis.FakeAsyncRedis(decode_responses=True))
        for i in ("a", "b", "c"):
            await q.push(i)
        await q.push_front("c")
        return [await q.pop(1) for _ in range(4)]

    assert asyncio.run(go()) == ["c", "a", "b", "c"]
