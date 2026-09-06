from __future__ import annotations

import ast
import asyncio
import builtins
import json
import shlex
from pathlib import PurePath
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from autocoder.domain import (
    AgentAction,
    AttemptPlan,
    FailureContext,
    IntakeDecision,
    JudgeVerdict,
    TestContract,
)
from autocoder.errors import InfrastructureError, ModelProtocolError
from autocoder.providers.models.base import ModelProvider
from autocoder.providers.models.prompts import (
    PYTHON_AGENT_SYSTEM_PROMPT,
    action_prompt,
    attempt_plan_prompt,
    intake_prompt,
    judge_prompt,
    test_generation_prompt,
)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class OllamaModelProvider(ModelProvider):
    name = "ollama"

    def __init__(self, base_url: str, timeout_seconds: float = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def intake(self, *, prompt: str, attachments: list[str], model: str) -> IntakeDecision:
        return await self._agent_chat(model, IntakeDecision, intake_prompt(prompt, attachments))

    async def plan_attempt(
        self,
        *,
        prompt: str,
        attempt_number: int,
        workspace,
        failure: FailureContext | None,
        model: str,
    ) -> AttemptPlan:
        return await self._agent_chat(
            model,
            AttemptPlan,
            attempt_plan_prompt(prompt, attempt_number, workspace, failure),
        )

    async def next_action(
        self,
        *,
        prompt: str,
        plan: AttemptPlan,
        workspace,
        transcript: list[dict[str, str]],
        model: str,
    ) -> AgentAction:
        return await self._agent_chat(
            model,
            AgentAction,
            action_prompt(prompt, plan, workspace, transcript),
        )

    async def generate_tests(
        self,
        *,
        prompt: str,
        workspace,
        previous: TestContract | None,
        failure: FailureContext | None,
        model: str,
    ) -> TestContract:
        return await self._agent_chat(
            model,
            TestContract,
            test_generation_prompt(prompt, workspace, previous, failure),
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
        return await self._agent_chat(
            model,
            JudgeVerdict,
            judge_prompt(prompt, criteria, stdout, stderr, exit_code),
        )

    async def _agent_chat(self, model: str, schema: type[SchemaT], prompt: str) -> SchemaT:
        return await self._structured_chat(
            model=model,
            schema=schema,
            messages=[
                {"role": "system", "content": PYTHON_AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )

    async def _structured_chat(
        self,
        *,
        model: str,
        schema: type[SchemaT],
        messages: list[dict[str, str]],
    ) -> SchemaT:
        request = {
            "model": model,
            "messages": messages,
            "stream": False,
            # Thinking-capable local models enable hidden reasoning by default.
            # Structured tool actions need only the validated final JSON.
            "think": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": 0},
        }
        invalid_content = ""
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for validation_attempt in range(2):
                try:
                    response = await self._post_compatible(client, request)
                except httpx.ConnectError as exc:
                    raise InfrastructureError(
                        f"Ollama is unavailable at {self.base_url}. Start Ollama and install {model}."
                    ) from exc
                except httpx.TimeoutException as exc:
                    raise InfrastructureError("Ollama timed out after transient retries.") from exc
                except httpx.RequestError as exc:
                    raise InfrastructureError(
                        f"Ollama connection failed after transient retries: {exc}"
                    ) from exc
                except httpx.HTTPStatusError as exc:
                    if (
                        exc.response.status_code == 400
                        and "parse grammar" in exc.response.text.lower()
                        and request["format"] != "json"
                    ):
                        request["format"] = "json"
                        request["messages"] = [
                            *messages,
                            {
                                "role": "user",
                                "content": (
                                    "Your response must be a JSON object matching this schema exactly:\n"
                                    f"{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"
                                ),
                            },
                        ]
                        try:
                            response = await self._post_compatible(client, request)
                        except httpx.HTTPError as fallback_exc:
                            raise InfrastructureError(
                                "Ollama rejected both JSON-schema and JSON compatibility modes."
                            ) from fallback_exc
                    else:
                        detail = exc.response.text[:1000]
                        raise InfrastructureError(
                            f"Ollama returned HTTP {exc.response.status_code}: {detail}"
                        ) from exc

                try:
                    body = response.json()
                    raw_content = body["message"]["content"]
                    invalid_content = (
                        raw_content
                        if isinstance(raw_content, str)
                        else json.dumps(raw_content, ensure_ascii=False)
                    )
                    validated = schema.model_validate(_normalize_structured_payload(invalid_content))
                    if isinstance(validated, TestContract):
                        _validate_test_contract_sources(validated)
                    return validated
                except (KeyError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                    if validation_attempt == 1:
                        raise ModelProtocolError(
                            f"Ollama did not return a valid {schema.__name__} after schema correction: {exc}"
                        ) from exc
                    # Fast-path thinking is disabled for valid structured replies.
                    # If a thinking-capable model cannot satisfy the schema, allow
                    # reasoning only for the single correction retry. The separate
                    # reasoning field is never read, stored, or exposed.
                    request["think"] = True
                    request["messages"] = [
                        *request["messages"],
                        {"role": "assistant", "content": invalid_content[:8000]},
                        {
                            "role": "user",
                            "content": (
                                "Your response failed JSON-schema validation. Return a corrected JSON object "
                                "matching the supplied schema exactly, with no Markdown or commentary. "
                                "Every required property and every nested file content must be present. "
                                f"Validation error: {exc}\n"
                                "Required JSON schema:\n"
                                f"{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"
                            ),
                        },
                    ]
        raise ModelProtocolError(f"Unable to parse {schema.__name__}")

    async def _post_compatible(self, client: httpx.AsyncClient, request: dict) -> httpx.Response:
        """Retry 400s caused only by Ollama versions/models that reject `think`."""
        for _ in range(3):
            try:
                return await self._post(client, request)
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code != 400
                    or "think" not in request
                    or not _is_thinking_compatibility_error(exc.response.text)
                ):
                    raise
                if request["think"] is True:
                    request["think"] = False
                else:
                    request.pop("think", None)
        raise InfrastructureError("Ollama thinking compatibility retry loop exhausted.")

    async def _post(self, client: httpx.AsyncClient, request: dict) -> httpx.Response:
        for transient_attempt in range(3):
            try:
                response = await client.post(f"{self.base_url}/api/chat", json=request)
                response.raise_for_status()
                return response
            except httpx.ConnectError:
                raise
            except httpx.TimeoutException:
                if transient_attempt == 2:
                    raise
            except httpx.TransportError:
                if transient_attempt == 2:
                    raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {429, 500, 502, 503, 504} or transient_attempt == 2:
                    raise
            await asyncio.sleep(0.35 * (transient_attempt + 1))
        raise httpx.TimeoutException("Ollama request retry loop exhausted")


def _normalize_structured_payload(content: str) -> object:
    """Normalize safe command-array shorthand used by smaller local models."""
    payload = json.loads(content)
    if not isinstance(payload, dict):
        return payload

    if "test_command" in payload:
        payload["test_command"] = _normalize_command(payload["test_command"])
    if "execution_command" in payload:
        normalized_command = _normalize_command(payload.pop("execution_command"))
        if "execution" not in payload:
            payload["execution"] = {"kind": "command", "command": normalized_command}
    if "execution" in payload:
        payload["execution"] = _normalize_execution(payload["execution"])
    if payload.get("action") == "write_file" and payload.get("content") is None:
        # Some compact local models place file text in another optional string
        # property even though the requested action is unambiguous.
        for alias in ("file_content", "code", "text", "query"):
            candidate = payload.get(alias)
            if isinstance(candidate, str):
                payload["content"] = candidate
                payload.pop(alias, None)
                break
    if payload.get("action") == "run_command" and not isinstance(payload.get("argv"), list):
        for alias in ("argv", "command", "args", "query"):
            candidate = payload.get(alias)
            normalized = _normalize_command(candidate)
            if isinstance(normalized, dict) and set(normalized) == {"argv"}:
                if alias != "argv":
                    payload.pop(alias, None)
                payload["argv"] = normalized["argv"]
                break
    return payload


def _validate_test_contract_sources(contract: TestContract) -> None:
    """Reject obvious syntax and unresolved-name defects in model-authored Python tests."""
    if not contract.deterministic:
        return
    python_files = [file for file in contract.test_files if file.path.casefold().endswith(".py")]
    if any(PurePath(file.path).name.casefold() == "conftest.py" for file in python_files):
        raise ValueError("Official tests cannot define conftest.py hooks.")
    collectable = [file for file in python_files if PurePath(file.path).name.casefold().startswith("test_")]
    if not collectable:
        raise ValueError("Official tests require at least one collectable test_*.py file.")

    builtin_names = set(dir(builtins))
    has_test_function = False
    for file in contract.test_files:
        if not file.path.casefold().endswith(".py"):
            continue
        try:
            tree = ast.parse(file.content, filename=file.path)
        except SyntaxError as exc:
            raise ValueError(
                f"Official test {file.path} has invalid Python syntax at line {exc.lineno}: {exc.msg}"
            ) from exc

        if file in collectable and any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
            for node in ast.walk(tree)
        ):
            has_test_function = True

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "input":
                raise ValueError(
                    f"Official test {file.path} cannot read pytest process stdin; execute the product "
                    "with subprocess.run and pass task input explicitly."
                )
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"read", "readline", "readlines"}
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "stdin"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "sys"
            ):
                raise ValueError(
                    f"Official test {file.path} cannot read pytest process stdin; execute the product "
                    "with subprocess.run and capture its output."
                )

        defined = set(builtin_names)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defined.update(argument.arg for argument in node.args.args)
                    defined.update(argument.arg for argument in node.args.kwonlyargs)
                    if node.args.vararg:
                        defined.add(node.args.vararg.arg)
                    if node.args.kwarg:
                        defined.add(node.args.kwarg.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    defined.add(alias.asname or alias.name.split(".", 1)[0])
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined.add(node.id)

        unresolved_roots: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            root: ast.expr = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id not in defined:
                unresolved_roots.add(root.id)
        if unresolved_roots:
            names = ", ".join(sorted(unresolved_roots))
            raise ValueError(
                f"Official test {file.path} uses undefined attribute roots: {names}. "
                "Import or define every referenced module/object."
            )
    if not has_test_function:
        raise ValueError("Official tests require at least one test_* function.")


def _normalize_command(value: object, depth: int = 0) -> object:
    if depth > 4:
        return value
    if isinstance(value, str):
        try:
            argv = shlex.split(value, posix=True)
        except ValueError:
            return value
        return {"argv": argv} if argv else value
    if isinstance(value, list):
        return {"argv": value}
    if isinstance(value, dict):
        for key in ("argv", "command", "args"):
            candidate = value.get(key)
            if isinstance(candidate, (str, list, dict)):
                normalized = _normalize_command(candidate, depth + 1)
                if isinstance(normalized, dict) and set(normalized) == {"argv"}:
                    return normalized
    return value


def _normalize_execution(value: object) -> object:
    if isinstance(value, str):
        return {"kind": "command", "command": _normalize_command(value)}
    if isinstance(value, list):
        return {"kind": "command", "command": _normalize_command(value)}
    if not isinstance(value, dict):
        return value
    if "start_command" in value and "port" in value:
        return {
            **value,
            "kind": "http",
            "start_command": _normalize_command(value["start_command"]),
        }
    if value.get("kind") == "command" and "command" in value:
        return {**value, "command": _normalize_command(value["command"])}
    if value.get("kind") == "http" and "start_command" in value:
        return {**value, "start_command": _normalize_command(value["start_command"])}
    if any(key in value for key in ("argv", "command", "args")):
        return {"kind": "command", "command": _normalize_command(value)}
    return value


def _is_thinking_compatibility_error(detail: str) -> bool:
    normalized = detail.casefold()
    return "think" in normalized and any(
        phrase in normalized
        for phrase in ("not support", "unsupported", "unknown", "unrecognized", "invalid field")
    )
