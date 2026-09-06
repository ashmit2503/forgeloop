from pathlib import Path

import pytest
from autocoder.config import Settings
from autocoder.domain import (
    CommandExecution,
    CommandSpec,
    FilePayload,
    RuntimeName,
    TaskCreate,
    WorkspacePatch,
    WorkspacePlan,
)
from autocoder.errors import WorkspaceValidationError
from autocoder.workspace import WorkspaceManager
from pydantic import ValidationError


def manager(tmp_path: Path, **overrides) -> WorkspaceManager:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        **overrides,
    )
    settings.ensure_directories()
    return WorkspaceManager(settings)


def test_case_alias_write_cannot_overwrite_an_existing_snapshot_file(tmp_path: Path) -> None:
    workspaces = manager(tmp_path)
    source = workspaces.create_attempt(
        "abc123", 1, attachments=[FilePayload(path="app.py", content="original")]
    )
    with pytest.raises(WorkspaceValidationError, match="conflicting workspace paths"):
        workspaces.write_file(source, FilePayload(path="App.py", content="replacement"))
    assert workspaces.read_file(source, "app.py") == "original"


def test_file_listing_enforces_limits_after_sandbox_writes(tmp_path: Path) -> None:
    workspaces = manager(tmp_path, max_file_bytes=4)
    source = workspaces.create_attempt("abc123", 1)
    (source / "large.txt").write_text("oversized", encoding="utf-8")
    with pytest.raises(WorkspaceValidationError, match="per-file size"):
        workspaces.list_files(source)


def test_disk_allowance_cannot_bypass_the_source_size_limit(tmp_path: Path) -> None:
    workspaces = manager(tmp_path, max_workspace_bytes=4)
    source = workspaces.create_attempt("abc123", 1)
    with pytest.raises(WorkspaceValidationError, match="total size"):
        workspaces.write_file(source, FilePayload(path="data.txt", content="large"), max_workspace_bytes=100)
    assert workspaces.list_files(source) == []


def plan(files: list[FilePayload]) -> WorkspacePlan:
    return WorkspacePlan(
        runtime=RuntimeName.PYTHON,
        summary="A test project",
        files=files,
        test_command=CommandSpec(argv=["python", "-m", "pytest"]),
        execution=CommandExecution(command=CommandSpec(argv=["python", "app.py"])),
    )


def test_writes_and_patches_immutable_snapshots(tmp_path: Path) -> None:
    workspaces = manager(tmp_path)
    source1 = workspaces.create_initial(
        "a0b1c2d3-e4f5-6789-abcd-ef0123456789",
        plan([FilePayload(path="app.py", content="print('one')")]),
    )
    source2 = workspaces.apply_patch(
        "a0b1c2d3-e4f5-6789-abcd-ef0123456789",
        2,
        WorkspacePatch(
            summary="Fixed output",
            upserts=[FilePayload(path="app.py", content="print('two')")],
        ),
    )

    assert workspaces.read_file(source1, "app.py") == "print('one')"
    assert workspaces.read_file(source2, "app.py") == "print('two')"
    assert [item.path for item in workspaces.list_files(source2)] == ["app.py"]


@pytest.mark.parametrize(
    "unsafe",
    ["../escape.py", "/absolute.py", "C:/windows.py", "folder\\file.py", "a/../../b.py"],
)
def test_rejects_unsafe_paths(tmp_path: Path, unsafe: str) -> None:
    with pytest.raises((WorkspaceValidationError, ValidationError)):
        manager(tmp_path).create_initial(
            "a0b1c2d3-e4f5-6789-abcd-ef0123456789",
            plan([FilePayload(path=unsafe, content="bad")]),
        )


def test_rejects_file_and_workspace_limits(tmp_path: Path) -> None:
    workspaces = manager(tmp_path, max_file_bytes=5, max_workspace_bytes=8)
    with pytest.raises(WorkspaceValidationError, match="per-file"):
        workspaces.create_initial(
            "a0b1c2d3-e4f5-6789-abcd-ef0123456789",
            plan([FilePayload(path="large.txt", content="123456")]),
        )


