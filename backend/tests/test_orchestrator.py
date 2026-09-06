from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from autocoder.config import Settings
from autocoder.database import Database
from autocoder.domain import (
    AgentAction,
    AttemptPlan,
    CommandExecution,
    CommandSpec,
    ExecutionResult,
    FilePayload,
    HttpExecution,
    PhaseName,
    RuntimeName,
    SandboxCapabilities,
    TaskCreate,
    TaskStatus,
)
from autocoder.domain import TestContract as AcceptanceContract
from autocoder.errors import InfrastructureError
from autocoder.orchestrator import OFFICIAL_TEST_PASS_MARKER, Orchestrator
from autocoder.providers.models.base import ModelProvider
from autocoder.providers.sandboxes.base import OutputCallback, SandboxProvider, SandboxSession
from autocoder.workspace import WorkspaceManager


class FakeModel(ModelProvider):
    name = "fake"

    def __init__(self) -> None:
        self.repairs = 0
        self.action_index = 0

    async def plan_attempt(self, *, prompt, attempt_number, workspace, failure, model):
        self.repairs = attempt_number - 1
        self.action_index = 0
        return AttemptPlan(summary="Build a greeting", steps=["Write the program", "Validate it"])

    async def next_action(self, *, prompt, plan, workspace, transcript, model):
        self.action_index += 1
        if self.action_index == 1:
            return AgentAction(
                action="write_file",
                note="Write the program",
                path="app.py",
                content=f"print('attempt {self.repairs + 1}')",
            )
        return AgentAction(
            action="finalize",
            note="Validate the program",
            execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
        )

    async def generate_tests(self, *, prompt, workspace, previous, failure, model):
        return previous or AcceptanceContract(
            summary="Verify output",
            expected="A successful greeting",
            test_files=[
                FilePayload(path=".autocoder_tests/test_app.py", content="def test_ok(): assert True")
            ],
            test_command=CommandSpec(argv=["python", "-m", "pytest", ".autocoder_tests"]),
            execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
        )


class FakeSession(SandboxSession):
    def __init__(self, behavior: dict[str, int]) -> None:
        self.behavior = behavior
        self.network_disabled = False
        self.destroyed = False

    async def run(
        self,
        command: CommandSpec,
        *,
        phase: PhaseName,
        timeout_seconds: int,
        on_output: OutputCallback,
    ) -> ExecutionResult:
        del timeout_seconds
        await on_output("stdout", f"{phase.value} output\n")
        if self.behavior.get(f"block_{phase.value}"):
            await asyncio.Event().wait()
        code = self.behavior.get(phase.value, 0)
        return ExecutionResult(
            phase=phase,
            argv=command.argv,
            exit_code=code,
            stderr="failure" if code else "",
            stdout=f"{OFFICIAL_TEST_PASS_MARKER}1\n" if phase == PhaseName.TEST and code == 0 else "",
        )

    async def disable_network(self) -> None:
        self.network_disabled = True

    async def enable_network(self) -> None:
        self.network_disabled = False

    async def start_and_probe(
        self,
        execution: HttpExecution,
        *,
        timeout_seconds: int,
        on_output: OutputCallback,
    ) -> ExecutionResult:
        del timeout_seconds
        await on_output("stdout", "server ready\n")
        code = self.behavior.get("healthcheck", 0)
        return ExecutionResult(
            phase=PhaseName.HEALTHCHECK,
            argv=execution.start_command.argv,
            exit_code=code,
        )

    async def destroy(self) -> None:
        self.destroyed = True


