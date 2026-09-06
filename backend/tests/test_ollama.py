import httpx
import pytest
from autocoder.domain import (
    AgentAction,
    CommandExecution,
    CommandSpec,
    FilePayload,
    HttpExecution,
    IntakeDecision,
    WorkspacePlan,
)
from autocoder.domain import TestContract as AcceptanceContract
from autocoder.providers.models.ollama import (
    OllamaModelProvider,
    _normalize_structured_payload,
    _validate_test_contract_sources,
)


def test_normalizes_safe_local_model_command_shorthand() -> None:
    payload = _normalize_structured_payload(
        """{
          "runtime": "python",
          "summary": "A small app",
          "files": [{"path": "app.py", "content": "print(5)"}],
          "test_command": ["python", "-m", "pytest"],
          "execution": {
            "kind": "command",
            "command": {"command": ["python", "app.py"]}
          }
        }"""
    )
    plan = WorkspacePlan.model_validate(payload)
    assert plan.test_command.argv == ["python", "-m", "pytest"]
    assert plan.execution.command.argv == ["python", "app.py"]

    string_command = _normalize_structured_payload(
        '{"action":"finalize","note":"Done",'
        '"execution":{"kind":"command","command":"python app.py --name \\"Ada Lovelace\\""}}'
    )
    action = AgentAction.model_validate(string_command)
    assert action.execution.command.argv == ["python", "app.py", "--name", "Ada Lovelace"]


def test_normalizes_finalize_command_and_http_execution_shapes() -> None:
    command_payload = _normalize_structured_payload(
        '{"action":"finalize","note":"Done","execution":["python","app.py"]}'
    )
    command_action = AgentAction.model_validate(command_payload)
    assert isinstance(command_action.execution, CommandExecution)
    assert command_action.execution.command.argv == ["python", "app.py"]

    nested_command = _normalize_structured_payload(
        '{"action":"finalize","note":"Done","execution":{"kind":"command",'
        '"command":{"command":{"argv":["python","app.py"]}}}}'
    )
    nested_action = AgentAction.model_validate(nested_command)
    assert isinstance(nested_action.execution, CommandExecution)
    assert nested_action.execution.command.argv == ["python", "app.py"]

    http_payload = _normalize_structured_payload(
        """{
          "action": "finalize",
          "note": "Serve health endpoint",
          "execution": {
            "kind": "http",
            "start_command": ["python", "app.py"],
            "port": 8000,
            "health_path": "/health"
          }
        }"""
    )
    http_action = AgentAction.model_validate(http_payload)
    assert isinstance(http_action.execution, HttpExecution)
    assert http_action.execution.start_command.argv == ["python", "app.py"]

    inferred_http = _normalize_structured_payload(
        '{"action":"finalize","note":"Serve","execution":'
        '{"start_command":"python app.py","port":8000,"health_path":"/health"}}'
    )
    inferred_http_action = AgentAction.model_validate(inferred_http)
    assert isinstance(inferred_http_action.execution, HttpExecution)

    legacy_command = _normalize_structured_payload(
        '{"action":"finalize","note":"Done","execution_command":"python app.py"}'
    )
    legacy_action = AgentAction.model_validate(legacy_command)
    assert legacy_action.execution.command.argv == ["python", "app.py"]


def test_normalizes_action_dependent_fields_from_compact_local_models() -> None:
    write_payload = _normalize_structured_payload(
        '{"action":"write_file","note":"Write app.py","path":"app.py","query":"print(42)\\n"}'
    )
    write_action = AgentAction.model_validate(write_payload)
    assert write_action.content == "print(42)\n"
    assert write_action.query is None

    run_payload = _normalize_structured_payload(
        '{"action":"run_command","note":"Run app.py","command":"python app.py"}'
    )
    run_action = AgentAction.model_validate(run_payload)
    assert run_action.argv == ["python", "app.py"]

    nested_string = _normalize_structured_payload(
        '{"action":"run_command","note":"Run app.py","argv":{"argv":"python app.py"}}'
    )
    nested_action = AgentAction.model_validate(nested_string)
    assert nested_action.argv == ["python", "app.py"]


async def test_structured_chat_accepts_object_content_from_compatible_ollama_servers(
    monkeypatch,
) -> None:
    class Response:
        @staticmethod
        def json():
            return {"message": {"content": {"ambiguous": False, "question": None}}}

    provider = OllamaModelProvider("http://ollama.invalid")
    captured = {}

    async def fake_post(client, request):
        del client
        captured.update(request)
        return Response()

    monkeypatch.setattr(provider, "_post", fake_post)
    result = await provider._structured_chat(
        model="test",
        schema=IntakeDecision,
        messages=[{"role": "user", "content": "Check ambiguity"}],
    )
    assert result.ambiguous is False
    assert captured["think"] is False
    assert captured["stream"] is False
    assert captured["options"] == {"temperature": 0}


async def test_schema_correction_enables_thinking_without_exposing_or_persisting_it(monkeypatch) -> None:
    class Response:
        def __init__(self, content):
            self.content = content

        def json(self):
            return {"message": {"thinking": "private trace", "content": self.content}}

    provider = OllamaModelProvider("http://ollama.invalid")
    think_values = []
    correction_prompts = []

    async def fake_post(client, request):
        del client
        think_values.append(request["think"])
        if len(think_values) == 1:
            return Response({"title": "IntakeDecision", "type": "object"})
        correction_prompts.append(request["messages"][-1]["content"])
        return Response({"ambiguous": False, "question": None})

    monkeypatch.setattr(provider, "_post", fake_post)

    result = await provider._structured_chat(
        model="test",
        schema=IntakeDecision,
        messages=[{"role": "user", "content": "Check ambiguity"}],
    )

    assert result.ambiguous is False
    assert think_values == [False, True]
    assert "Required JSON schema" in correction_prompts[0]
    assert '"ambiguous"' in correction_prompts[0]


