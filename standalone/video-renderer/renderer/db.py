"""SQLite persistence (SQLAlchemy 2.0): one table of render jobs."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

JOB_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
# the only legal state transitions
ALLOWED = {
    "QUEUED": {"RUNNING", "CANCELLED", "FAILED"},
    "RUNNING": {"SUCCEEDED", "FAILED", "CANCELLED"},
    "SUCCEEDED": set(), "FAILED": set(), "CANCELLED": set(),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class RenderJob(Base):
    __tablename__ = "render_jobs"

    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="QUEUED")
    package_path: Mapped[str] = mapped_column(Text)
    package_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retry_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frames_done: Mapped[int] = mapped_column(Integer, default=0)
    frames_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    ffmpeg_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    qc_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def snapshot(self, links: bool = True) -> dict:
        d = {
            "job_id": self.job_id, "status": self.status, "progress": round(self.progress or 0.0, 4),
            "stage": self.stage, "frames_done": self.frames_done, "frames_total": self.frames_total,
            "package_path": self.package_path, "retry_of": self.retry_of, "idempotency_key": self.idempotency_key,
            "cancel_requested": bool(self.cancel_requested), "error_code": self.error_code,
            "error_message": self.error_message, "output_sha256": self.output_sha256,
            "created_at": _iso(self.created_at), "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
        }
        if links and self.status == "SUCCEEDED":
            d["output_url"], d["qc_url"] = f"/renders/{self.job_id}/output", f"/renders/{self.job_id}/qc"
        return d


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def make_session_factory(db_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
