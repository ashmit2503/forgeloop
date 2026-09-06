from __future__ import annotations

import asyncio
import contextlib
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from autocoder.config import Settings
from autocoder.database import Database, TaskRecord, utcnow
from autocoder.domain import (
    TERMINAL_STATUSES,
    AgentAction,
    AttemptPlan,
    AttemptStatus,
    Citation,
    CommandExecution,
    CommandSpec,
    ExecutionResult,
    FailureContext,
    FilePayload,
    HttpExecution,
    PhaseName,
    RuntimeName,
    SandboxLimits,
    TaskStatus,
    TestContract,
    ToolEvent,
)
from autocoder.errors import (
    AutocoderError,
    InfrastructureError,
    ModelProtocolError,
    WorkspaceValidationError,
)
from autocoder.installers import derive_install_command
from autocoder.providers.models.base import ModelProvider
from autocoder.providers.sandboxes.base import SandboxProvider, SandboxSession
from autocoder.workspace import WorkspaceManager

OFFICIAL_TEST_PASS_MARKER = "AUTOCODER_OFFICIAL_TESTS_PASSED="
_OFFICIAL_TEST_RUNNER = (
    "import os,sys; os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'; import pytest; "
    "sys.path[:0]=['/workspace','/tmp/agent-deps']; "
    'ns={}; exec("class Tracker:\\n'
    "    passed=0\\n"
    "    def pytest_runtest_logreport(self, report):\\n"
    "        if report.when == 'call' and report.passed and not hasattr(report, 'wasxfail'):\\n"
    '            self.passed += 1",ns); '
    "tracker=ns['Tracker'](); "
    "code=pytest.main(['-q','-p','no:cacheprovider','-c','/dev/null',"
    "'--confcutdir=/workspace/.autocoder_tests','/workspace/.autocoder_tests'],plugins=[tracker]); "
    "print('AUTOCODER_OFFICIAL_TESTS_PASSED='+str(tracker.passed)); "
    "sys.exit(1 if code == 0 and tracker.passed == 0 else int(code))"
)
OFFICIAL_TEST_COMMAND = CommandSpec(argv=["python", "-I", "-c", _OFFICIAL_TEST_RUNNER, ".autocoder_tests"])
_MISSING_OFFICIAL_TEST_RESULT = (
    "Official validation did not report any completed passing tests; treating the checkpoint as failed.\n"
)


class Orchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        workspaces: WorkspaceManager,
        model_providers: dict[str, ModelProvider],
        sandbox_providers: dict[str, SandboxProvider],
    ) -> None:
        self.settings = settings
        self.database = database
        self.workspaces = workspaces
        self.model_providers = model_providers
        self.sandbox_providers = sandbox_providers
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.worker: asyncio.Task[None] | None = None
        self.active_task_id: str | None = None
        self.active_job: asyncio.Task[None] | None = None
        self._stopping = False
        self._clarification_events: dict[str, asyncio.Event] = {}

    async def start(self) -> None:
        self._stopping = False
        for provider in self.sandbox_providers.values():
            with contextlib.suppress(Exception):
                await provider.cleanup_orphans()
        self.worker = asyncio.create_task(self._worker_loop(), name="autocoder-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self.active_job and not self.active_job.done():
            self.active_job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.active_job
        if self.worker:
            await self.queue.put(None)
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker
        for provider in self.sandbox_providers.values():
            with contextlib.suppress(Exception):
                await provider.cleanup_orphans()

    async def enqueue(self, task_id: str) -> None:
        await self.queue.put(task_id)
        self.database.add_event(task_id, "state", {"status": TaskStatus.QUEUED.value})

    async def cancel(self, task_id: str) -> bool:
        task = self.database.get_task(task_id)
        if not task or task.status in TERMINAL_STATUSES:
            return False
        self.database.update_task(task_id, cancel_requested=True)
        self.database.add_event(task_id, "cancellation_requested", {})
        if self.active_task_id == task_id and self.active_job and not self.active_job.done():
            self.active_job.cancel()
        else:
            self._finish_task(task_id, TaskStatus.CANCELLED, "Task cancelled before starting.")
        return True

    async def answer_clarification(self, task_id: str, answer: str) -> bool:
        task = self.database.get_task(task_id)
        if not task or task.status != TaskStatus.AWAITING_CLARIFICATION or task.clarification_answer:
            return False
        self.database.answer_clarification(task_id, answer)
        self.database.add_event(task_id, "clarification_answered", {"answer": answer})
        event = self._clarification_events.get(task_id)
        if event:
            event.set()
        return True

    async def _worker_loop(self) -> None:
        while not self._stopping:
            task_id = await self.queue.get()
            if task_id is None:
                self.queue.task_done()
                break
            self.active_task_id = task_id
            self.active_job = asyncio.create_task(self._process(task_id), name=f"task-{task_id}")
            try:
                await self.active_job
            except asyncio.CancelledError:
                # Cancellation can arrive before the child coroutine starts, so its
                # own cleanup handler may never run.
                task = self.database.get_task(task_id)
                if task and task.status not in TERMINAL_STATUSES:
                    self._finish_task(
                        task_id,
                        TaskStatus.INTERRUPTED if self._stopping else TaskStatus.CANCELLED,
                        "Application stopped." if self._stopping else "Task cancelled by the user.",
                    )
                if self._stopping:
                    break
            finally:
                self.active_job = None
                self.active_task_id = None
                self.queue.task_done()

    async def _process(self, task_id: str) -> None:
        record = self.database.get_task_record(task_id)
        if not record:
            return
        if TaskStatus(record.status) in TERMINAL_STATUSES:
            return
        if record.cancel_requested:
            self._finish_task(task_id, TaskStatus.CANCELLED, "Task was cancelled before starting.")
            return
        model_provider = self.model_providers.get(record.model_provider)
        sandbox_provider = self.sandbox_providers.get(record.sandbox_provider)
        if not model_provider or not sandbox_provider:
            self._finish_task(task_id, TaskStatus.FAILED, "Selected provider is not registered.")
            return
        await self._run_task(task_id, record, model_provider, sandbox_provider)

    async def _set_state(self, task_id: str, status: TaskStatus, *, attempt: int | None = None) -> None:
        task = self.database.get_task(task_id)
        if task and task.cancel_requested:
            raise asyncio.CancelledError
        values = {"status": status}
        if attempt is not None:
            values["active_attempt"] = attempt
        self.database.update_task(task_id, **values)
        self.database.add_event(
            task_id,
            "state",
            {"status": status.value, **({"attempt": attempt} if attempt else {})},
        )

    def _finish_task(self, task_id: str, status: TaskStatus, message: str) -> None:
        self.database.update_task(
            task_id,
            status=status,
            error=None if status == TaskStatus.SUCCEEDED else message,
            completed_at=utcnow(),
        )
        self.database.add_event(
            task_id,
            "task_complete",
            {"status": status.value, "message": message},
        )
        with contextlib.suppress(Exception):
            self.database.prune_transient_events(task_id)

    def _finish_active_attempt(
        self,
        attempt_id: str | None,
        started: float | None,
        results: list[ExecutionResult],
        status: AttemptStatus,
        message: str,
    ) -> None:
        if not attempt_id:
            return
        self.database.update_attempt(
            attempt_id,
            status=status,
            failure_message=message,
            results=results,
            completed_at=utcnow(),
            duration_ms=int((time.monotonic() - started) * 1000) if started else None,
        )

    def _failure(self, result: ExecutionResult) -> FailureContext:
        if result.timed_out:
            message = f"{result.phase.value} timed out"
        else:
            message = f"{result.phase.value} exited with code {result.exit_code}"
        return FailureContext(
            phase=result.phase,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stdout_tail=self._tail(result.stdout),
            stderr_tail=self._tail(result.stderr),
            message=message,
        )

    def _tail(self, text: str) -> str:
        encoded = text.encode("utf-8")
        return encoded[-self.settings.repair_log_tail_bytes :].decode("utf-8", errors="replace")

    async def _run_task(
        self,
        task_id: str,
        record: TaskRecord,
        model_provider: ModelProvider,
        sandbox_provider: SandboxProvider,
    ) -> None:
        source: Path | None = None
        attempt_number = 0
        active_session: SandboxSession | None = None
        active_attempt_id: str | None = None
        active_attempt_started: float | None = None
        active_results: list[ExecutionResult] = []
        try:
            attachments = [
                FilePayload.model_validate(item) for item in json.loads(record.attachments_json or "[]")
            ]
            await self._set_state(task_id, TaskStatus.INTAKE)
            intake = await model_provider.intake(
                prompt=record.prompt,
                attachments=[item.path for item in attachments],
                model=record.model,
            )
            prompt = record.prompt
            if intake.ambiguous:
                wait_event = asyncio.Event()
                self._clarification_events[task_id] = wait_event
                self.database.update_task(
                    task_id,
                    status=TaskStatus.AWAITING_CLARIFICATION,
                    clarification_question=intake.question,
                )
                self.database.add_event(
                    task_id,
                    "clarification_required",
                    {"question": intake.question},
                )
                await wait_event.wait()
                refreshed = self.database.get_task_record(task_id)
                if not refreshed or refreshed.cancel_requested:
                    raise asyncio.CancelledError
                prompt = f"{record.prompt}\n\nUser clarification: {refreshed.clarification_answer}"
                self._clarification_events.pop(task_id, None)

            if attachments:
                prompt = (
                    f"{prompt}\n\nThis task extends or fixes an attached existing codebase. "
                    "Preserve unaffected behavior and include regression coverage for behavior you touch. "
                    f"Original attached files: {', '.join(item.path for item in attachments)}"
                )

            self.database.update_task(
                task_id,
                run_started_at=utcnow(),
                resolved_runtime=RuntimeName.PYTHON,
            )
            failure: FailureContext | None = None
            previous_contract: TestContract | None = None
            for attempt_number in range(1, record.max_retries + 2):
                await self._set_state(
                    task_id,
                    TaskStatus.PLANNING if attempt_number == 1 else TaskStatus.REPAIRING,
                    attempt=attempt_number,
                )
                source = self.workspaces.create_attempt(
                    task_id,
                    attempt_number,
                    attachments=attachments if attempt_number == 1 else None,
                )
                source_before = self.workspaces.snapshot(source)
                attempt_plan = await model_provider.plan_attempt(
                    prompt=prompt,
                    attempt_number=attempt_number,
                    workspace=source,
                    failure=failure,
                    model=record.model,
                )
                attempt = self.database.create_attempt(
                    task_id,
                    attempt_number,
                    RuntimeName.PYTHON,
                    source,
                    attempt_plan.summary,
                    [],
                )
                active_attempt_id = attempt.id
                active_attempt_started = time.monotonic()
                active_results = []
                tool_events: list[ToolEvent] = []
                changed_files: list[str] = []
                self.database.update_attempt(attempt.id, plan=attempt_plan)
                self.database.add_event(
                    task_id,
                    "attempt_started",
                    {
                        "attempt": attempt_number,
                        "attempt_id": attempt.id,
                        "plan": attempt_plan.model_dump(mode="json"),
                    },
                )
                limits = SandboxLimits(
                    cpu=record.cpu_limit,
                    memory_mb=record.memory_mb,
                    disk_mb=record.disk_mb,
                )
                if previous_contract and failure and _is_private_test_contract_defect(failure):
                    execution = previous_contract.execution
                    event = ToolEvent(
                        index=1,
                        tool="finalize",
                        label="Preserved product code for a justified private-test revision",
                        detail=(
                            "The prior traceback identifies a collection or source error inside "
                            ".autocoder_tests; product code was not changed."
                        ),
                    )
                    tool_events.append(event)
                    self.database.add_event(task_id, "tool", event.model_dump(mode="json"))
                else:
                    active_session = await self._create_tool_session(
                        task_id,
                        attempt_number,
                        sandbox_provider,
                        source,
                        limits,
                    )
                    try:
                        async with asyncio.timeout(self.settings.agent_loop_timeout_seconds):
                            execution = await self._run_coding_agent(
                                task_id=task_id,
                                attempt_id=attempt.id,
                                attempt_number=attempt_number,
                                prompt=prompt,
                                plan=attempt_plan,
                                source=source,
                                session=active_session,
                                provider=model_provider,
                                model=record.model,
                                disk_mb=record.disk_mb,
                                timeout_seconds=record.execution_timeout_seconds,
                                execution_network=record.execution_network,
                                failure=failure,
                                tool_events=tool_events,
                                changed_files=changed_files,
                            )
                    except TimeoutError as exc:
                        raise ModelProtocolError(
                            "Coding agent exceeded the "
                            f"{self.settings.agent_loop_timeout_seconds}-second per-attempt safety ceiling."
                        ) from exc
                    await active_session.destroy()
                    active_session = None
                self.workspaces.clean_runtime_artifacts(source)
                self.workspaces.remove_file(source, ".autocoder_tests")
                changed_files = self.workspaces.changed_since(source, source_before)
                self.database.update_attempt(
                    attempt.id,
                    tool_events=tool_events,
                    inner_iterations=len(tool_events),
                    changed_files=changed_files,
                )
                candidate_contract = await model_provider.generate_tests(
                    prompt=prompt,
                    workspace=source,
                    previous=previous_contract,
                    failure=failure,
                    model=record.model,
                )
                contract, test_event = self._select_test_contract(
                    previous_contract,
                    candidate_contract,
                    execution,
                )
                validation_workspace = self.workspaces.prepare_run(task_id, attempt_number)
                for test_file in contract.test_files:
                    if not test_file.path.startswith(".autocoder_tests/"):
                        raise ModelProtocolError("Official tests must be placed under .autocoder_tests/.")
                    self.workspaces.write_file(
                        validation_workspace,
                        test_file,
                        max_workspace_bytes=min(
                            self.settings.max_workspace_bytes,
                            record.disk_mb * 1024 * 1024,
                        ),
                    )
                previous_contract = contract
                self.database.update_attempt(attempt.id, test_contract=contract)
                self.database.add_event(
                    task_id,
                    test_event,
                    {
                        "attempt": attempt_number,
                        "summary": contract.summary,
                        "expected": contract.expected,
                        "revision_reason": contract.revision_reason,
                    },
                )
                try:
                    active_session = await self._create_tool_session(
                        task_id,
                        attempt_number,
                        sandbox_provider,
                        validation_workspace,
                        limits,
                    )
                    failure = await self._execute_tool_attempt(
                        task_id=task_id,
                        attempt_id=attempt.id,
                        workspace=validation_workspace,
                        session=active_session,
                        contract=contract,
                        provider=model_provider,
                        model=record.model,
                        prompt=prompt,
                        execution_network=record.execution_network,
                        timeout_seconds=record.execution_timeout_seconds,
                        results=active_results,
                    )
                finally:
                    try:
                        if active_session:
                            await active_session.destroy()
                    finally:
                        with contextlib.suppress(OSError, WorkspaceValidationError):
                            self.workspaces.delete_run(task_id, attempt_number)
                    active_session = None
                duration_ms = int((time.monotonic() - active_attempt_started) * 1000)
                if failure is None:
                    final_result = active_results[-1]
                    self.database.update_attempt(
                        attempt.id,
                        status=AttemptStatus.SUCCEEDED,
                        phase=final_result.phase.value,
                        exit_code=final_result.exit_code,
                        timed_out=False,
                        results=active_results,
                        completed_at=utcnow(),
                        duration_ms=duration_ms,
                    )
                    self.database.update_task(
                        task_id,
                        status=TaskStatus.SUCCEEDED,
                        summary=attempt_plan.summary,
                        error=None,
                        completed_at=utcnow(),
                        active_attempt=attempt_number,
                    )
                    self.database.add_event(
                        task_id,
                        "task_complete",
                        {"status": TaskStatus.SUCCEEDED.value, "attempt": attempt_number},
                    )
                    with contextlib.suppress(Exception):
                        self.database.prune_transient_events(task_id)
                    active_attempt_id = None
                    return
                self.database.update_attempt(
                    attempt.id,
                    status=AttemptStatus.FAILED,
                    phase=failure.phase.value,
                    failure_message=failure.message,
                    exit_code=failure.exit_code,
                    timed_out=failure.timed_out,
                    results=active_results,
                    completed_at=utcnow(),
                    duration_ms=duration_ms,
                )
                self.database.add_event(
                    task_id,
                    "attempt_complete",
                    {
                        "status": AttemptStatus.FAILED.value,
                        "attempt": attempt_number,
                        "phase": failure.phase.value,
                        "exit_code": failure.exit_code,
                        "timed_out": failure.timed_out,
                    },
                )
                active_attempt_id = None
                if attempt_number > record.max_retries:
                    self._finish_task(
                        task_id,
                        TaskStatus.STOPPED,
                        f"Maximum attempts reached. Last failure: {failure.message}",
                    )
                    return
        except asyncio.CancelledError:
            if active_session:
                with contextlib.suppress(Exception):
                    await active_session.destroy()
            self._clarification_events.pop(task_id, None)
            status = TaskStatus.INTERRUPTED if self._stopping else TaskStatus.CANCELLED
            message = (
                "Application stopped while the task was running."
                if self._stopping
                else "Task cancelled by the user."
            )
            self._finish_active_attempt(
                active_attempt_id, active_attempt_started, active_results, AttemptStatus.CANCELLED, message
            )
            self._finish_task(task_id, status, message)
        except InfrastructureError as exc:
            if active_session:
                with contextlib.suppress(Exception):
                    await active_session.destroy()
            self._finish_active_attempt(
                active_attempt_id,
                active_attempt_started,
                active_results,
                AttemptStatus.FAILED,
                f"Infrastructure error: {exc}",
            )
            self._finish_task(task_id, TaskStatus.FAILED, f"Infrastructure error: {exc}")
        except (ModelProtocolError, AutocoderError) as exc:
            if active_session:
                with contextlib.suppress(Exception):
                    await active_session.destroy()
            self._finish_active_attempt(
                active_attempt_id, active_attempt_started, active_results, AttemptStatus.FAILED, str(exc)
            )
            self._finish_task(task_id, TaskStatus.FAILED, str(exc))
        except Exception as exc:
            if active_session:
                with contextlib.suppress(Exception):
                    await active_session.destroy()
            self._finish_active_attempt(
                active_attempt_id,
                active_attempt_started,
                active_results,
                AttemptStatus.FAILED,
                f"Unexpected orchestration error: {exc}",
            )
            self._finish_task(task_id, TaskStatus.FAILED, f"Unexpected orchestration error: {exc}")

        finally:
            self._clarification_events.pop(task_id, None)
            if attempt_number:
                with contextlib.suppress(OSError, WorkspaceValidationError):
                    self.workspaces.delete_run(task_id, attempt_number)
            if source is not None:
                with contextlib.suppress(OSError, WorkspaceValidationError):
                    self.workspaces.clean_runtime_artifacts(source)

    @staticmethod
    def _select_test_contract(
        previous: TestContract | None,
        candidate: TestContract,
        execution: CommandExecution | HttpExecution,
    ) -> tuple[TestContract, str]:
        update: dict[str, object] = {"execution": execution}
        if candidate.deterministic:
            # A model-authored command can point at a Python file that merely defines
            # tests and exits zero without executing any of them.  The orchestrator,
            # not the candidate solution, owns the official checkpoint runner.
            update["test_command"] = OFFICIAL_TEST_COMMAND
        candidate = candidate.model_copy(update=update)
        if previous is None:
            return candidate, "official_test_created"

        comparable_previous = previous.model_copy(update={"execution": execution, "revision_reason": None})
        comparable_candidate = candidate.model_copy(update={"revision_reason": None})
        changed = comparable_candidate != comparable_previous
        if changed and candidate.revision_reason:
            return candidate, "official_test_revised"
        return previous.model_copy(
            update={"execution": execution, "revision_reason": None}
        ), "official_test_reused"

    async def _run_coding_agent(
        self,
        *,
        task_id: str,
        attempt_id: str,
        attempt_number: int,
        prompt: str,
        plan: AttemptPlan,
        source: Path,
        session: SandboxSession,
        provider: ModelProvider,
        model: str,
        disk_mb: int,
        timeout_seconds: int,
        execution_network: bool,
        failure: FailureContext | None,
        tool_events: list[ToolEvent],
        changed_files: list[str],
    ) -> CommandExecution | HttpExecution:
        await self._set_state(task_id, TaskStatus.GENERATING)
        transcript: list[dict[str, str]] = []
        stream_delay_spent = 0.0
        if failure is not None:
            transcript.append(
                {
                    "action": "previous_attempt_failure",
                    "result": failure.model_dump_json(),
                }
            )
        for index in range(1, 51):
            action = await provider.next_action(
                prompt=prompt,
                plan=plan,
                workspace=source,
                transcript=transcript,
                model=model,
            )
            started = time.monotonic()
            detail = ""
            status = "passed"
            citations: list[Citation] = []
            if action.action == "read_file":
                try:
                    detail = self.workspaces.read_file(source, action.path or "")[:16_384]
                except FileNotFoundError:
                    status = "failed"
                    detail = f"Tool error: file not found: {action.path}"
            elif action.action == "list_dir":
                try:
                    detail = "\n".join(self.workspaces.list_directory(source, action.path))
                except FileNotFoundError:
                    status = "failed"
                    detail = f"Tool error: directory not found: {action.path}"
            elif action.action == "write_file":
                if (action.path or "").startswith(".autocoder_tests/"):
                    raise ModelProtocolError("The coding agent cannot modify official tests.")
                content = action.content or ""
                try:
                    self.workspaces.write_file(
                        source,
                        FilePayload(path=action.path or "", content=content),
                        max_workspace_bytes=disk_mb * 1024 * 1024,
                    )
                except (OSError, WorkspaceValidationError) as exc:
                    status = "failed"
                    detail = f"Tool error: {exc}"
                else:
                    self.database.add_event(
                        task_id,
                        "file_stream_start",
                        {"attempt": attempt_number, "path": action.path},
                    )
                    # Structured model output arrives as one validated action. Replay
                    # the authored file in a bounded number of small chunks so the
                    # editor visibly progresses instead of jumping from empty to done.
                    stream_chunk_size = _stream_chunk_size(content)
                    for offset in range(0, len(content), stream_chunk_size):
                        self.database.add_event(
                            task_id,
                            "file_stream_chunk",
                            {
                                "attempt": attempt_number,
                                "path": action.path,
                                "chunk": content[offset : offset + stream_chunk_size],
                            },
                        )
                        if offset + stream_chunk_size < len(content) and stream_delay_spent < 1.5:
                            delay = min(0.03, 1.5 - stream_delay_spent)
                            await asyncio.sleep(delay)
                            stream_delay_spent += delay
                    if action.path not in changed_files:
                        changed_files.append(action.path or "")
                    detail = f"Wrote {len(content.encode('utf-8'))} bytes"
                    self.database.add_event(
                        task_id,
                        "file_stream_complete",
                        {"attempt": attempt_number, "path": action.path},
                    )
            elif action.action in {"run_command", "run_linter"}:
                command = CommandSpec(
                    argv=action.argv
                    if action.action == "run_command"
                    else ["python", "-m", "compileall", "-q", "."]
                )
                if _is_dependency_install(command):
                    await session.enable_network()
                elif not execution_network:
                    await session.disable_network()
                result = await session.run(
                    command,
                    phase=PhaseName.EXECUTE,
                    timeout_seconds=timeout_seconds,
                    on_output=lambda stream, chunk: self._tool_output(task_id, attempt_id, stream, chunk),
                )
                detail = (
                    f"exit {result.exit_code}\n{result.stdout[-8_192:]}\n{result.stderr[-8_192:]}".strip()
                )
                status = "passed" if result.succeeded else "failed"
            elif action.action == "web_search":
                detail, citations = await self._web_search(action.query or "")
            elif action.action == "finalize":
                execution = action.execution or CommandExecution(
                    command=CommandSpec(argv=["python", "-m", "compileall", "-q", "."])
                )
                policy_error = _finalization_policy_error(execution, source)
                if policy_error:
                    status = "failed"
                    detail = f"Tool error: {policy_error}"
                else:
                    event = ToolEvent(
                        index=index,
                        tool="finalize",
                        label=action.note,
                        detail="Implementation finalized for official validation.",
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    tool_events.append(event)
                    self.database.update_attempt(attempt_id, tool_events=tool_events, inner_iterations=index)
                    self.database.add_event(task_id, "tool", event.model_dump(mode="json"))
                    return execution
            event = ToolEvent(
                index=index,
                tool=action.action,
                label=action.note,
                detail=detail,
                status=status,
                duration_ms=int((time.monotonic() - started) * 1000),
                citations=citations,
            )
            tool_events.append(event)
            transcript.append({"action": _action_for_transcript(action), "result": detail[-12_000:]})
            self.database.update_attempt(
                attempt_id,
                tool_events=tool_events,
                inner_iterations=index,
                changed_files=changed_files,
            )
            self.database.add_event(task_id, "tool", event.model_dump(mode="json"))
        raise ModelProtocolError("Coding agent exceeded the 50-tool-call safety limit without finalizing.")

    async def _create_tool_session(
        self,
        task_id: str,
        attempt_number: int,
        provider: SandboxProvider,
        workspace: Path,
        limits: SandboxLimits,
    ) -> SandboxSession:
        for infrastructure_try in range(1, 4):
            try:
                return await provider.create(RuntimeName.PYTHON, workspace, limits)
            except InfrastructureError:
                if infrastructure_try == 3:
                    raise
                self.database.add_event(
                    task_id,
                    "infrastructure_retry",
                    {"attempt": attempt_number, "retry": infrastructure_try},
                )
                await asyncio.sleep(0.35 * infrastructure_try)
        raise InfrastructureError("Sandbox creation retry loop exhausted")

    async def _tool_output(self, task_id: str, attempt_id: str, stream: str, chunk: str) -> None:
        accepted = self.database.append_attempt_log(attempt_id, stream, chunk, self.settings.max_log_bytes)
        if accepted:
            self.database.add_event(
                task_id,
                "log",
                {"attempt_id": attempt_id, "stream": stream, "chunk": accepted},
            )

    async def _web_search(self, query: str) -> tuple[str, list[Citation]]:
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                response = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
                response.raise_for_status()
            matches = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', response.text)
            citations: list[Citation] = []
            for url, title in matches:
                normalized_url = _normalize_search_url(url)
                if not normalized_url:
                    continue
                citations.append(
                    Citation(
                        title=html.unescape(re.sub("<[^>]+>", "", title))[:300],
                        url=normalized_url,
                    )
                )
                if len(citations) == 5:
                    break
            return "\n".join(f"{item.title}: {item.url}" for item in citations), citations
        except httpx.HTTPError as exc:
            return f"Search unavailable: {exc}", []

    async def _execute_tool_attempt(
        self,
        *,
        task_id: str,
        attempt_id: str,
        workspace: Path,
        session: SandboxSession,
        contract: TestContract,
        provider: ModelProvider,
        model: str,
        prompt: str,
        execution_network: bool,
        timeout_seconds: int,
        results: list[ExecutionResult],
    ) -> FailureContext | None:
        async def on_output(stream: str, chunk: str) -> None:
            await self._tool_output(task_id, attempt_id, stream, chunk)

        install_command = derive_install_command(RuntimeName.PYTHON, workspace)
        if install_command:
            await self._set_state(task_id, TaskStatus.INSTALLING)
            self.database.update_attempt(attempt_id, phase=PhaseName.INSTALL.value)
            result = await session.run(
                install_command,
                phase=PhaseName.INSTALL,
                timeout_seconds=self.settings.install_timeout_seconds,
                on_output=on_output,
            )
            results.append(result)
            if not result.succeeded:
                return self._failure(result)
        if not execution_network:
            await session.disable_network()
        if contract.deterministic:
            await self._set_state(task_id, TaskStatus.TESTING)
            self.database.update_attempt(attempt_id, phase=PhaseName.TEST.value)
            result = await session.run(
                contract.test_command or OFFICIAL_TEST_COMMAND,
                phase=PhaseName.TEST,
                timeout_seconds=timeout_seconds,
                on_output=on_output,
            )
            if result.succeeded and not _has_verified_official_test_pass(result):
                await on_output("stderr", _MISSING_OFFICIAL_TEST_RESULT)
                result = result.model_copy(
                    update={
                        "exit_code": 1,
                        "stderr": f"{result.stderr}{_MISSING_OFFICIAL_TEST_RESULT}",
                    }
                )
            results.append(result)
            if not result.succeeded:
                return self._failure(result)
        await self._set_state(task_id, TaskStatus.EXECUTING)
        self.database.update_attempt(attempt_id, phase=PhaseName.EXECUTE.value)
        if isinstance(contract.execution, CommandExecution):
            result = await session.run(
                contract.execution.command,
                phase=PhaseName.EXECUTE,
                timeout_seconds=timeout_seconds,
                on_output=on_output,
            )
        else:
            self.database.update_attempt(attempt_id, phase=PhaseName.HEALTHCHECK.value)
            result = await session.start_and_probe(
                contract.execution,
                timeout_seconds=timeout_seconds,
                on_output=on_output,
            )
        results.append(result)
        if not result.succeeded:
            return self._failure(result)
        if not contract.deterministic:
            await self._set_state(task_id, TaskStatus.TESTING)
            self.database.update_attempt(attempt_id, phase=PhaseName.JUDGE.value)
            judge_started = time.monotonic()
            verdict = await provider.judge(
                prompt=prompt,
                criteria=contract.judge_criteria or contract.expected,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                model=model,
            )
            self.database.add_event(task_id, "judge", verdict.model_dump(mode="json"))
            judge_result = ExecutionResult(
                phase=PhaseName.JUDGE,
                argv=["llm-judge"],
                exit_code=0 if verdict.passed else 1,
                stdout=verdict.reason,
                duration_ms=int((time.monotonic() - judge_started) * 1000),
            )
            results.append(judge_result)
            if not verdict.passed:
                return FailureContext(
                    phase=PhaseName.JUDGE,
                    exit_code=judge_result.exit_code,
                    timed_out=False,
                    stdout_tail=self._tail(verdict.reason),
                    stderr_tail="",
                    message=f"LLM judge rejected the result: {verdict.reason}",
                )
        return None


def _normalize_search_url(value: str) -> str | None:
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urlparse(value)
    redirect = parse_qs(parsed.query).get("uddg")
    if redirect:
        value = unquote(redirect[0])
        parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _is_dependency_install(command: CommandSpec) -> bool:
    """Identify the explicit Python package-install forms that may use network access."""
    argv = [part.casefold() for part in command.argv]
    executable = Path(argv[0]).name.removesuffix(".exe")
    if len(argv) >= 4 and (executable == "py" or re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable)):
        return argv[1:4] == ["-m", "pip", "install"]
    if len(argv) >= 2 and re.fullmatch(r"pip(?:\d+(?:\.\d+)*)?", executable):
        return argv[1] == "install"
    if len(argv) >= 3 and executable == "uv":
        return argv[1:3] == ["pip", "install"]
    return False


def _stream_chunk_size(content: str, max_chunks: int = 24) -> int:
    """Keep editor replay visible without adding unbounded delay or event volume."""
    return max(64, (len(content) + max_chunks - 1) // max_chunks)


def _action_for_transcript(action: AgentAction) -> str:
    """Record tool intent and results without duplicating authored file contents."""
    payload: dict[str, object] = {"action": action.action, "note": action.note}
    if action.path is not None:
        payload["path"] = action.path
    if action.content is not None:
        payload["content_bytes"] = len(action.content.encode("utf-8"))
    if action.argv is not None:
        payload["argv"] = action.argv
    if action.query is not None:
        payload["query"] = action.query
    if action.execution is not None:
        payload["execution"] = action.execution.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _finalization_policy_error(
    execution: CommandExecution | HttpExecution,
    workspace: Path,
) -> str | None:
    """Reject empty or output-spoofing finalizations while keeping Python entrypoints flexible."""
    runtime_directories = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
    files = []
    for item in workspace.rglob("*"):
        if not item.is_file() or item.is_symlink() or item.suffix.casefold() == ".pyc":
            continue
        relative = item.relative_to(workspace)
        if any(part.casefold() in runtime_directories for part in relative.parts):
            continue
        files.append(item)
    if not files:
        return "the workspace has no implementation files; write the requested code before finalizing"

    command = execution.command if isinstance(execution, CommandExecution) else execution.start_command
    executable = Path(command.argv[0]).name.casefold().removesuffix(".exe")
    if executable in {"echo", "printf", "true", "false", "cmd", "powershell", "pwsh", "sh", "bash"}:
        return "the final command must execute the generated Python project, not spoof output"

    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        if len(command.argv) < 2:
            return "the final Python command is missing a module or script entrypoint"
        if command.argv[1] == "-c":
            return "the final command must execute code stored in the workspace, not inline Python"
        script_arguments = [argument for argument in command.argv[1:] if argument.casefold().endswith(".py")]
        for argument in script_arguments:
            pure = Path(argument)
            if pure.is_absolute() or ".." in pure.parts:
                return "the final Python script must be a safe relative workspace path"
            target = (workspace / pure).resolve()
            if not target.is_relative_to(workspace.resolve()) or not target.is_file():
                return f"the final Python script does not exist in the workspace: {argument}"
        if "-m" not in command.argv[1:] and not script_arguments:
            return "the final Python command must name an existing script or module"
    return None


def _has_verified_official_test_pass(result: ExecutionResult) -> bool:
    """Require the orchestrator-owned pytest runner to confirm a real passing test call."""
    matches = re.findall(
        rf"{re.escape(OFFICIAL_TEST_PASS_MARKER)}(\d+)",
        f"{result.stdout}\n{result.stderr}",
    )
    # The runner emits its count after pytest returns. Earlier matching text can
    # come from the candidate program's own output and must not spoof a pass.
    return bool(matches) and int(matches[-1]) > 0


def _is_private_test_contract_defect(failure: FailureContext) -> bool:
    """Recognize narrow pytest failures that originate in the generated private test source."""
    if failure.phase != PhaseName.TEST:
        return False
    output = f"{failure.stdout_tail}\n{failure.stderr_tail}"
    test_location = r"\.autocoder_tests/[\w./-]+(?::\d+)?"
    source_error = rf"{test_location}[^\n]*(?:NameError|SyntaxError)"
    summary_error = rf"{test_location}:\d+:\s*(?:NameError|SyntaxError)"
    collection_error = (
        r"(?:no tests ran|collected 0 items|fixture ['\"][^'\"]+['\"] not found|"
        r"pytest: reading from stdin while output is captured)"
    )
    if bool(
        re.search(source_error, output, flags=re.IGNORECASE)
        or re.search(summary_error, output, flags=re.IGNORECASE)
        or re.search(collection_error, output, flags=re.IGNORECASE)
    ):
        return True

    # Import failures belong to the test contract only when the final traceback
    # source before the exception is itself a private test. If product code is the
    # final frame, the coding agent must repair the product instead.
    for match in re.finditer(r"(?:ModuleNotFoundError|ImportError)\b", output):
        traceback_prefix = output[max(0, match.start() - 8_000) : match.start()]
        locations = re.findall(r"([^\s:]+\.py):\d+(?::|\s)", traceback_prefix)
        if locations and ".autocoder_tests/" in locations[-1].replace("\\", "/"):
            return True
    return False