class FakeSandboxProvider(SandboxProvider):
    name = "fake"

    def __init__(self, behaviors: list[dict[str, int]], infrastructure_error: bool = False) -> None:
        self.behaviors = behaviors
        self.infrastructure_error = infrastructure_error
        self.sessions: list[FakeSession] = []

    async def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(provider="fake", available=True, install_only_network_isolation=True)

    async def create(self, runtime: RuntimeName, workspace: Path, limits=None) -> SandboxSession:
        del runtime, limits
        if self.infrastructure_error:
            raise InfrastructureError("sandbox service unavailable")
        behavior = (
            self.behaviors[min(len(self.sessions) // 2, len(self.behaviors) - 1)]
            if workspace.name == "run"
            else {}
        )
        session = FakeSession(behavior)
        self.sessions.append(session)
        return session


@pytest.fixture
def services(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.initialize()
    yield settings, database, WorkspaceManager(settings)
    database.close()


async def run_task(
    services,
    sandbox: FakeSandboxProvider,
    *,
    max_retries: int,
) -> tuple[FakeModel, object]:
    settings, database, workspaces = services
    model = FakeModel()
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": model},
        sandbox_providers={"docker": sandbox},
    )
    task = database.create_task(TaskCreate(prompt="Build a hello program", max_retries=max_retries))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        for _ in range(200):
            detail = database.get_task(task.id)
            if detail and detail.status in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.STOPPED,
                TaskStatus.CANCELLED,
            }:
                return model, detail
            await asyncio.sleep(0.01)
        raise AssertionError("task did not finish")
    finally:
        await orchestrator.stop()


@pytest.mark.asyncio
async def test_immediate_success(services) -> None:
    model, task = await run_task(services, FakeSandboxProvider([{"test": 0, "execute": 0}]), max_retries=3)
    assert task.status == TaskStatus.SUCCEEDED
    assert len(task.attempts) == 1
    assert model.repairs == 0


@pytest.mark.asyncio
async def test_event_compaction_failure_cannot_overwrite_a_successful_run(
    services,
    monkeypatch,
) -> None:
    _, database, _ = services

    def fail_compaction(task_id: str) -> int:
        del task_id
        raise RuntimeError("database maintenance unavailable")

    monkeypatch.setattr(database, "prune_transient_events", fail_compaction)
    _, task = await run_task(
        services,
        FakeSandboxProvider([{"test": 0, "execute": 0}]),
        max_retries=0,
    )

    assert task.status == TaskStatus.SUCCEEDED
    assert task.attempts[0].status.value == "succeeded"


@pytest.mark.asyncio
async def test_failure_is_repaired_and_reexecuted(services) -> None:
    sandbox = FakeSandboxProvider([{"test": 1}, {"test": 0, "execute": 0}])
    model, task = await run_task(services, sandbox, max_retries=3)
    assert task.status == TaskStatus.SUCCEEDED
    assert len(task.attempts) == 2
    assert model.repairs == 1
    assert all(session.network_disabled for session in sandbox.sessions[1::2])
    assert all(session.destroyed for session in sandbox.sessions)


@pytest.mark.asyncio
async def test_retry_limit_is_exact(services) -> None:
    model, task = await run_task(services, FakeSandboxProvider([{"test": 2}]), max_retries=2)
    assert task.status == TaskStatus.STOPPED
    assert len(task.attempts) == 3
    assert model.repairs == 2
    assert "Maximum attempts reached" in task.error


@pytest.mark.asyncio
async def test_infrastructure_failure_does_not_trigger_repair(services) -> None:
    model, task = await run_task(
        services, FakeSandboxProvider([{}], infrastructure_error=True), max_retries=3
    )
    assert task.status == TaskStatus.FAILED
    assert model.repairs == 0
    assert "Infrastructure error" in task.error
    assert task.attempts[0].status.value == "failed"


async def wait_for_attempt(database: Database, task_id: str):
    for _ in range(200):
        detail = database.get_task(task_id)
        if detail and detail.attempts:
            return detail
        await asyncio.sleep(0.01)
    raise AssertionError("attempt did not start")


@pytest.mark.asyncio
async def test_cancellation_closes_active_attempt_and_sandbox(services) -> None:
    settings, database, workspaces = services
    model = FakeModel()
    sandbox = FakeSandboxProvider([{"block_test": 1}])
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": model},
        sandbox_providers={"docker": sandbox},
    )
    task = database.create_task(TaskCreate(prompt="Build a hello program"))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    await wait_for_attempt(database, task.id)
    await orchestrator.cancel(task.id)
    try:
        for _ in range(200):
            detail = database.get_task(task.id)
            if detail and detail.status == TaskStatus.CANCELLED:
                assert detail.attempts[0].status.value == "cancelled"
                assert sandbox.sessions[0].destroyed
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("task was not cancelled")
    finally:
        await orchestrator.stop()


@pytest.mark.asyncio
async def test_shutdown_marks_active_task_interrupted(services) -> None:
    settings, database, workspaces = services
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": FakeModel()},
        sandbox_providers={"docker": FakeSandboxProvider([{"block_test": 1}])},
    )
    task = database.create_task(TaskCreate(prompt="Build a hello program"))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    await wait_for_attempt(database, task.id)
    await orchestrator.stop()
    detail = database.get_task(task.id)
    assert detail.status == TaskStatus.INTERRUPTED
    assert detail.attempts[0].status.value == "cancelled"


@pytest.mark.asyncio
async def test_queued_task_cancels_immediately(services) -> None:
    settings, database, workspaces = services
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": FakeModel()},
        sandbox_providers={"docker": FakeSandboxProvider([{}])},
    )
    task = database.create_task(TaskCreate(prompt="Build a queued program"))

    assert await orchestrator.cancel(task.id)
    detail = database.get_task(task.id)
    assert detail is not None
    assert detail.status == TaskStatus.CANCELLED
    assert detail.completed_at is not None
