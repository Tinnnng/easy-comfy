"""行程內的事件匯流排：worker 發布任務更新，每條前端 WebSocket 各自訂閱一個佇列。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class EventBus:
    def __init__(self, max_backlog: int = 100):
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._max_backlog = max_backlog

    def publish(self, event: dict[str, Any]) -> None:
        for q in self._subscribers:
            if q.full():
                # 前端太慢就丟掉最舊的事件；任務最終狀態仍可用 GET /api/jobs 取得
                q.get_nowait()
            q.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self._max_backlog)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)
