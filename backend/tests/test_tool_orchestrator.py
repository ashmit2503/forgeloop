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
    FailureContext,
    FilePayload,
    HttpExecution,
    IntakeDecision,
    JudgeVerdict,
    PhaseName,
    RuntimeName,
    SandboxCapabilities,
    SandboxLimits,
    TaskCreate,
    TaskStatus,
)
from autocoder.domain import (
    TestContract as AcceptanceContract,
)
from autocoder.orchestrator import (
    OFFICIAL_TEST_COMMAND,
    OFFICIAL_TEST_PASS_MARKER,
    Orchestrator,
    _action_for_transcript,
    _finalization_policy_error,
    _has_verified_official_test_pass,
    _is_dependency_install,
    _is_private_test_contract_defect,
    _normalize_search_url,
    _stream_chunk_size,
)
from autocoder.providers.models.base import ModelProvider
from autocoder.providers.sandboxes.base import OutputCallback, SandboxProvider, SandboxSession
from autocoder.workspace import WorkspaceManager


class ToolModel(ModelProvider):
    name = "tool-fake"

    def __init__(
        self,
        *,
        ambiguous: bool = False,
        deterministic: bool = True,
        judge_passes: bool = True,
        http: bool = False,
    ) -> None:
        self.ambiguous = ambiguous
        self.deterministic = deterministic
        self.judge_passes = judge_passes
        self.http = http
        self.action_index = 0
        self.plans: list[AttemptPlan] = []
        self.test_failures: list[FailureContext | None] = []

    async def intake(self, *, prompt: str, attachments: list[str], model: str) -> IntakeDecision:
        del prompt, attachments, model
        return IntakeDecision(
            ambiguous=self.ambiguous, question="Which output format?" if self.ambiguous else None
        )

    async def plan_attempt(
        self, *, prompt: str, attempt_number: int, workspace: Path, failure: FailureContext | None, model: str
    ) -> AttemptPlan:
        del prompt, workspace, model
        plan = AttemptPlan(
            summary=f"Plan {attempt_number}",
            steps=["Write the implementation", "Validate it"],
            failure_response=failure.message if failure else None,
        )
        self.plans.append(plan)
        self.action_index = 0
        return plan

    async def next_action(
        self, *, prompt: str, plan: AttemptPlan, workspace: Path, transcript: list[dict[str, str]], model: str
    ) -> AgentAction:
        del prompt, plan, workspace, transcript, model
        self.action_index += 1
        if self.action_index == 1:
            return AgentAction(
                action="write_file", note="Write the program", path="app.py", content="print('ok')\n"
            )
        return AgentAction(
            action="finalize",
            note="Finalize the implementation",
            execution=(
                HttpExecution(
                    start_command=CommandSpec(argv=["python", "app.py"]),
                    port=8000,
                    health_path="/health",
                )
                if self.http
                else CommandExecution(command=CommandSpec(argv=["python", "app.py"]))
            ),
        )

    async def generate_tests(
        self,
        *,
        prompt: str,
        workspace: Path,
        previous: AcceptanceContract | None,
        failure: FailureContext | None,
        model: str,
    ) -> AcceptanceContract:
        del prompt, workspace, model
        self.test_failures.append(failure)
        return previous or AcceptanceContract(
            deterministic=self.deterministic,
            summary="Program exits successfully",
            expected="Exit code 0 and output ok",
            test_files=(
                [FilePayload(path=".autocoder_tests/test_app.py", content="def test_ok(): assert True\n")]
                if self.deterministic
                else []
            ),
            test_command=(
                CommandSpec(argv=["python", "-m", "pytest", "-q", ".autocoder_tests"])
                if self.deterministic
                else None
            ),
            execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
            judge_criteria=None if self.deterministic else "The program greets the user.",
        )

    async def judge(
        self,
        *,
        prompt: str,
        criteria: str,
        stdout: str,
        stderr: str,
        exit_code: int | None,
        model: str,
    ) -> JudgeVerdict:
        del prompt, criteria, stdout, stderr, exit_code, model
        return JudgeVerdict(
            passed=self.judge_passes,
            reason="Behavior matched" if self.judge_passes else "Greeting was missing",
        )


