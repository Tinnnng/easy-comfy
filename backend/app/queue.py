"""任務佇列：決定「下一個要跑哪個任務」。任務本身的狀態一律存在資料庫。

- SqliteQueue：直接查資料庫裡 status=queued 的任務，零設定，適合單機。
- RedisQueue：用 Redis list（LPUSH / BRPOP）派送，worker 可以分散在多個行程或機器。

兩種佇列都可能把同一個任務派給兩個 worker（例如重啟後重新補送），
所以 worker 拿到 id 之後還要用 Database.claim() 原子地把狀態從 queued 改成 running，搶到的才算數。
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from .db import Database


class JobQueue(Protocol):
    name: str

    async def push(self, job_id: str) -> None: ...

    async def push_front(self, job_id: str) -> None:
        """插隊：讓這個任務下一個被取走。"""
        ...

    async def pop(self, timeout: float) -> str | None:
        """等最多 timeout 秒，回傳下一個候選任務 id；沒有就回傳 None。"""
        ...

    async def close(self) -> None: ...


class SqliteQueue:
    name = "sqlite"

    def __init__(self, db: Database):
        self.db = db
        self._wake = asyncio.Event()

    async def push(self, job_id: str) -> None:
        self._wake.set()

    async def push_front(self, job_id: str) -> None:
        # 順序由資料庫的 priority 欄位決定，這裡只要叫醒 worker
        self._wake.set()

    async def pop(self, timeout: float) -> str | None:
        job = self.db.next_queued()
        if job is not None:
            return job.id
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout)
        except TimeoutError:
            return None
        job = self.db.next_queued()
        return job.id if job else None

    async def close(self) -> None:
        pass


class RedisQueue:
    name = "redis"
    key = "easycomfy:queue"

    def __init__(self, redis):  # redis.asyncio.Redis
        self.redis = redis

    @classmethod
    def from_url(cls, url: str) -> "RedisQueue":
        from redis.asyncio import Redis

        # socket_timeout 要比 BRPOP 的等待時間長；連線無聲斷掉時才會丟出例外讓 worker 重試，而不是永遠卡住
        return cls(Redis.from_url(url, decode_responses=True, socket_timeout=30, socket_connect_timeout=5,
                                  socket_keepalive=True, health_check_interval=30))

    async def push(self, job_id: str) -> None:
        await self.redis.lpush(self.key, job_id)

    async def push_front(self, job_id: str) -> None:
        # BRPOP 從右邊取，所以從右邊塞進去就是下一個；原本那一筆之後被取到時 claim 會失敗而略過
        await self.redis.rpush(self.key, job_id)

    async def pop(self, timeout: float) -> str | None:
        # BRPOP 的 timeout 單位是秒，0 代表永遠等，所以至少給 1
        item = await self.redis.brpop([self.key], timeout=max(1, int(timeout)))
        return item[1] if item else None

    async def resync(self, job_ids: list[str]) -> None:
        """啟動時把資料庫裡還在排隊的任務重新放回 Redis（Redis 被清空或換了一台時才不會漏掉）。"""
        await self.redis.delete(self.key)
        if job_ids:
            await self.redis.lpush(self.key, *job_ids)

    async def close(self) -> None:
        await self.redis.aclose()


def create_queue(backend: str, db: Database, redis_url: str) -> JobQueue:
    if backend == "redis":
        return RedisQueue.from_url(redis_url)
    if backend == "sqlite":
        return SqliteQueue(db)
    raise ValueError(f"不支援的佇列後端：{backend}")