def test_run_workspace_does_not_mutate_source(tmp_path: Path) -> None:
    workspaces = manager(tmp_path)
    task_id = "a0b1c2d3-e4f5-6789-abcd-ef0123456789"
    source = workspaces.create_initial(task_id, plan([FilePayload(path="app.py", content="original")]))
    run = workspaces.prepare_run(task_id, 1)
    (run / "app.py").write_text("mutated", encoding="utf-8")
    assert workspaces.read_file(source, "app.py") == "original"


def test_task_attachments_cannot_claim_the_private_test_namespace() -> None:
    with pytest.raises(ValidationError, match="reserved"):
        TaskCreate(
            prompt="Fix the attached project",
            attachments=[FilePayload(path=".autocoder_tests/test_hidden.py", content="assert True")],
        )


def test_list_directory_respects_the_requested_folder(tmp_path: Path) -> None:
    workspaces = manager(tmp_path)
    source = workspaces.create_initial(
        "a0b1c2d3-e4f5-6789-abcd-ef0123456789",
        plan(
            [
                FilePayload(path="app.py", content="print('ok')"),
                FilePayload(path="pkg/__init__.py", content=""),
                FilePayload(path="pkg/tool.py", content="VALUE = 1"),
            ]
        ),
    )

    assert workspaces.list_directory(source) == ["app.py", "pkg/"]
    assert workspaces.list_directory(source, "pkg") == ["pkg/__init__.py", "pkg/tool.py"]


def test_rejected_tool_write_rolls_back_the_workspace(tmp_path: Path) -> None:
    workspaces = manager(tmp_path, max_workspace_bytes=12)
    source = workspaces.create_initial(
        "a0b1c2d3-e4f5-6789-abcd-ef0123456789",
        plan([FilePayload(path="app.py", content="original")]),
    )

    with pytest.raises(WorkspaceValidationError, match="total size"):
        workspaces.write_file(source, FilePayload(path="nested/new.py", content="123456"))
    assert workspaces.read_file(source, "app.py") == "original"
    assert [item.path for item in workspaces.list_files(source)] == ["app.py"]
    assert not (source / "nested").exists()

    with pytest.raises(WorkspaceValidationError, match="total size"):
        workspaces.write_file(source, FilePayload(path="app.py", content="too-large-now"))
    assert workspaces.read_file(source, "app.py") == "original"


def test_disposable_run_workspaces_are_removed_without_touching_source_snapshots(tmp_path: Path) -> None:
    workspaces = manager(tmp_path)
    source = workspaces.create_attempt(
        "abc123",
        1,
        attachments=[FilePayload(path="app.py", content="original")],
    )
    run = workspaces.prepare_run("abc123", 1)
    (run / "runtime-cache.bin").write_bytes(b"runtime")

    workspaces.delete_run("abc123", 1)

    assert not run.exists()
    assert workspaces.read_file(source, "app.py") == "original"


def test_startup_cleanup_removes_only_disposable_run_directories(tmp_path: Path) -> None:
    workspaces = manager(tmp_path)
    source = workspaces.create_attempt(
        "abc123",
        1,
        attachments=[FilePayload(path="app.py", content="original")],
    )
    run = workspaces.prepare_run("abc123", 1)

    workspaces.clean_stale_runs()

    assert not run.exists()
    assert source.is_dir()


def test_runtime_cleanup_removes_case_variant_private_test_directories(tmp_path: Path) -> None:
    workspaces = manager(tmp_path)
    source = workspaces.create_attempt(
        "abc123",
        1,
        attachments=[FilePayload(path="app.py", content="original")],
    )
    private_tests = source / ".AUTOCODER_TESTS"
    private_tests.mkdir()
    (private_tests / "test_fake.py").write_text("assert True", encoding="utf-8")

    workspaces.clean_runtime_artifacts(source)

    assert not private_tests.exists()
    assert workspaces.read_file(source, "app.py") == "original"