class ToolSession(SandboxSession):
    def __init__(self, test_exit: int, *, verified_test_output: bool = True) -> None:
        self.test_exit = test_exit
        self.verified_test_output = verified_test_output
        self.test_runs = 0
        self.health_runs = 0
        self.destroyed = False
        self.network_disabled = False
        self.network_events: list[bool] = []

    async def run(
        self, command: CommandSpec, *, phase: PhaseName, timeout_seconds: int, on_output: OutputCallback
    ) -> ExecutionResult:
        del timeout_seconds
        await on_output("stdout", f"{phase.value}\n")
        code = self.test_exit if phase == PhaseName.TEST else 0
        if phase == PhaseName.TEST:
            self.test_runs += 1
        stdout = "ok\n"
        if phase == PhaseName.TEST and code == 0 and self.verified_test_output:
            stdout += f"{OFFICIAL_TEST_PASS_MARKER}1\n"
        return ExecutionResult(
            phase=phase,
            argv=command.argv,
            exit_code=code,
            stdout=stdout if not code else "",
            stderr="failure" if code else "",
        )

    async def disable_network(self) -> None:
        self.network_disabled = True
        self.network_events.append(False)

    async def enable_network(self) -> None:
        self.network_disabled = False
        self.network_events.append(True)

    async def start_and_probe(
        self, execution: HttpExecution, *, timeout_seconds: int, on_output: OutputCallback
    ) -> ExecutionResult:
        del timeout_seconds
        self.health_runs += 1
        await on_output("stdout", "healthy\n")
        return ExecutionResult(
            phase=PhaseName.HEALTHCHECK,
            argv=execution.start_command.argv,
            exit_code=0,
            stdout="healthy\n",
        )

    async def destroy(self) -> None:
        self.destroyed = True


class ToolSandbox(SandboxProvider):
    name = "tool-sandbox"

    def __init__(self, exits: list[int], *, verified_test_output: bool = True) -> None:
        self.exits = exits
        self.verified_test_output = verified_test_output
        self.sessions: list[ToolSession] = []
        self.limits: list[SandboxLimits | None] = []

    async def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(provider=self.name, available=True, install_only_network_isolation=True)

    async def create(
        self, runtime: RuntimeName, workspace: Path, limits: SandboxLimits | None = None
    ) -> SandboxSession:
        del runtime, workspace
        session = ToolSession(
            self.exits[min(len(self.sessions), len(self.exits) - 1)],
            verified_test_output=self.verified_test_output,
        )
        self.sessions.append(session)
        self.limits.append(limits)
        return session


class PrivateTestDefectSession(ToolSession):
    async def run(
        self, command: CommandSpec, *, phase: PhaseName, timeout_seconds: int, on_output: OutputCallback
    ) -> ExecutionResult:
        result = await super().run(
            command,
            phase=phase,
            timeout_seconds=timeout_seconds,
            on_output=on_output,
        )
        if phase == PhaseName.TEST and result.exit_code:
            return result.model_copy(
                update={
                    "stdout": (
                        ".autocoder_tests/test_app.py:4: NameError: name 'subprocess' is not defined\n"
                    ),
                    "stderr": "",
                }
            )
        return result


class PrivateTestDefectSandbox(ToolSandbox):
    async def create(
        self, runtime: RuntimeName, workspace: Path, limits: SandboxLimits | None = None
    ) -> SandboxSession:
        del runtime, workspace
        session = PrivateTestDefectSession(self.exits[min(len(self.sessions), len(self.exits) - 1)])
        self.sessions.append(session)
        self.limits.append(limits)
        return session


