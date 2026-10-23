"""佇列壓測：比較 SQLite 與 Redis 佇列、不同 ComfyUI 台數下的派送效率。

不需要 GPU：每台 ComfyUI 用一個假的客戶端代替，「生成」就是 sleep 固定時間。
所以量到的是排程本身的成本（派送延遲、搶單、資料庫更新），不是模型速度。

用法（在 backend 資料夾）：
    python -m scripts.bench_queue --backend sqlite redis --instances 1 2 4 --jobs 60 --gen-ms 200
    python -m scripts.bench_queue --backend redis --redis-url redis://localhost:6379/1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from sqlmodel import func, select

from app.comfy_client import ComfyClient
from app.db import Database, Job, JobStatus
from app.events import EventBus
from app.queue import JobQueue, RedisQueue, SqliteQueue
from app.templates import load_templates
from app.worker import Worker, WorkerPool

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


class SimulatedComfy(ComfyClient):
    """假的 ComfyUI：收到 workflow 後等 gen_seconds，再回報完成。"""

    def __init__(self, name: str, gen_seconds: float):
        super().__init__(f"http://{name}.invalid", client_id=name)
        self.gen_seconds = gen_seconds
        self._prompt = 0

    async def queue_prompt(self, workflow: dict[str, Any]) -> str:
        self._prompt += 1
        return f"{self.client_id}-{self._prompt}"

    def connect_events(self):
        return _Events(self)

    async def prompt_state(self, prompt_id: str) -> str:
        return "done"

    async def output_images(self, prompt_id: str) -> list[dict[str, str]]:
        return [{"filename": f"{prompt_id}.png", "subfolder": "", "type": "output"}]

    async def download(self, image: dict[str, str]) -> bytes:
        return b"PNG"

    async def interrupt(self) -> None:
        pass


class _Events:
    def __init__(self, comfy: SimulatedComfy):
        self.comfy = comfy

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        await asyncio.sleep(self.comfy.gen_seconds)
        return json.dumps({"type": "execution_success", "data": {}})


async def run_once(backend: str, instances: int, jobs: int, gen_seconds: float, redis_url: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db = Database(f"sqlite:///{(tmp_path / 'bench.db').as_posix()}")
        db.init()
        queue: JobQueue
        if backend == "redis":
            queue = RedisQueue.from_url(redis_url)
            await queue.resync([])
        else:
            queue = SqliteQueue(db)

        templates = load_templates(TEMPLATES_DIR)
        bus = EventBus()
        comfies = [SimulatedComfy(f"comfy{i}", gen_seconds) for i in range(instances)]
        workers = [Worker(db, c, bus, templates, tmp_path / "out", 60, queue=queue, name=c.client_id,
                          primary=comfies[0]) for c in comfies]
        pool = WorkerPool(workers, db, bus)
        pool.start()

        start = time.perf_counter()
        created = []
        for i in range(jobs):
            job = db.add(Job(template_id="flux_schnell_t2i", params={"prompt": "bench", "seed": i}))
            created.append(job.id)
            await queue.push(job.id)

        # 監看迴圈本身也會查資料庫，所以只用一個 count 查詢、每 50ms 看一次，避免干擾量測
        finished = (JobStatus.succeeded, JobStatus.failed)
        while True:
            with db.session() as s:
                n_done = s.exec(select(func.count()).select_from(Job).where(Job.status.in_(finished))).one()
            if n_done >= jobs:
                break
            await asyncio.sleep(0.05)
        makespan = time.perf_counter() - start
        done = [db.get(i) for i in created]
        await pool.stop()
        await queue.close()
        for c in comfies:
            await c.close()
        db.engine.dispose()  # Windows 上不釋放連線就刪不掉暫存的資料庫檔

    failed = sum(j.status == JobStatus.failed for j in done)
    waits = sorted((j.started_at - j.created_at).total_seconds() for j in done)
    ideal = jobs * gen_seconds / instances
    return {
        "backend": backend,
        "instances": instances,
        "jobs": jobs,
        "gen_ms": round(gen_seconds * 1000),
        "makespan_s": round(makespan, 2),
        "throughput_jobs_per_min": round(jobs / makespan * 60, 1),
        # 理想值是「所有任務的生成時間 ÷ 台數」，多出來的就是排程的成本
        "overhead_pct": round((makespan - ideal) / ideal * 100, 1),
        "wait_p50_s": round(statistics.median(waits), 2),
        "wait_p95_s": round(waits[int(len(waits) * 0.95) - 1], 2),
        "failed": failed,
        "per_instance": {w: sum(j.worker == w for j in done) for w in sorted({j.worker for j in done})},
    }


def to_markdown(rows: list[dict[str, Any]]) -> str:
    head = "| 佇列 | ComfyUI 台數 | 任務數 | 總耗時 (s) | 吞吐量 (張/分) | 排程成本 | 等待 p50 (s) | 等待 p95 (s) | 各台分配 |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    lines = [head, sep]
    for r in rows:
        spread = " / ".join(str(v) for v in r["per_instance"].values())
        lines.append(f"| {r['backend']} | {r['instances']} | {r['jobs']} | {r['makespan_s']} | "
                     f"{r['throughput_jobs_per_min']} | {r['overhead_pct']}% | {r['wait_p50_s']} | "
                     f"{r['wait_p95_s']} | {spread} |")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", nargs="+", default=["sqlite"], choices=["sqlite", "redis"])
    parser.add_argument("--instances", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--jobs", type=int, default=60)
    parser.add_argument("--gen-ms", type=int, default=200)
    parser.add_argument("--redis-url", default="redis://localhost:6379/15")
    parser.add_argument("--json", type=Path, help="另外把原始結果寫成 JSON")
    args = parser.parse_args()

    rows = []
    for backend in args.backend:
        for n in args.instances:
            row = await run_once(backend, n, args.jobs, args.gen_ms / 1000, args.redis_url)
            print(f"{backend:6} x{n}: {row['makespan_s']}s, overhead {row['overhead_pct']}%", flush=True)
            rows.append(row)
    print()
    print(to_markdown(rows))
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
