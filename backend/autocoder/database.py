from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    cast,
    create_engine,
    delete,
    func,
    literal_column,
    select,
    update,
)
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

from autocoder.domain import (
    TERMINAL_STATUSES,
    AttemptPlan,
    AttemptStatus,
    AttemptView,
    BenchmarkReport,
    BenchmarkRunView,
    BenchmarkSetCreate,
    BenchmarkSetView,
    BenchmarkTaskDefinition,
    ExecutionResult,
    RuntimeName,
    RuntimeRequest,
    TaskCreate,
    TaskDetail,
    TaskStatus,
    TaskView,
    TestContract,
    ToolEvent,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Restore UTC metadata that SQLite drops from timezone-aware datetimes."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    prompt: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime: Mapped[str] = mapped_column(String(16))
    resolved_runtime: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(200))
    sandbox_provider: Mapped[str] = mapped_column(String(32))
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attachments_json: Mapped[str] = mapped_column(Text, default="[]")
    clarification_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    memory_mb: Mapped[int] = mapped_column(Integer, default=1024)
    cpu_limit: Mapped[float] = mapped_column(Float, default=1.0)
    disk_mb: Mapped[int] = mapped_column(Integer, default=512)
    execution_network: Mapped[bool] = mapped_column(Boolean, default=False)

    attempts: Mapped[list[AttemptRecord]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="AttemptRecord.number"
    )
    events: Mapped[list[EventRecord]] = relationship(back_populates="task", cascade="all, delete-orphan")


class AttemptRecord(Base):
    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    runtime: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    logs_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    stdout_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    stderr_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_files_json: Mapped[str] = mapped_column(Text, default="[]")
    results_json: Mapped[str] = mapped_column(Text, default="[]")
    workspace_path: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_events_json: Mapped[str] = mapped_column(Text, default="[]")
    inner_iterations: Mapped[int] = mapped_column(Integer, default=0)
    test_contract_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped[TaskRecord] = relationship(back_populates="attempts")


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    data_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[TaskRecord] = relationship(back_populates="events")