class PrivateTestRepairModel(ToolModel):
    async def generate_tests(
        self,
        *,
        prompt: str,
        workspace: Path,
        previous: AcceptanceContract | None,
        failure: FailureContext | None,
        model: str,
    ) -> AcceptanceContract:
        del prompt, workspace, model
        self.test_failures.append(failure)
        execution = CommandExecution(command=CommandSpec(argv=["python", "app.py"]))
        if previous is None:
            return AcceptanceContract(
                deterministic=True,
                summary="Run the private test",
                expected="The generated private test imports its dependencies and passes",
                test_files=[
                    FilePayload(
                        path=".autocoder_tests/test_app.py",
                        content="def test_output():\n    assert subprocess is not None\n",
                    )
                ],
                test_command=CommandSpec(argv=["python", "-m", "pytest", "-q", ".autocoder_tests"]),
                execution=execution,
            )
        return previous.model_copy(
            update={
                "test_files": [
                    FilePayload(
                        path=".autocoder_tests/test_app.py",
                        content=(
                            "import subprocess\n\ndef test_output():\n    assert subprocess is not None\n"
                        ),
                    )
                ],
                "revision_reason": "The private test omitted its required subprocess import.",
            }
        )


class RecoveringToolModel(ToolModel):
    async def next_action(
        self,
        *,
        prompt: str,
        plan: AttemptPlan,
        workspace: Path,
        transcript: list[dict[str, str]],
        model: str,
    ) -> AgentAction:
        del prompt, plan, workspace, transcript, model
        self.action_index += 1
        if self.action_index == 1:
            return AgentAction(action="read_file", note="Inspect optional config", path="missing.toml")
        if self.action_index == 2:
            return AgentAction(
                action="write_file",
                note="Write the program",
                path="app.py",
                content="print('ok')\n",
            )
        return AgentAction(
            action="finalize",
            note="Finalize the implementation",
            execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
        )


class PrematureFinalizeModel(ToolModel):
    async def next_action(
        self,
        *,
        prompt: str,
        plan: AttemptPlan,
        workspace: Path,
        transcript: list[dict[str, str]],
        model: str,
    ) -> AgentAction:
        del prompt, plan, workspace, model
        self.action_index += 1
        if self.action_index == 1:
            return AgentAction(
                action="finalize",
                note="Claim completion too early",
                execution=CommandExecution(command=CommandSpec(argv=["echo", "ok"])),
            )
        if self.action_index == 2:
            assert "no implementation files" in transcript[-1]["result"]
            return AgentAction(
                action="write_file",
                note="Write the missing implementation",
                path="app.py",
                content="print('ok')\n",
            )
        return AgentAction(
            action="finalize",
            note="Run the stored implementation",
            execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
        )


@pytest.fixture
def tool_services(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'tool.db'}")
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.initialize()
    yield settings, database, WorkspaceManager(settings)
    database.close()


async def wait_terminal(database: Database, task_id: str):
    for _ in range(400):
        task = database.get_task(task_id)
        if task and task.status in {TaskStatus.SUCCEEDED, TaskStatus.STOPPED, TaskStatus.FAILED}:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("tool task did not finish")


async def test_failed_private_test_staging_removes_the_disposable_workspace(tool_services) -> None:
    settings, database, workspaces = tool_services
    settings.max_files = 1
    sandbox = ToolSandbox([0])
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": ToolModel()},
        sandbox_providers={"docker": sandbox},
    )
    task = database.create_task(TaskCreate(prompt="Build a Python greeting"))
    try:
        await orchestrator.start()
        await orchestrator.enqueue(task.id)
        result = await wait_terminal(database, task.id)
        assert result.status == TaskStatus.FAILED
        source = workspaces.source_path(task.id, 1)
        assert not (source.parent / "run").exists()
        assert [file.path for file in workspaces.list_files(source)] == ["app.py"]
        assert all(session.destroyed for session in sandbox.sessions)
    finally:
        await orchestrator.stop()


