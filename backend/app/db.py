"""任務資料表。這張表同時就是佇列：status=queued 的列依 created_at 排隊。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, create_engine, select


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


FINISHED = {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}


class Job(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    template_id: str
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: JobStatus = Field(default=JobStatus.queued, index=True)
    progress: float = 0.0
    error: str | None = None
    prompt_id: str | None = None
    images: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # 翻譯前的原文，例如 {"prompt": "一隻貓"}；params 裡放的是實際送給模型的英文
    sources: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Database:
    def __init__(self, url: str):
        self.engine = create_engine(url, connect_args={"check_same_thread": False})

    def init(self) -> None:
        SQLModel.metadata.create_all(self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """create_all 不會替舊資料表補欄位，這裡補上之後才新增的欄位。"""
        with self.engine.begin() as conn:
            existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(job)")}
            if "sources" not in existing:
                conn.exec_driver_sql("ALTER TABLE job ADD COLUMN sources JSON DEFAULT '{}'")

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    def add(self, job: Job) -> Job:
        with self.session() as s:
            s.add(job)
            s.commit()
            s.refresh(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self.session() as s:
            return s.get(Job, job_id)

    def list(self, limit: int = 50) -> list[Job]:
        with self.session() as s:
            return list(s.exec(select(Job).order_by(Job.created_at.desc()).limit(limit)))

    def update(self, job_id: str, **changes: Any) -> Job:
        with self.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            for k, v in changes.items():
                setattr(job, k, v)
            s.add(job)
            s.commit()
            s.refresh(job)
            return job

    def next_queued(self) -> Job | None:
        with self.session() as s:
            stmt = (select(Job).where(Job.status == JobStatus.queued)
                    .order_by(Job.created_at).limit(1))
            return s.exec(stmt).first()

    def queue_position(self, job: Job) -> int:
        """前面還有幾個 queued 任務。"""
        with self.session() as s:
            stmt = select(Job).where(Job.status == JobStatus.queued, Job.created_at < job.created_at)
            return len(list(s.exec(stmt)))

    def fail_interrupted(self) -> int:
        """服務重啟時，把上次沒跑完的 running 任務標記為失敗。"""
        with self.session() as s:
            jobs = list(s.exec(select(Job).where(Job.status == JobStatus.running)))
            for job in jobs:
                job.status = JobStatus.failed
                job.error = "服務重啟，任務被中斷"
                job.finished_at = utcnow()
                s.add(job)
            s.commit()
            return len(jobs)