class BenchmarkSetRecord(Base):
    __tablename__ = "benchmark_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(80))
    tasks_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BenchmarkRunRecord(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    benchmark_set_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    task_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        # All sessions and request threads must share the same in-memory database.
        parsed_url = make_url(url)
        memory_database = parsed_url.get_backend_name() == "sqlite" and parsed_url.database in (
            None,
            "",
            ":memory:",
        )
        options = {"poolclass": StaticPool} if memory_database else {}
        self.engine = create_engine(url, connect_args=connect_args, **options)
        if url.startswith("sqlite"):
            sqlalchemy_event.listen(self.engine, "connect", _configure_sqlite_connection)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        if self.engine.url.database and self.engine.url.drivername.startswith("sqlite"):
            Path(self.engine.url.database).parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self.engine)
        if self.engine.url.drivername.startswith("sqlite"):
            # WAL is a database-level setting. Configure it once at startup instead
            # of trying to take the journal lock on every pooled connection.
            with self.engine.begin() as connection:
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        self._migrate_sqlite_columns()

    def _migrate_sqlite_columns(self) -> None:
        if not self.engine.url.drivername.startswith("sqlite"):
            return
        additions = {
            "tasks": {
                "title": "TEXT",
                "attachments_json": "TEXT NOT NULL DEFAULT '[]'",
                "clarification_question": "TEXT",
                "clarification_answer": "TEXT",
                "run_started_at": "DATETIME",
                "execution_timeout_seconds": "INTEGER NOT NULL DEFAULT 60",
                "memory_mb": "INTEGER NOT NULL DEFAULT 1024",
                "cpu_limit": "FLOAT NOT NULL DEFAULT 1.0",
                "disk_mb": "INTEGER NOT NULL DEFAULT 512",
                "execution_network": "BOOLEAN NOT NULL DEFAULT 0",
            },
            "attempts": {
                "plan_json": "TEXT",
                "tool_events_json": "TEXT NOT NULL DEFAULT '[]'",
                "inner_iterations": "INTEGER NOT NULL DEFAULT 0",
                "test_contract_json": "TEXT",
                "stdout_truncated": "BOOLEAN NOT NULL DEFAULT 0",
                "stderr_truncated": "BOOLEAN NOT NULL DEFAULT 0",
            },
        }
        with self.engine.begin() as connection:
            for table, columns in additions.items():
                existing = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in existing:
                        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def mark_unfinished_interrupted(self) -> int:
        with self.sessions.begin() as session:
            records = session.query(TaskRecord).filter(
                TaskRecord.status.not_in([status.value for status in TERMINAL_STATUSES])
            )
            count = 0
            task_ids: list[str] = []
            for record in records:
                task_ids.append(record.id)
                record.status = TaskStatus.INTERRUPTED.value
                record.error = "Application restarted while the task was running."
                record.completed_at = utcnow()
                record.updated_at = utcnow()
                count += 1
            if task_ids:
                attempts = session.query(AttemptRecord).filter(
                    AttemptRecord.task_id.in_(task_ids),
                    AttemptRecord.status == AttemptStatus.RUNNING.value,
                )
                for attempt in attempts:
                    attempt.status = AttemptStatus.CANCELLED.value
                    attempt.failure_message = "Application restarted during this attempt."
                    attempt.completed_at = utcnow()
            return count

    def mark_unfinished_benchmarks_cancelled(self) -> int:
        with self.sessions.begin() as session:
            records = session.query(BenchmarkRunRecord).filter(
                BenchmarkRunRecord.status.in_(["queued", "running", "cancelling"])
            )
            count = 0
            for record in records:
                record.status = "cancelled"
                record.completed_at = utcnow()
                count += 1
            return count

    def create_task(self, request: TaskCreate) -> TaskView:
        record = self._new_task_record(request)
        with self.sessions.begin() as session:
            session.add(record)
        return self._task_view(record)

    @staticmethod
    def _new_task_record(request: TaskCreate) -> TaskRecord:
        now = utcnow()
        return TaskRecord(
            id=str(uuid.uuid4()),
            prompt=request.prompt,
            title=_derive_title(request.prompt),
            runtime=request.runtime.value,
            model_provider=request.model_provider,
            model=request.model,
            sandbox_provider=request.sandbox_provider,
            max_retries=request.max_retries,
            status=TaskStatus.QUEUED.value,
            attachments_json=json.dumps([item.model_dump() for item in request.attachments]),
            execution_timeout_seconds=request.execution_timeout_seconds,
            memory_mb=request.memory_mb,
            cpu_limit=request.cpu_limit,
            disk_mb=request.disk_mb,
            execution_network=request.execution_network,
            created_at=now,
            updated_at=now,
        )

    def create_benchmark_batch(
        self,
        benchmark_set_id: str,
        requests: list[TaskCreate],
    ) -> BenchmarkRunView:
        """Create a benchmark run and all of its queued tasks in one transaction."""
        records = [self._new_task_record(request) for request in requests]
        run = BenchmarkRunRecord(
            id=str(uuid.uuid4()),
            benchmark_set_id=benchmark_set_id,
            status="running",
            task_ids_json=json.dumps([record.id for record in records]),
        )
        with self.sessions.begin() as session:
            if not session.get(BenchmarkSetRecord, benchmark_set_id):
                raise KeyError(benchmark_set_id)
            active = session.execute(
                select(BenchmarkRunRecord.id).where(
                    BenchmarkRunRecord.status.in_(["queued", "running", "cancelling"])
                )
            ).first()
            if active:
                raise ValueError("Stop or finish the active benchmark before starting another.")
            session.add_all([*records, run])
        return self._benchmark_run_view(run)

    def get_task_record(self, task_id: str) -> TaskRecord | None:
        with self.sessions() as session:
            return session.get(TaskRecord, task_id)

    def get_task(self, task_id: str) -> TaskDetail | None:
        with self.sessions() as session:
            record = session.get(TaskRecord, task_id)
            if not record:
                return None
            attempts = list(record.attempts)
            event_cursor = session.scalar(
                select(func.max(EventRecord.id)).where(EventRecord.task_id == task_id)
            )
            return self._task_detail(record, attempts, event_cursor=event_cursor or 0)

    def list_tasks(self, limit: int = 100, offset: int = 0) -> list[TaskView]:
        # Windows clocks can give adjacent inserts identical timestamps. SQLite's
        # row id preserves insertion order; random UUIDs do not.
        tie_breaker = literal_column("tasks.rowid") if self.engine.dialect.name == "sqlite" else TaskRecord.id
        with self.sessions() as session:
            records = (
                session.query(TaskRecord)
                .order_by(TaskRecord.created_at.desc(), tie_breaker.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [self._task_view(record) for record in records]

    def update_task(self, task_id: str, **values: Any) -> TaskView:
        with self.sessions.begin() as session:
            record = session.get(TaskRecord, task_id)
            if not record:
                raise KeyError(task_id)
            for key, value in values.items():
                if hasattr(value, "value"):
                    value = value.value
                setattr(record, key, value)
            record.updated_at = utcnow()
        return self._task_view(record)

    def answer_clarification(self, task_id: str, answer: str) -> TaskView:
        return self.update_task(task_id, clarification_answer=answer)

    def create_attempt(
        self,
        task_id: str,
        number: int,
        runtime: RuntimeName,
        workspace_path: Path,
        summary: str,
        changed_files: list[str],
    ) -> AttemptRecord:
        record = AttemptRecord(
            id=str(uuid.uuid4()),
            task_id=task_id,
            number=number,
            status=AttemptStatus.RUNNING.value,
            runtime=runtime.value,
            summary=summary,
            workspace_path=str(workspace_path),
            changed_files_json=json.dumps(changed_files),
        )
        with self.sessions.begin() as session:
            session.add(record)
        return record

    def update_attempt(self, attempt_id: str, **values: Any) -> None:
        with self.sessions.begin() as session:
            record = session.get(AttemptRecord, attempt_id)
            if not record:
                raise KeyError(attempt_id)
            for key, value in values.items():
                if key == "results":
                    value = json.dumps([item.model_dump(mode="json") for item in value])
                    key = "results_json"
                elif key == "plan":
                    value = json.dumps(value.model_dump(mode="json")) if value else None
                    key = "plan_json"
                elif key == "tool_events":
                    value = json.dumps([item.model_dump(mode="json") for item in value])
                    key = "tool_events_json"
                elif key == "test_contract":
                    value = json.dumps(value.model_dump(mode="json")) if value else None
                    key = "test_contract_json"
                elif key == "changed_files":
                    value = json.dumps(value)
                    key = "changed_files_json"
                if hasattr(value, "value"):
                    value = value.value
                setattr(record, key, value)

    def append_attempt_log(self, attempt_id: str, stream: str, chunk: str, limit: int) -> str:
        if stream not in {"stdout", "stderr"}:
            raise ValueError(f"Unsupported log stream: {stream}")
        column = AttemptRecord.stdout if stream == "stdout" else AttemptRecord.stderr
        truncated_column = (
            AttemptRecord.stdout_truncated if stream == "stdout" else AttemptRecord.stderr_truncated
        )
        with self.sessions.begin() as session:
            row = session.execute(
                select(
                    AttemptRecord.id,
                    func.length(cast(column, LargeBinary)),
                    truncated_column,
                ).where(AttemptRecord.id == attempt_id)
            ).one_or_none()
            if row is None:
                return ""
            if row[2]:
                return ""
            remaining = max(0, limit - int(row[1] or 0))
            if remaining <= 0:
                session.execute(
                    update(AttemptRecord)
                    .where(AttemptRecord.id == attempt_id)
                    .values(logs_truncated=True, **{truncated_column.key: True})
                )
                return ""
            encoded = chunk.encode("utf-8")
            accepted = encoded[:remaining].decode("utf-8", errors="ignore")
            values: dict[str, Any] = {column.key: column + accepted}
            if len(encoded) > remaining:
                values["logs_truncated"] = True
                values[truncated_column.key] = True
            session.execute(update(AttemptRecord).where(AttemptRecord.id == attempt_id).values(**values))
            return accepted

    def add_event(self, task_id: str, event_type: str, data: dict[str, Any]) -> int:
        payload = dict(data)
        payload.setdefault("task_id", task_id)
        payload.setdefault("timestamp", utcnow().isoformat())
        event = EventRecord(
            task_id=task_id,
            event_type=event_type,
            data_json=json.dumps(payload, default=str),
        )
        with self.sessions.begin() as session:
            session.add(event)
            session.flush()
            return event.id

    def list_events(self, task_id: str, after_id: int, limit: int = 250) -> list[dict[str, Any]]:
        with self.sessions() as session:
            records = (
                session.query(EventRecord)
                .filter(EventRecord.task_id == task_id, EventRecord.id > after_id)
                .order_by(EventRecord.id)
                .limit(limit)
                .all()
            )
            return [
                {"id": item.id, "event": item.event_type, "data": json.loads(item.data_json)}
                for item in records
            ]

    def prune_transient_events(self, task_id: str) -> int:
        """Remove streamed payloads once their durable task snapshot is complete."""
        transient_types = (
            "log",
            "file_stream_start",
            "file_stream_chunk",
            "file_stream_complete",
        )
        with self.sessions.begin() as session:
            result = session.execute(
                delete(EventRecord).where(
                    EventRecord.task_id == task_id,
                    EventRecord.event_type.in_(transient_types),
                )
            )
            return int(result.rowcount or 0)

    def delete_task(self, task_id: str) -> bool:
        with self.sessions.begin() as session:
            record = session.get(TaskRecord, task_id)
            if not record:
                return False
            session.delete(record)
            return True

    def create_benchmark_set(self, request: BenchmarkSetCreate) -> BenchmarkSetView:
        with self.sessions() as session:
            existing = session.execute(
                select(BenchmarkSetRecord.id).where(
                    BenchmarkSetRecord.name == request.name,
                    BenchmarkSetRecord.version == request.version,
                )
            ).first()
        if existing:
            raise ValueError(f"Benchmark set {request.name!r} version {request.version!r} already exists.")
        record = BenchmarkSetRecord(
            id=str(uuid.uuid4()),
            name=request.name,
            version=request.version,
            tasks_json=json.dumps([item.model_dump(mode="json") for item in request.tasks]),
        )
        with self.sessions.begin() as session:
            session.add(record)
        return self._benchmark_set_view(record)

    def list_benchmark_sets(self) -> list[BenchmarkSetView]:
        with self.sessions() as session:
            records = session.query(BenchmarkSetRecord).order_by(BenchmarkSetRecord.created_at.desc()).all()
            return [self._benchmark_set_view(item) for item in records]

    def get_benchmark_set(self, benchmark_set_id: str) -> BenchmarkSetView | None:
        with self.sessions() as session:
            record = session.get(BenchmarkSetRecord, benchmark_set_id)
            return self._benchmark_set_view(record) if record else None

    def add_benchmark_task(self, benchmark_set_id: str, task: BenchmarkTaskDefinition) -> BenchmarkSetView:
        with self.sessions.begin() as session:
            record = session.get(BenchmarkSetRecord, benchmark_set_id)
            if not record:
                raise KeyError(benchmark_set_id)
            existing_run = session.execute(
                select(BenchmarkRunRecord.id).where(BenchmarkRunRecord.benchmark_set_id == benchmark_set_id)
            ).first()
            if existing_run:
                raise ValueError(
                    "A benchmark set is immutable after its first run; import a new version to edit it."
                )
            tasks = [BenchmarkTaskDefinition.model_validate(item) for item in json.loads(record.tasks_json)]
            if any(item.id == task.id for item in tasks):
                raise ValueError(f"Benchmark task id {task.id!r} already exists in this set.")
            tasks.append(task)
            record.tasks_json = json.dumps([item.model_dump(mode="json") for item in tasks])
        return self._benchmark_set_view(record)

    def create_benchmark_run(self, benchmark_set_id: str, task_ids: list[str]) -> BenchmarkRunView:
        record = BenchmarkRunRecord(
            id=str(uuid.uuid4()),
            benchmark_set_id=benchmark_set_id,
            status="running",
            task_ids_json=json.dumps(task_ids),
        )
        with self.sessions.begin() as session:
            session.add(record)
        return self._benchmark_run_view(record)

    def update_benchmark_run(self, run_id: str, **values: Any) -> BenchmarkRunView:
        with self.sessions.begin() as session:
            record = session.get(BenchmarkRunRecord, run_id)
            if not record:
                raise KeyError(run_id)
            for key, value in values.items():
                if key == "report":
                    value = json.dumps(value.model_dump(mode="json")) if value else None
                    key = "report_json"
                setattr(record, key, value)
        return self._benchmark_run_view(record)

    def list_benchmark_runs(self) -> list[BenchmarkRunView]:
        with self.sessions() as session:
            records = session.query(BenchmarkRunRecord).order_by(BenchmarkRunRecord.created_at.desc()).all()
            return [self._benchmark_run_view(item) for item in records]

    def get_benchmark_run(self, run_id: str) -> BenchmarkRunView | None:
        with self.sessions() as session:
            record = session.get(BenchmarkRunRecord, run_id)
            return self._benchmark_run_view(record) if record else None

    def has_active_benchmark_run(self) -> bool:
        with self.sessions() as session:
            return (
                session.execute(
                    select(BenchmarkRunRecord.id).where(
                        BenchmarkRunRecord.status.in_(["queued", "running", "cancelling"])
                    )
                ).first()
                is not None
            )

    def task_in_active_benchmark(self, task_id: str) -> bool:
        with self.sessions() as session:
            records = session.execute(
                select(BenchmarkRunRecord.task_ids_json).where(
                    BenchmarkRunRecord.status.in_(["queued", "running", "cancelling"])
                )
            ).scalars()
            return any(task_id in json.loads(task_ids_json or "[]") for task_ids_json in records)

    def close(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _task_view(record: TaskRecord) -> TaskView:
        return TaskView(
            id=record.id,
            prompt=record.prompt,
            title=record.title or _derive_title(record.prompt),
            runtime=RuntimeRequest(record.runtime),
            resolved_runtime=RuntimeName(record.resolved_runtime) if record.resolved_runtime else None,
            model_provider=record.model_provider,
            model=record.model,
            sandbox_provider=record.sandbox_provider,
            max_retries=record.max_retries,
            status=TaskStatus(record.status),
            summary=record.summary,
            error=record.error,
            active_attempt=record.active_attempt,
            cancel_requested=record.cancel_requested,
            created_at=as_utc(record.created_at),
            updated_at=as_utc(record.updated_at),
            completed_at=as_utc(record.completed_at),
            clarification_question=record.clarification_question,
            clarification_answer=record.clarification_answer,
            run_started_at=as_utc(record.run_started_at),
            execution_timeout_seconds=record.execution_timeout_seconds or 60,
            memory_mb=record.memory_mb or 1024,
            cpu_limit=record.cpu_limit or 1.0,
            disk_mb=record.disk_mb or 512,
            execution_network=bool(record.execution_network),
            attachment_names=[item.get("path", "") for item in json.loads(record.attachments_json or "[]")],
        )

    @classmethod
    def _task_detail(
        cls,
        record: TaskRecord,
        attempts: Iterable[AttemptRecord],
        *,
        event_cursor: int = 0,
    ) -> TaskDetail:
        base = cls._task_view(record).model_dump()
        return TaskDetail(
            **base,
            attempts=[
                AttemptView(
                    id=item.id,
                    number=item.number,
                    status=AttemptStatus(item.status),
                    phase=item.phase,
                    runtime=RuntimeName(item.runtime),
                    summary=item.summary,
                    failure_message=item.failure_message,
                    exit_code=item.exit_code,
                    timed_out=item.timed_out,
                    stdout=item.stdout,
                    stderr=item.stderr,
                    logs_truncated=item.logs_truncated,
                    stdout_truncated=item.stdout_truncated,
                    stderr_truncated=item.stderr_truncated,
                    changed_files=json.loads(item.changed_files_json),
                    results=[
                        ExecutionResult.model_validate(value) for value in json.loads(item.results_json)
                    ],
                    started_at=as_utc(item.started_at),
                    completed_at=as_utc(item.completed_at),
                    duration_ms=item.duration_ms,
                    plan=AttemptPlan.model_validate_json(item.plan_json) if item.plan_json else None,
                    tool_events=[
                        ToolEvent.model_validate(value) for value in json.loads(item.tool_events_json or "[]")
                    ],
                    inner_iterations=item.inner_iterations or 0,
                    test_contract=TestContract.model_validate_json(item.test_contract_json)
                    if item.test_contract_json
                    else None,
                )
                for item in attempts
            ],
            event_cursor=event_cursor,
        )

    @staticmethod
    def _benchmark_set_view(record: BenchmarkSetRecord) -> BenchmarkSetView:
        return BenchmarkSetView(
            id=record.id,
            name=record.name,
            version=record.version,
            tasks=json.loads(record.tasks_json),
            created_at=as_utc(record.created_at),
        )

    def _benchmark_run_view(self, record: BenchmarkRunRecord) -> BenchmarkRunView:
        task_ids = json.loads(record.task_ids_json or "[]")
        with self.sessions() as session:
            task_statuses = session.execute(
                select(TaskRecord.status).where(TaskRecord.id.in_(task_ids))
            ).scalars()
            completed = sum(1 for status in task_statuses if TaskStatus(status) in TERMINAL_STATUSES)
        return BenchmarkRunView(
            id=record.id,
            benchmark_set_id=record.benchmark_set_id,
            status=record.status,
            task_ids=task_ids,
            completed_tasks=completed,
            total_tasks=len(task_ids),
            report=BenchmarkReport.model_validate_json(record.report_json) if record.report_json else None,
            created_at=as_utc(record.created_at),
            completed_at=as_utc(record.completed_at),
        )


def _derive_title(prompt: str) -> str:
    compact = " ".join(prompt.split())
    return compact if len(compact) <= 72 else f"{compact[:69]}…"


def _configure_sqlite_connection(connection, connection_record) -> None:
    del connection_record
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()