@pytest.mark.asyncio
async def test_tool_loop_persists_plan_tools_and_official_test_once(tool_services) -> None:
    settings, database, workspaces = tool_services
    model = ToolModel()
    sandbox = ToolSandbox([0])
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": model},
        sandbox_providers={"docker": sandbox},
    )
    task = database.create_task(TaskCreate(prompt="Build a Python greeting", memory_mb=2048, cpu_limit=2))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        detail = await wait_terminal(database, task.id)
    finally:
        await orchestrator.stop()
    assert detail.status == TaskStatus.SUCCEEDED
    assert detail.attempts[0].plan.summary == "Plan 1"
    assert [event.tool for event in detail.attempts[0].tool_events] == ["write_file", "finalize"]
    assert detail.attempts[0].test_contract.expected == "Exit code 0 and output ok"
    assert len(sandbox.sessions) == 2
    assert sandbox.sessions[0].test_runs == 0
    assert sandbox.sessions[1].test_runs == 1
    assert sandbox.sessions[1].network_disabled
    assert sandbox.sessions[0].destroyed and sandbox.sessions[1].destroyed
    assert sandbox.limits[0].memory_mb == 2048
    assert sandbox.limits[0].cpu == 2
    source = workspaces.source_path(task.id, 1)
    source_files = [item.path for item in workspaces.list_files(source)]
    assert source_files == ["app.py"]
    assert not (source.parent / "run").exists()


@pytest.mark.asyncio
async def test_recoverable_tool_errors_are_returned_to_the_agent(tool_services) -> None:
    settings, database, workspaces = tool_services
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": RecoveringToolModel()},
        sandbox_providers={"docker": ToolSandbox([0])},
    )
    task = database.create_task(TaskCreate(prompt="Build a resilient Python greeting"))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        detail = await wait_terminal(database, task.id)
    finally:
        await orchestrator.stop()

    assert detail.status == TaskStatus.SUCCEEDED
    assert detail.attempts[0].tool_events[0].status == "failed"
    assert "file not found" in (detail.attempts[0].tool_events[0].detail or "")
    assert detail.attempts[0].tool_events[-1].tool == "finalize"


@pytest.mark.asyncio
async def test_premature_empty_finalization_is_returned_to_the_agent(tool_services) -> None:
    settings, database, workspaces = tool_services
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": PrematureFinalizeModel()},
        sandbox_providers={"docker": ToolSandbox([0])},
    )
    task = database.create_task(TaskCreate(prompt="Build a real Python program"))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        detail = await wait_terminal(database, task.id)
    finally:
        await orchestrator.stop()

    assert detail.status == TaskStatus.SUCCEEDED
    assert [event.status for event in detail.attempts[0].tool_events] == [
        "failed",
        "passed",
        "passed",
    ]
    assert (workspaces.source_path(task.id, 1) / "app.py").is_file()


@pytest.mark.asyncio
async def test_clarification_pauses_timer_and_attempts(tool_services) -> None:
    settings, database, workspaces = tool_services
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": ToolModel(ambiguous=True)},
        sandbox_providers={"docker": ToolSandbox([0])},
    )
    task = database.create_task(TaskCreate(prompt="Build the requested formatter"))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        for _ in range(200):
            waiting = database.get_task(task.id)
            if waiting and waiting.status == TaskStatus.AWAITING_CLARIFICATION:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("task never requested clarification")
        assert waiting.run_started_at is None
        assert waiting.attempts == []
        assert await orchestrator.answer_clarification(task.id, "Use JSON")
        detail = await wait_terminal(database, task.id)
    finally:
        await orchestrator.stop()
    assert detail.status == TaskStatus.SUCCEEDED
    assert detail.run_started_at is not None
    assert detail.clarification_answer == "Use JSON"


