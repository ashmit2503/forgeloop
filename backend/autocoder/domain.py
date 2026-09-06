from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}

_ARCHIVE_SUFFIXES = {
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
}


class RuntimeName(StrEnum):
    PYTHON = "python"
    NODE = "node"


class RuntimeRequest(StrEnum):
    AUTO = "auto"
    PYTHON = "python"
    NODE = "node"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    INTAKE = "intake"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    PLANNING = "planning"
    GENERATING = "generating"
    INSTALLING = "installing"
    TESTING = "testing"
    EXECUTING = "executing"
    REPAIRING = "repairing"
    SUCCEEDED = "succeeded"
    STOPPED = "stopped"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.STOPPED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.INTERRUPTED,
}


class AttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PhaseName(StrEnum):
    INSTALL = "install"
    TEST = "test"
    EXECUTE = "execute"
    HEALTHCHECK = "healthcheck"
    JUDGE = "judge"


class FilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    content: str

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_workspace_path(value)

    @field_validator("content")
    @classmethod
    def validate_text_content(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("workspace files must contain text, not NUL bytes")
        if len(value.encode("utf-8")) > 512 * 1024:
            raise ValueError("workspace files must be 512 KiB or smaller")
        return value


def validate_workspace_path(value: str) -> str:
    pure = PurePosixPath(value)
    if (
        "\\" in value
        or "\x00" in value
        or ":" in value
        or any(character in value for character in '<>"|?*')
        or pure.is_absolute()
        or not pure.parts
        or value != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(part.endswith((" ", ".")) for part in pure.parts)
        or any(part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES for part in pure.parts)
        or any(any(ord(character) < 32 for character in part) for part in pure.parts)
    ):
        raise ValueError("file paths must be safe relative POSIX paths")
    return value


def _path_identity(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def validate_path_set(paths: list[str]) -> None:
    """Reject file/directory conflicts and case aliases on portable workspaces."""
    entries: dict[str, tuple[str, bool]] = {}
    for path in paths:
        validate_workspace_path(path)
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            identity = _path_identity(prefix)
            is_file = index == len(parts)
            previous = entries.get(identity)
            if previous is not None and (previous[0] != prefix or previous[1] or is_file):
                raise ValueError(f"conflicting workspace paths: {previous[0]!r} and {path!r}")
            entries[identity] = (prefix, is_file)


def _required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("value must not be blank")
    return value


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class CommandSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argv: list[str] = Field(min_length=1, max_length=32)

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 1000 or "\x00" in item for item in value):
            raise ValueError("command arguments must be non-empty, bounded strings")
        return value


class CommandExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["command"] = "command"
    command: CommandSpec


class HttpExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["http"] = "http"
    start_command: CommandSpec
    port: int = Field(ge=1024, le=65535)
    health_path: str = Field(default="/health", min_length=1, max_length=200)

    @field_validator("health_path")
    @classmethod
    def validate_health_path(cls, value: str) -> str:
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("health_path must be an absolute URL path")
        return value


ExecutionSpec = Annotated[CommandExecution | HttpExecution, Field(discriminator="kind")]


class WorkspacePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: RuntimeName
    summary: str = Field(min_length=1, max_length=2000)
    files: list[FilePayload] = Field(min_length=1, max_length=100)
    test_command: CommandSpec
    execution: ExecutionSpec

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _required_text(value)

    @model_validator(mode="after")
    def ensure_unique_files(self) -> WorkspacePlan:
        paths = [_path_identity(file.path) for file in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("workspace contains duplicate file paths")
        validate_path_set([file.path for file in self.files])
        if sum(len(file.content.encode("utf-8")) for file in self.files) > 5 * 1024 * 1024:
            raise ValueError("workspace exceeds the 5 MiB generated size limit")
        return self


class WorkspacePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    upserts: list[FilePayload] = Field(default_factory=list, max_length=100)
    deletes: list[str] = Field(default_factory=list, max_length=100)
    test_command: CommandSpec | None = None
    execution: ExecutionSpec | None = None

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("deletes")
    @classmethod
    def validate_delete_paths(cls, value: list[str]) -> list[str]:
        return [validate_workspace_path(path) for path in value]

    @model_validator(mode="after")
    def validate_operations(self) -> WorkspacePatch:
        validate_path_set([file.path for file in self.upserts])
        upserts = [_path_identity(file.path) for file in self.upserts]
        if len(upserts) != len(set(upserts)):
            raise ValueError("patch contains duplicate upsert paths")
        deletes = [_path_identity(path) for path in self.deletes]
        if len(deletes) != len(set(deletes)):
            raise ValueError("patch contains duplicate delete paths")
        if set(upserts).intersection(deletes):
            raise ValueError("a patch cannot upsert and delete the same path")
        if sum(len(file.content.encode("utf-8")) for file in self.upserts) > 5 * 1024 * 1024:
            raise ValueError("patch upserts exceed the 5 MiB generated size limit")
        if not self.upserts and not self.deletes and not self.test_command and not self.execution:
            raise ValueError("patch must change files or execution metadata")
        return self


class IntakeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ambiguous: bool = False
    question: str | None = Field(default=None, max_length=2000)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def question_required_when_ambiguous(self) -> IntakeDecision:
        if self.ambiguous and not self.question:
            raise ValueError("an ambiguity decision must include a question")
        return self


class AttemptPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    steps: list[str] = Field(min_length=1, max_length=20)
    failure_response: str | None = Field(default=None, max_length=2000)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, value: list[str]) -> list[str]:
        return [_required_text(step) for step in value]

    @field_validator("failure_response")
    @classmethod
    def validate_failure_response(cls, value: str | None) -> str | None:
        return _optional_text(value)


class Citation(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2000)


class ToolEvent(BaseModel):
    index: int = Field(ge=1)
    tool: Literal[
        "read_file", "write_file", "list_dir", "run_command", "run_linter", "web_search", "finalize"
    ]
    label: str
    detail: str | None = None
    status: Literal["running", "passed", "failed"] = "passed"
    duration_ms: int = Field(default=0, ge=0)
    citations: list[Citation] = Field(default_factory=list, max_length=10)


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "read_file", "write_file", "list_dir", "run_command", "run_linter", "web_search", "finalize"
    ]
    note: str = Field(min_length=1, max_length=1000)
    path: str | None = Field(default=None, max_length=240)
    content: str | None = None
    argv: list[str] | None = Field(default=None, max_length=32)
    query: str | None = Field(default=None, max_length=500)
    execution: ExecutionSpec | None = None

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="before")
    @classmethod
    def normalize_root_list_path(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("action") == "list_dir" and value.get("path") in {"", "."}:
            return {**value, "path": None}
        return value

    @field_validator("path")
    @classmethod
    def validate_tool_path(cls, value: str | None) -> str | None:
        return validate_workspace_path(value) if value is not None else None

    @model_validator(mode="after")
    def validate_action_payload(self) -> AgentAction:
        if self.action in {"read_file", "write_file"} and not self.path:
            raise ValueError(f"{self.action} requires a path")
        if self.action == "write_file" and self.content is None:
            raise ValueError("write_file requires content")
        if self.action == "write_file" and _path_identity(self.path or "") == ".autocoder_tests":
            raise ValueError("the coding agent cannot write private acceptance tests")
        if self.action == "write_file" and _path_identity(self.path or "").startswith(".autocoder_tests/"):
            raise ValueError("the coding agent cannot write private acceptance tests")
        if self.action == "run_command" and not self.argv:
            raise ValueError("run_command requires argv")
        if self.argv is not None:
            CommandSpec(argv=self.argv)
        if self.content is not None:
            FilePayload(path=self.path or "file", content=self.content)
        if self.action == "web_search" and not self.query:
            raise ValueError("web_search requires a query")
        if self.action == "finalize" and not self.execution:
            raise ValueError("finalize requires an execution command")
        return self


class TestContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deterministic: bool = True
    summary: str = Field(min_length=1, max_length=2000)
    expected: str = Field(min_length=1, max_length=4000)
    test_files: list[FilePayload] = Field(default_factory=list, max_length=30)
    test_command: CommandSpec | None = None
    execution: ExecutionSpec
    judge_criteria: str | None = Field(default=None, max_length=4000)
    revision_reason: str | None = Field(default=None, max_length=2000)

    @field_validator("summary", "expected")
    @classmethod
    def validate_required_descriptions(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("judge_criteria", "revision_reason")
    @classmethod
    def validate_optional_descriptions(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_execution_command(cls, value: Any) -> Any:
        if isinstance(value, dict) and "execution" not in value and "execution_command" in value:
            migrated = dict(value)
            command = migrated.pop("execution_command")
            migrated["execution"] = {"kind": "command", "command": command}
            return migrated
        return value

    @model_validator(mode="after")
    def validate_test_mode(self) -> TestContract:
        if self.deterministic:
            if not self.test_command or not self.test_files:
                raise ValueError("deterministic tests require test files and a test command")
            if any(not file.path.startswith(".autocoder_tests/") for file in self.test_files):
                raise ValueError("official test files must be under .autocoder_tests/")
            if not any(PurePosixPath(file.path).suffix.casefold() == ".py" for file in self.test_files):
                raise ValueError("deterministic tests require at least one pytest-compatible Python file")
            paths = [_path_identity(file.path) for file in self.test_files]
            if len(paths) != len(set(paths)):
                raise ValueError("official tests contain duplicate file paths")
            if not any(
                argument == ".autocoder_tests" or argument.startswith(".autocoder_tests/")
                for argument in self.test_command.argv
            ):
                raise ValueError("the deterministic test command must target .autocoder_tests")
            if sum(len(file.content.encode("utf-8")) for file in self.test_files) > 5 * 1024 * 1024:
                raise ValueError("official test files exceed the 5 MiB workspace limit")
        else:
            if not self.judge_criteria:
                raise ValueError("judge fallback requires explicit criteria")
            if self.test_files or self.test_command:
                raise ValueError("judge fallback cannot include deterministic test files or commands")
        return self


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    reason: str = Field(min_length=1, max_length=2000)


class ExecutionResult(BaseModel):
    phase: PhaseName
    argv: list[str]
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: int = 0
    truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class FailureContext(BaseModel):
    phase: PhaseName
    exit_code: int | None
    timed_out: bool
    stdout_tail: str
    stderr_tail: str
    message: str


class SandboxCapabilities(BaseModel):
    provider: str
    available: bool
    install_only_network_isolation: bool
    supports_http_healthcheck: bool = True
    detail: str | None = None


class SandboxLimits(BaseModel):
    cpu: float = Field(default=1.0, ge=0.1, le=16)
    memory_mb: int = Field(default=1024, ge=128, le=16384)
    disk_mb: int = Field(default=512, ge=16, le=10240)


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=3, max_length=20_000)
    runtime: Literal[RuntimeRequest.PYTHON] = RuntimeRequest.PYTHON
    model_provider: Literal["ollama"] = "ollama"
    model: str = Field(default="qwen2.5-coder:7b", min_length=1, max_length=200)
    sandbox_provider: Literal["docker"] = "docker"
    max_retries: int = Field(default=3, ge=0, le=5)
    attachments: list[FilePayload] = Field(default_factory=list, max_length=100)
    execution_timeout_seconds: int = Field(default=60, ge=5, le=1800)
    memory_mb: int = Field(default=1024, ge=128, le=16384)
    cpu_limit: float = Field(default=1.0, ge=0.1, le=16)
    disk_mb: int = Field(default=512, ge=16, le=10240)
    execution_network: bool = False

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        if len(value) < 3:
            raise ValueError("prompt must contain at least 3 non-whitespace characters")
        return value

    @field_validator("model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model must not be blank")
        return value

    @model_validator(mode="after")
    def validate_attachments(self) -> TaskCreate:
        validate_path_set([file.path for file in self.attachments])
        paths = [_path_identity(item.path) for item in self.attachments]
        if len(paths) != len(set(paths)):
            raise ValueError("attached files must have unique paths")
        if any(PurePosixPath(path).suffix.casefold() in _ARCHIVE_SUFFIXES for path in paths):
            raise ValueError("attach individual text files, not an archive")
        if any(path == ".autocoder_tests" or path.startswith(".autocoder_tests/") for path in paths):
            raise ValueError(".autocoder_tests is reserved for private acceptance tests")
        if sum(len(item.content.encode("utf-8")) for item in self.attachments) > 5 * 1024 * 1024:
            raise ValueError("attached files exceed the 5 MiB intake limit")
        return self


class FileInfo(BaseModel):
    path: str
    size: int


class AttemptView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: int
    status: AttemptStatus
    phase: str | None
    runtime: RuntimeName
    summary: str | None
    failure_message: str | None
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    logs_truncated: bool
    stdout_truncated: bool
    stderr_truncated: bool
    changed_files: list[str]
    results: list[ExecutionResult]
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    plan: AttemptPlan | None = None
    tool_events: list[ToolEvent] = Field(default_factory=list)
    inner_iterations: int = 0
    test_contract: TestContract | None = None


class TaskView(BaseModel):
    id: str
    prompt: str
    runtime: RuntimeRequest
    resolved_runtime: RuntimeName | None
    model_provider: str
    model: str
    sandbox_provider: str
    max_retries: int
    status: TaskStatus
    summary: str | None
    error: str | None
    active_attempt: int | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    title: str | None = None
    clarification_question: str | None = None
    clarification_answer: str | None = None
    run_started_at: datetime | None = None
    execution_timeout_seconds: int = 60
    memory_mb: int = 1024
    cpu_limit: float = 1.0
    disk_mb: int = 512
    execution_network: bool = False
    attachment_names: list[str] = Field(default_factory=list)


class TaskDetail(TaskView):
    attempts: list[AttemptView] = Field(default_factory=list)
    event_cursor: int = 0


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    timestamp: datetime


class SystemStatus(BaseModel):
    docker: dict[str, Any]
    ollama: dict[str, Any]
    models: list[str]
    default_model: str
    sandbox_images: dict[str, bool]
    providers: dict[str, dict[str, Any]]


class ClarificationAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=20_000)

    @field_validator("answer")
    @classmethod
    def strip_answer(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("answer must not be blank")
        return value


class BenchmarkTaskDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=3, max_length=20_000)
    attachments: list[FilePayload] = Field(default_factory=list, max_length=100)

    @field_validator("id")
    @classmethod
    def strip_task_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task id must not be blank")
        return value

    @field_validator("prompt")
    @classmethod
    def strip_task_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        if len(value) < 3:
            raise ValueError("prompt must contain at least 3 non-whitespace characters")
        return value

    @model_validator(mode="after")
    def validate_attachments(self) -> BenchmarkTaskDefinition:
        validate_path_set([file.path for file in self.attachments])
        paths = [_path_identity(item.path) for item in self.attachments]
        if len(paths) != len(set(paths)):
            raise ValueError("benchmark attachments must have unique paths")
        if any(PurePosixPath(path).suffix.casefold() in _ARCHIVE_SUFFIXES for path in paths):
            raise ValueError("attach individual text files, not an archive")
        if any(path == ".autocoder_tests" or path.startswith(".autocoder_tests/") for path in paths):
            raise ValueError(".autocoder_tests is reserved for private acceptance tests")
        if sum(len(item.content.encode("utf-8")) for item in self.attachments) > 5 * 1024 * 1024:
            raise ValueError("benchmark attachments exceed the 5 MiB intake limit")
        return self


class BenchmarkSetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=80)
    tasks: list[BenchmarkTaskDefinition] = Field(min_length=1, max_length=500)

    @field_validator("name", "version")
    @classmethod
    def strip_set_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def unique_task_ids(self) -> BenchmarkSetCreate:
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark task ids must be unique")
        payload_bytes = sum(
            len(task.prompt.encode("utf-8"))
            + sum(len(file.content.encode("utf-8")) for file in task.attachments)
            for task in self.tasks
        )
        if payload_bytes > 20 * 1024 * 1024:
            raise ValueError("benchmark set exceeds the 20 MiB import limit")
        return self


class BenchmarkSetView(BenchmarkSetCreate):
    id: str
    created_at: datetime


class BenchmarkRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    max_retries: int = Field(default=3, ge=0, le=5)
    execution_timeout_seconds: int = Field(default=60, ge=5, le=1800)
    memory_mb: int = Field(default=1024, ge=128, le=16384)
    cpu_limit: float = Field(default=1.0, ge=0.1, le=16)
    disk_mb: int = Field(default=512, ge=16, le=10240)
    execution_network: bool = False

    @field_validator("model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model must not be blank")
        return value


class BenchmarkReport(BaseModel):
    pass_rate: float = 0
    average_attempts_to_pass: float = 0
    average_inner_iterations: float = 0
    total_time_ms: int = 0


class BenchmarkRunView(BaseModel):
    id: str
    benchmark_set_id: str
    status: Literal["queued", "running", "cancelling", "completed", "cancelled"]
    task_ids: list[str]
    completed_tasks: int
    total_tasks: int
    report: BenchmarkReport | None = None
    created_at: datetime
    completed_at: datetime | None = None
