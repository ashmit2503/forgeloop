import pytest
from autocoder.domain import (
    AgentAction,
    BenchmarkTaskDefinition,
    CommandExecution,
    CommandSpec,
    FilePayload,
    HttpExecution,
    IntakeDecision,
    RuntimeName,
    TaskCreate,
    WorkspacePatch,
    WorkspacePlan,
)
from autocoder.domain import (
    TestContract as AcceptanceTestContract,
)
from pydantic import ValidationError


def test_command_requires_bounded_argv() -> None:
    with pytest.raises(ValidationError):
        CommandSpec(argv=[])
    with pytest.raises(ValidationError):
        CommandSpec(argv=["python", ""])


def test_http_health_path_must_be_absolute() -> None:
    with pytest.raises(ValidationError):
        HttpExecution(
            start_command=CommandSpec(argv=["python", "app.py"]),
            port=8000,
            health_path="health",
        )


def test_patch_must_change_something() -> None:
    with pytest.raises(ValidationError):
        WorkspacePatch(summary="No change")


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "/absolute.py",
        "C:/host.py",
        "folder\\file.py",
        "folder//file.py",
        "folder/./file.py",
        "folder/file.py/",
    ],
)
def test_file_payload_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        FilePayload(path=path, content="unsafe")


def test_deterministic_contract_must_run_private_test_files() -> None:
    with pytest.raises(ValidationError, match="target .autocoder_tests"):
        AcceptanceTestContract(
            summary="Run checks",
            expected="Pass",
            test_files=[FilePayload(path=".autocoder_tests/test_app.py", content="assert True")],
            test_command=CommandSpec(argv=["python", "app.py"]),
            execution_command=CommandSpec(argv=["python", "app.py"]),
        )


def test_tool_actions_and_patch_deletes_reject_traversal_during_schema_validation() -> None:
    with pytest.raises(ValidationError, match="safe relative"):
        AgentAction(action="read_file", note="Inspect host", path="../secret")
    with pytest.raises(ValidationError, match="safe relative"):
        WorkspacePatch(summary="Delete host file", deletes=["../secret"])


@pytest.mark.parametrize(
    "path",
    [
        "CON.py",
        "folder/name. ",
        "folder/control\x01.py",
        "a?.py",
        "a*.py",
        'a".py',
        "a|b.py",
        "a<b.py",
        "a>b.py",
    ],
)
def test_file_paths_are_portable_across_host_filesystems(path: str) -> None:
    with pytest.raises(ValidationError, match="safe relative"):
        FilePayload(path=path, content="unsafe")


@pytest.mark.parametrize("paths", [["app", "app/main.py"], ["App/main.py", "app/util.py"]])
def test_attachments_reject_file_directory_and_directory_case_conflicts(paths: list[str]) -> None:
    from autocoder.domain import TaskCreate

    with pytest.raises(ValidationError, match="conflicting workspace paths"):
        TaskCreate(prompt="Fix this project", attachments=[FilePayload(path=p, content="") for p in paths])


def test_workspace_rejects_case_colliding_paths() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        WorkspacePlan(
            runtime=RuntimeName.PYTHON,
            summary="Collision",
            files=[
                FilePayload(path="App.py", content="print('one')"),
                FilePayload(path="app.py", content="print('two')"),
            ],
            test_command=CommandSpec(argv=["python", "-m", "pytest"]),
            execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
        )


def test_workspace_files_reject_binary_and_oversized_content() -> None:
    with pytest.raises(ValidationError, match="NUL"):
        FilePayload(path="data.txt", content="before\x00after")
    with pytest.raises(ValidationError, match="512 KiB"):
        FilePayload(path="large.txt", content="x" * (512 * 1024 + 1))


def test_tool_command_arguments_are_validated_during_model_parsing() -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        AgentAction(action="run_command", note="Run it", argv=[""])


def test_model_facing_descriptions_reject_or_normalize_whitespace_only_values() -> None:
    with pytest.raises(ValidationError, match="blank"):
        AgentAction(action="list_dir", note="   ")
    with pytest.raises(ValidationError, match="ambiguity decision"):
        IntakeDecision(ambiguous=True, question="   ")

    contract = AcceptanceTestContract(
        summary=" Check behavior ",
        expected=" Exit 0 ",
        test_files=[FilePayload(path=".autocoder_tests/test_app.py", content="assert True")],
        test_command=CommandSpec(argv=["python", "-m", "pytest", ".autocoder_tests"]),
        execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
        revision_reason="   ",
    )
    assert contract.summary == "Check behavior"
    assert contract.revision_reason is None


def test_deterministic_contract_requires_a_pytest_collectable_file() -> None:
    with pytest.raises(ValidationError, match="pytest-compatible Python file"):
        AcceptanceTestContract(
            summary="Check behavior",
            expected="The CLI prints the requested value",
            test_files=[FilePayload(path=".autocoder_tests/check.sh", content="exit 0\n")],
            test_command=CommandSpec(argv=["sh", ".autocoder_tests/check.sh"]),
            execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
        )


def test_root_list_directory_shorthand_is_normalized() -> None:
    action = AgentAction(action="list_dir", note="Inspect files", path=".")
    assert action.path is None


def test_public_task_input_rejects_blank_prompts_and_unknown_settings() -> None:
    with pytest.raises(ValidationError, match="blank"):
        TaskCreate(prompt="   ")
    with pytest.raises(ValidationError, match="at least 3"):
        TaskCreate(prompt="  a  ")
    with pytest.raises(ValidationError, match="Extra inputs"):
        TaskCreate.model_validate({"prompt": "Build a CLI", "memroy_mb": 1024})


@pytest.mark.parametrize("path", ["project.zip", "source.TAR", "bundle.tar.gz"])
def test_public_task_input_rejects_archive_attachments(path: str) -> None:
    with pytest.raises(ValidationError, match="individual text files"):
        TaskCreate(prompt="Extend this project", attachments=[FilePayload(path=path, content="text")])


def test_benchmark_import_rejects_archive_attachments_before_a_run_is_created() -> None:
    with pytest.raises(ValidationError, match="individual text files"):
        BenchmarkTaskDefinition(
            id="archive-task",
            prompt="Extend this project",
            attachments=[FilePayload(path="project.zip", content="text")],
        )