@pytest.mark.asyncio
async def test_exhausted_tool_attempts_end_stopped_with_fresh_repair_plan(tool_services) -> None:
    settings, database, workspaces = tool_services
    model = ToolModel()
    sandbox = ToolSandbox([1, 1])
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": model},
        sandbox_providers={"docker": sandbox},
    )
    task = database.create_task(TaskCreate(prompt="Build a Python greeting", max_retries=1))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        detail = await wait_terminal(database, task.id)
    finally:
        await orchestrator.stop()
    assert detail.status == TaskStatus.STOPPED
    assert len(detail.attempts) == 2
    assert model.plans[1].failure_response == "test exited with code 1"
    assert model.test_failures[0] is None
    assert model.test_failures[1] is not None
    assert model.test_failures[1].message == "test exited with code 1"
    assert len(sandbox.sessions) == 4
    assert [session.test_runs for session in sandbox.sessions] == [0, 1, 0, 1]
    assert all(session.destroyed for session in sandbox.sessions)


def test_official_test_changes_require_an_explicit_revision_reason() -> None:
    execution = CommandExecution(command=CommandSpec(argv=["python", "app.py"]))
    original = AcceptanceContract(
        summary="Check original behavior",
        expected="original",
        test_files=[FilePayload(path=".autocoder_tests/test_app.py", content="assert True\n")],
        test_command=CommandSpec(argv=["python", "-m", "pytest", ".autocoder_tests"]),
        execution=execution,
    )
    weakened = original.model_copy(
        update={
            "expected": "anything",
            "test_files": [FilePayload(path=".autocoder_tests/test_app.py", content="assert 1 == 1\n")],
        }
    )

    selected, event = Orchestrator._select_test_contract(original, weakened, execution)
    assert selected.expected == "original"
    assert event == "official_test_reused"

    justified = weakened.model_copy(
        update={"revision_reason": "The original expectation conflicts with the task."}
    )
    selected, event = Orchestrator._select_test_contract(original, justified, execution)
    assert selected.expected == "anything"
    assert event == "official_test_revised"


def test_official_checkpoint_uses_pytest_instead_of_a_model_authored_noop_runner() -> None:
    execution = CommandExecution(command=CommandSpec(argv=["python", "app.py"]))
    candidate = AcceptanceContract(
        summary="Check behavior",
        expected="A concrete result",
        test_files=[
            FilePayload(
                path=".autocoder_tests/test_app.py",
                content="def test_result():\n    assert False\n",
            )
        ],
        # Running this file directly would define the test and exit zero without
        # executing it. The orchestrator must never trust this as the checkpoint.
        test_command=CommandSpec(argv=["python", ".autocoder_tests/test_app.py"]),
        execution=execution,
    )

    selected, event = Orchestrator._select_test_contract(None, candidate, execution)

    assert event == "official_test_created"
    assert selected.test_command == OFFICIAL_TEST_COMMAND


def test_official_checkpoint_requires_a_positive_runner_marker() -> None:
    base = ExecutionResult(phase=PhaseName.TEST, argv=["pytest"], exit_code=0)

    assert not _has_verified_official_test_pass(base)
    assert not _has_verified_official_test_pass(
        base.model_copy(update={"stdout": f"{OFFICIAL_TEST_PASS_MARKER}0\n"})
    )
    assert _has_verified_official_test_pass(
        base.model_copy(update={"stdout": f"{OFFICIAL_TEST_PASS_MARKER}2\n"})
    )
    assert not _has_verified_official_test_pass(
        base.model_copy(update={"stdout": (f"{OFFICIAL_TEST_PASS_MARKER}99\n{OFFICIAL_TEST_PASS_MARKER}0\n")})
    )


