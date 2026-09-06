from __future__ import annotations

import json
from pathlib import Path

from autocoder.domain import AttemptPlan, FailureContext, TestContract

PYTHON_AGENT_SYSTEM_PROMPT = """You are the coding engine inside a local autonomous Python agent.
Return only data matching the supplied JSON schema. Never expose hidden reasoning. The `note` field must be
a short, user-visible action summary, not chain-of-thought. Work only with relative POSIX workspace paths.
Use tools iteratively: inspect relevant files, make focused edits, run scratch checks or a linter,
then finalize.
Do not create or modify official acceptance tests yourself. Do not access the host, Docker daemon, metadata
services, private networks, credentials, or secrets. Commands are argv arrays and must never require a shell.
"""


def intake_prompt(prompt: str, attachments: list[str]) -> str:
    return f"""Decide whether exactly one clarification is essential before implementation can begin.
Ask only when materially different interpretations would produce incompatible outcomes. Prefer proceeding
with reasonable defaults. If clarification is required, ask one concise question.

<task>{prompt}</task>
<attached_files>{json.dumps(attachments)}</attached_files>"""


def attempt_plan_prompt(
    prompt: str,
    attempt_number: int,
    workspace: Path,
    failure: FailureContext | None,
) -> str:
    failure_text = "none"
    if failure:
        failure_text = f"""phase={failure.phase.value}
exit_code={failure.exit_code}
timed_out={failure.timed_out}
message={failure.message}
stdout_tail={failure.stdout_tail}
stderr_tail={failure.stderr_tail}"""
    return f"""Create a concise implementation plan for attempt {attempt_number}. This is a Python-only task.
On a repair attempt, explicitly state how the plan responds to the previous failure. Do not write code yet.

<task>{prompt}</task>
<workspace>{_workspace_text(workspace, 45_000)}</workspace>
<previous_failure>{failure_text}</previous_failure>"""


def action_prompt(
    prompt: str,
    plan: AttemptPlan,
    workspace: Path,
    transcript: list[dict[str, str]],
) -> str:
    recent_results = transcript[-12:]
    if transcript and transcript[0].get("action") == "previous_attempt_failure":
        recent_results = [transcript[0], *transcript[-11:]]
    return f"""Choose the single next tool action. Use finalize only after the implementation files exist in
the workspace and the implementation is complete and scratch-checked. finalize.execution must be the
terminating command that executes the stored Python project and demonstrates the requested program. Never
use echo, printf, a shell, or inline `python -c` as a substitute for writing and running the requested code.
For a long-running HTTP service, finalize with its start command, port, and deterministic health path instead.
Use web_search only when current external documentation is actually needed.
On a repair attempt, the first recent_tool_results item contains the prior official failure. If that traceback
shows the private .autocoder_tests code itself is malformed while the implementation is correct, do not try to
read or edit those private files. They are intentionally absent and protected. Finalize the unchanged product
code so the following test-contract phase can make a justified, audited test revision.

<task>{prompt}</task>
<plan>{plan.model_dump_json()}</plan>
<workspace>{_workspace_text(workspace, 52_000)}</workspace>
<recent_tool_results>{json.dumps(recent_results, ensure_ascii=False)}</recent_tool_results>"""


def test_generation_prompt(
    prompt: str,
    workspace: Path,
    previous: TestContract | None,
    failure: FailureContext | None,
) -> str:
    previous_text = previous.model_dump_json() if previous else "none"
    failure_text = failure.model_dump_json() if failure else "none"
    contract_example = json.dumps(
        {
            "deterministic": True,
            "summary": "Verify the requested CLI behavior",
            "expected": "The CLI returns the exact required output and errors",
            "test_files": [
                {
                    "path": ".autocoder_tests/test_behavior.py",
                    "content": "def test_behavior():\n    assert True\n",
                }
            ],
            "test_command": {"argv": ["python", "-m", "pytest", "-q", ".autocoder_tests"]},
            "execution": {
                "kind": "command",
                "command": {"argv": ["python", "app.py"]},
            },
            "judge_criteria": None,
            "revision_reason": None,
        },
        ensure_ascii=False,
    )
    return f"""Generate the official acceptance test contract from the finished implementation and
original task.
Default to deterministic tests. Put generated tests under .autocoder_tests/ and include at least one
pytest-compatible Python file named test_*.py with real collected test functions. Set test_command to
`python -m pytest -q .autocoder_tests`; the orchestrator owns and enforces that exact checkpoint runner.
Every test_files item must include both its relative `path` and its complete executable `content`.
Test files must explicitly import every module they use; for example, subprocess.run requires
`import subprocess`. Never claim an import defect was fixed unless the revised content includes it.
Tests must exercise the finished workspace directly. For command-line programs, launch the generated
entry point with `subprocess.run(..., capture_output=True, text=True, check=False)` and assert its return
code and output. Never read `sys.stdin` or call `input()` in the pytest process as a substitute for
running the generated program; pass any required input to the product subprocess explicitly.
Use an LLM judge only when correctness cannot be determined programmatically. Tests are
fixed after the first attempt; on repairs, preserve the previous contract unless it conflicts with the
explicit task. Any revision must include revision_reason and must not weaken coverage. The official
test command must terminate. If the task began with attached existing code, add regression checks for
existing behavior affected by the change, in addition to tests for the requested new behavior.
When previous_test_failure proves that the private test code itself has a collection, import, fixture,
syntax, or task-expectation defect, correct that defect and set revision_reason to the specific evidence.
Do not set revision_reason merely because valid tests exposed a product-code failure.

The following is a shape example only. Replace its descriptions, test code, and execution command with
task-specific values while preserving every required field:
<contract_shape>{contract_example}</contract_shape>

<task>{prompt}</task>
<workspace>{_workspace_text(workspace, 65_000)}</workspace>
<previous_contract>{previous_text}</previous_contract>
<previous_test_failure>{failure_text}</previous_test_failure>"""


def judge_prompt(
    prompt: str,
    criteria: str,
    stdout: str,
    stderr: str,
    exit_code: int | None,
) -> str:
    return f"""Judge the execution strictly against the criteria. Return a pass/fail verdict and concise
reason.
<task>{prompt}</task><criteria>{criteria}</criteria><exit_code>{exit_code}</exit_code>
<stdout>{stdout[-16_384:]}</stdout><stderr>{stderr[-16_384:]}</stderr>"""


def _workspace_text(workspace: Path, max_chars: int) -> str:
    if not workspace.exists():
        return "(empty)"
    used = 0
    parts: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative.startswith((".deps/", ".npm-cache/", "__pycache__/")):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        allowance = max_chars - used
        if allowance <= 0:
            break
        block = f"<file path={json.dumps(relative)}>{content[:allowance]}</file>"
        parts.append(block)
        used += len(block)
    return "\n".join(parts) or "(empty)"