async def test_schema_correction_falls_back_when_model_rejects_thinking(monkeypatch) -> None:
    class Response:
        def __init__(self, content):
            self.content = content

        def json(self):
            return {"message": {"content": self.content}}

    provider = OllamaModelProvider("http://ollama.invalid")
    think_values = []

    async def fake_post(client, request):
        del client
        think_values.append(request.get("think"))
        if len(think_values) == 1:
            return Response({"wrong": True})
        if request.get("think") is True:
            response = httpx.Response(400, text="model does not support thinking")
            raise httpx.HTTPStatusError(
                "bad request", request=httpx.Request("POST", "http://x"), response=response
            )
        return Response({"ambiguous": False, "question": None})

    monkeypatch.setattr(provider, "_post", fake_post)
    result = await provider._structured_chat(
        model="test",
        schema=IntakeDecision,
        messages=[{"role": "user", "content": "Check ambiguity"}],
    )

    assert result.ambiguous is False
    assert think_values == [False, True, False]


@pytest.mark.parametrize(
    ("path", "content", "message"),
    [
        (".autocoder_tests/check.py", "def test_ok():\n    pass\n", "test_\\*\\.py"),
        (".autocoder_tests/test_empty.py", "VALUE = 1\n", "test_\\* function"),
        (".autocoder_tests/conftest.py", "def test_ok():\n    pass\n", "conftest\\.py"),
        (
            ".autocoder_tests/test_stdin.py",
            "import sys\ndef test_output():\n    assert sys.stdin.read() == 'ok'\n",
            "cannot read pytest process stdin",
        ),
    ],
)
def test_rejects_noncollectable_or_hook_based_official_tests(path: str, content: str, message: str) -> None:
    contract = AcceptanceContract(
        deterministic=True,
        summary="Check behavior",
        expected="A pass",
        test_files=[FilePayload(path=path, content=content)],
        test_command=CommandSpec(argv=["python", "-m", "pytest", ".autocoder_tests"]),
        execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
    )

    with pytest.raises(ValueError, match=message):
        _validate_test_contract_sources(contract)


async def test_test_contract_correction_catches_missing_module_imports(monkeypatch) -> None:
    class Response:
        def __init__(self, content):
            self.content = content

        def json(self):
            return {"message": {"content": self.content}}

    provider = OllamaModelProvider("http://ollama.invalid")
    prompts = []
    base_contract = AcceptanceContract(
        deterministic=True,
        summary="Check the CLI",
        expected="The command succeeds",
        test_files=[
            FilePayload(
                path=".autocoder_tests/test_cli.py",
                content="def test_cli():\n    assert subprocess.run\n",
            )
        ],
        test_command=CommandSpec(argv=["python", "-m", "pytest", "-q", ".autocoder_tests"]),
        execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
    )

    async def fake_post(client, request):
        del client
        prompts.append(request["messages"][-1]["content"])
        contract = base_contract
        if len(prompts) == 2:
            contract = base_contract.model_copy(
                update={
                    "test_files": [
                        FilePayload(
                            path=".autocoder_tests/test_cli.py",
                            content="import subprocess\n\ndef test_cli():\n    assert subprocess.run\n",
                        )
                    ]
                }
            )
        return Response(contract.model_dump(mode="json"))

    monkeypatch.setattr(provider, "_post", fake_post)

    result = await provider._structured_chat(
        model="test",
        schema=AcceptanceContract,
        messages=[{"role": "user", "content": "Generate tests"}],
    )

    assert result.test_files[0].content.startswith("import subprocess")
    assert "undefined attribute roots: subprocess" in prompts[1]


async def test_test_contract_correction_replaces_pytest_stdin_with_product_execution(
    monkeypatch,
) -> None:
    class Response:
        def __init__(self, content):
            self.content = content

        def json(self):
            return {"message": {"content": self.content}}

    provider = OllamaModelProvider("http://ollama.invalid")
    prompts = []
    invalid = AcceptanceContract(
        deterministic=True,
        summary="Check output",
        expected="hello",
        test_files=[
            FilePayload(
                path=".autocoder_tests/test_cli.py",
                content="import sys\ndef test_cli():\n    assert sys.stdin.read() == 'hello'\n",
            )
        ],
        test_command=CommandSpec(argv=["python", "-m", "pytest", ".autocoder_tests"]),
        execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
    )
    valid = invalid.model_copy(
        update={
            "test_files": [
                FilePayload(
                    path=".autocoder_tests/test_cli.py",
                    content=(
                        "import subprocess\nimport sys\n"
                        "def test_cli():\n"
                        "    result = subprocess.run([sys.executable, 'app.py'], "
                        "capture_output=True, text=True)\n"
                        "    assert result.stdout.strip() == 'hello'\n"
                    ),
                )
            ]
        }
    )

    async def fake_post(client, request):
        del client
        prompts.append(request["messages"][-1]["content"])
        return Response((invalid if len(prompts) == 1 else valid).model_dump(mode="json"))

    monkeypatch.setattr(provider, "_post", fake_post)
    result = await provider._structured_chat(
        model="test",
        schema=AcceptanceContract,
        messages=[{"role": "user", "content": "Generate tests"}],
    )

    assert "subprocess.run" in result.test_files[0].content
    assert "cannot read pytest process stdin" in prompts[1]


async def test_transient_transport_errors_are_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ReadError("connection reset", request=request)
        return httpx.Response(200, json={"ok": True})

    provider = OllamaModelProvider("http://ollama.invalid")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await provider._post(client, {"model": "test"})

    assert response.status_code == 200
    assert calls == 3