@pytest.mark.asyncio
async def test_clean_test_exit_without_runner_confirmation_cannot_pass(tool_services) -> None:
    settings, database, workspaces = tool_services
    sandbox = ToolSandbox([0], verified_test_output=False)
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": ToolModel()},
        sandbox_providers={"docker": sandbox},
    )
    task = database.create_task(TaskCreate(prompt="Build a Python greeting", max_retries=0))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        detail = await wait_terminal(database, task.id)
    finally:
        await orchestrator.stop()

    assert detail.status == TaskStatus.STOPPED
    assert detail.attempts[0].results[0].exit_code == 1
    assert "did not report any completed passing tests" in detail.attempts[0].stderr


def test_tool_transcript_excludes_file_content_and_records_its_size() -> None:
    action = AgentAction(
        action="write_file",
        note="Write application",
        path="app.py",
        content="print('private payload')\n",
    )

    transcript = _action_for_transcript(action)

    assert "private payload" not in transcript
    assert '"content_bytes":25' in transcript
    assert '"path":"app.py"' in transcript


def test_stream_chunking_is_bounded_for_large_files() -> None:
    content = "x" * (512 * 1024)
    chunk_size = _stream_chunk_size(content)

    assert (len(content) + chunk_size - 1) // chunk_size <= 24
    assert _stream_chunk_size("tiny") == 64


def test_finalization_requires_real_workspace_code_and_an_honest_entrypoint(tmp_path: Path) -> None:
    echo = CommandExecution(command=CommandSpec(argv=["echo", "claimed output"]))
    python_inline = CommandExecution(command=CommandSpec(argv=["python", "-c", "print('claimed output')"]))

    assert "no implementation files" in (_finalization_policy_error(echo, tmp_path) or "")
    (tmp_path / "app.py").write_text("print('real output')\n", encoding="utf-8")
    assert "not spoof output" in (_finalization_policy_error(echo, tmp_path) or "")
    assert "not inline Python" in (_finalization_policy_error(python_inline, tmp_path) or "")
    assert (
        _finalization_policy_error(CommandExecution(command=CommandSpec(argv=["python", "app.py"])), tmp_path)
        is None
    )
    assert "does not exist" in (
        _finalization_policy_error(
            CommandExecution(command=CommandSpec(argv=["python", "missing.py"])), tmp_path
        )
        or ""
    )


@pytest.mark.asyncio
async def test_llm_judge_result_is_persisted_as_a_success_gate(tool_services) -> None:
    settings, database, workspaces = tool_services
    model = ToolModel(deterministic=False, judge_passes=False)
    sandbox = ToolSandbox([0])
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": model},
        sandbox_providers={"docker": sandbox},
    )
    task = database.create_task(TaskCreate(prompt="Build a Python greeting", max_retries=0))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        detail = await wait_terminal(database, task.id)
    finally:
        await orchestrator.stop()

    assert detail.status == TaskStatus.STOPPED
    assert detail.attempts[0].phase == "judge"
    assert detail.attempts[0].results[-1].phase == PhaseName.JUDGE
    assert detail.attempts[0].results[-1].stdout == "Greeting was missing"


@pytest.mark.asyncio
async def test_tool_loop_health_checks_http_projects_in_the_fresh_validator(tool_services) -> None:
    settings, database, workspaces = tool_services
    sandbox = ToolSandbox([0])
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": ToolModel(http=True)},
        sandbox_providers={"docker": sandbox},
    )
    task = database.create_task(TaskCreate(prompt="Build a Python health service", max_retries=0))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        detail = await wait_terminal(database, task.id)
    finally:
        await orchestrator.stop()

    assert detail.status == TaskStatus.SUCCEEDED
    assert sandbox.sessions[0].health_runs == 0
    assert sandbox.sessions[1].health_runs == 1
    assert detail.attempts[0].results[-1].phase == PhaseName.HEALTHCHECK


def test_search_urls_are_unwrapped_and_unsafe_schemes_are_rejected() -> None:
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2F"
    assert _normalize_search_url(wrapped) == "https://docs.python.org/3/"
    assert _normalize_search_url("javascript:alert(1)") is None


def test_only_explicit_dependency_installs_receive_install_network() -> None:
    assert _is_dependency_install(CommandSpec(argv=["python", "-m", "pip", "install", "httpx"]))
    assert _is_dependency_install(
        CommandSpec(argv=["/usr/local/bin/python3.12", "-m", "pip", "install", "httpx"])
    )
    assert _is_dependency_install(CommandSpec(argv=["pip3", "install", "httpx"]))
    assert _is_dependency_install(CommandSpec(argv=["pip3.12", "install", "httpx"]))
    assert _is_dependency_install(CommandSpec(argv=["uv", "pip", "install", "httpx"]))
    assert not _is_dependency_install(CommandSpec(argv=["python", "app.py"]))
    assert not _is_dependency_install(CommandSpec(argv=["python", "-m", "pip", "list"]))


def test_only_private_test_source_errors_bypass_product_code_repair() -> None:
    private_failure = FailureContext(
        phase=PhaseName.TEST,
        exit_code=1,
        timed_out=False,
        stdout_tail=(".autocoder_tests/test_app.py:4: NameError: name 'subprocess' is not defined\n"),
        stderr_tail="",
        message="test exited with code 1",
    )
    product_failure = private_failure.model_copy(
        update={"stdout_tail": "app.py:4: NameError: name 'subprocess' is not defined"}
    )

    assert _is_private_test_contract_defect(private_failure)
    assert not _is_private_test_contract_defect(product_failure)

    private_import = private_failure.model_copy(
        update={
            "stdout_tail": (
                ".autocoder_tests/test_app.py:2: in <module>\n"
                "    import test_only_dependency\n"
                "E   ModuleNotFoundError: No module named 'test_only_dependency'"
            )
        }
    )
    product_import = private_failure.model_copy(
        update={
            "stdout_tail": (
                ".autocoder_tests/test_app.py:2: in <module>\n"
                "    from app import run\n"
                "app.py:3: in <module>\n"
                "    import missing_product_dependency\n"
                "E   ModuleNotFoundError: No module named 'missing_product_dependency'"
            )
        }
    )

    assert _is_private_test_contract_defect(private_import)
    assert not _is_private_test_contract_defect(product_import)

    pytest_stdin_failure = private_failure.model_copy(
        update={
            "stdout_tail": (
                ".autocoder_tests/test_app.py:4: in test_output\n"
                "    result = sys.stdin.read()\n"
                "E   OSError: pytest: reading from stdin while output is captured!"
            )
        }
    )
    assert _is_private_test_contract_defect(pytest_stdin_failure)


@pytest.mark.asyncio
async def test_private_test_source_defect_revises_tests_without_rewriting_product_code(
    tool_services,
) -> None:
    settings, database, workspaces = tool_services
    model = PrivateTestRepairModel()
    sandbox = PrivateTestDefectSandbox([0, 1, 0])
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=workspaces,
        model_providers={"ollama": model},
        sandbox_providers={"docker": sandbox},
    )
    task = database.create_task(TaskCreate(prompt="Build a Python greeting", max_retries=1))
    await orchestrator.start()
    await orchestrator.enqueue(task.id)
    try:
        detail = await wait_terminal(database, task.id)
    finally:
        await orchestrator.stop()

    assert detail.status == TaskStatus.SUCCEEDED
    assert len(detail.attempts) == 2
    assert len(sandbox.sessions) == 3
    assert [event.tool for event in detail.attempts[1].tool_events] == ["finalize"]
    assert "Preserved product code" in detail.attempts[1].tool_events[0].label
    assert detail.attempts[1].test_contract.revision_reason
    assert model.test_failures[1] is not None
    assert ".autocoder_tests/test_app.py" in model.test_failures[1].stdout_tail
    assert workspaces.read_file(workspaces.source_path(task.id, 1), "app.py") == workspaces.read_file(
        workspaces.source_path(task.id, 2), "app.py"
    )
