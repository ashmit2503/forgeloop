import asyncio
from pathlib import Path

import pytest
from autocoder.config import Settings
from autocoder.domain import RuntimeName, SandboxLimits
from autocoder.errors import InfrastructureError
from autocoder.providers.sandboxes import docker as docker_module
from autocoder.providers.sandboxes.docker import (
    DockerSandboxSession,
    _directory_exceeds_limit,
    _docker_infrastructure_detail,
    _healthcheck_exit_code,
)


async def test_docker_create_applies_resource_and_security_boundaries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    async def fake_control(args: list[str], timeout: int) -> tuple[int, str, str]:
        del timeout
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(docker_module, "_run_control", fake_control)
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    session = await DockerSandboxSession.create(
        settings,
        RuntimeName.PYTHON,
        tmp_path,
        SandboxLimits(cpu=0.5, memory_mb=256, disk_mb=64),
    )

    create = calls[0]
    assert create[:2] == ["docker", "create"]
    assert create[create.index("--cpus") + 1] == "0.5"
    assert create[create.index("--memory") + 1] == "256m"
    assert create[create.index("--memory-swap") + 1] == "256m"
    labels = [create[index + 1] for index, value in enumerate(create) if value == "--label"]
    assert "com.codingsandbox.managed=true" in labels
    assert any(label.startswith("com.codingsandbox.owner=") for label in labels)
    assert "--init" in create
    assert "--read-only" in create
    assert create[create.index("--cap-drop") + 1] == "ALL"
    assert create[create.index("--security-opt") + 1] == "no-new-privileges"
    assert "--pids-limit" not in create
    assert not any("docker.sock" in argument for argument in create)
    assert "PIP_TARGET=/tmp/agent-deps" in create
    assert "PYTHONPATH=/tmp/agent-deps" in create
    assert "npm_config_cache=/tmp/npm-cache" in create
    assert not any("/workspace/.deps" in argument for argument in create)

    await session.destroy()


async def test_provider_cleanup_targets_only_owned_codingsandbox_containers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    async def fake_control(args: list[str], timeout: int) -> tuple[int, str, str]:
        del timeout
        calls.append(args)
        return (0, "container-one\ncontainer-two\n", "") if args[1:3] == ["ps", "-aq"] else (0, "", "")

    monkeypatch.setattr(docker_module, "_run_control", fake_control)
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")

    await docker_module.DockerSandboxProvider(settings).cleanup_orphans()

    assert calls[0][:4] == ["docker", "ps", "-aq", "--filter"]
    assert "label=com.codingsandbox.managed=true" in calls[0]
    assert any(value.startswith("label=com.codingsandbox.owner=") for value in calls[0])
    assert calls[1] == ["docker", "rm", "-f", "container-one", "container-two"]


async def test_docker_create_removes_partial_container_when_start_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    async def fake_control(args: list[str], timeout: int) -> tuple[int, str, str]:
        del timeout
        calls.append(args)
        if args[1] == "start":
            return 1, "", "daemon refused to start the container"
        return 0, "", ""

    monkeypatch.setattr(docker_module, "_run_control", fake_control)
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")

    with pytest.raises(InfrastructureError, match="could not start"):
        await DockerSandboxSession.create(settings, RuntimeName.PYTHON, tmp_path)

    assert calls[-1][1:3] == ["rm", "-f"]


async def test_stream_capture_truncates_without_suppressing_the_overflow_signal(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        max_log_bytes=4,
    )
    session = DockerSandboxSession(
        settings,
        RuntimeName.PYTHON,
        tmp_path,
        "sandbox",
        SandboxLimits(),
    )
    reader = asyncio.StreamReader()
    reader.feed_data(b"12345")
    reader.feed_eof()
    captured: list[str] = []
    emitted: list[tuple[str, str]] = []

    truncated = await session._read_stream(
        reader,
        "stdout",
        captured,
        lambda stream, chunk: _record_output(emitted, stream, chunk),
    )

    assert truncated is True
    assert captured == ["2345"]
    assert emitted == [("stdout", "12345")]


async def test_transient_cleanup_failure_is_retried_before_marking_the_session_destroyed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = 0

    async def flaky_control(args: list[str], timeout: int) -> tuple[int, str, str]:
        nonlocal calls
        del args, timeout
        calls += 1
        return (1, "", "daemon unavailable") if calls == 1 else (0, "", "")

    monkeypatch.setattr(docker_module, "_run_control", flaky_control)
    session = DockerSandboxSession(
        Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'db.sqlite'}"),
        RuntimeName.PYTHON,
        tmp_path,
        "sandbox",
        SandboxLimits(),
    )

    await session.destroy()
    assert session.destroyed
    assert calls == 2


async def _record_output(target: list[tuple[str, str]], stream: str, chunk: str) -> None:
    target.append((stream, chunk))


def test_workspace_disk_limit_counts_nested_regular_files(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "data.bin").write_bytes(b"12345")

    assert _directory_exceeds_limit(tmp_path, 4) is True
    assert _directory_exceeds_limit(tmp_path, 5) is False


def test_workspace_disk_scan_ignores_files_removed_by_a_running_test(monkeypatch) -> None:
    class VanishedEntry:
        path = "vanished"

        @staticmethod
        def is_symlink() -> bool:
            return False

        @staticmethod
        def is_dir(*, follow_symlinks: bool) -> bool:
            del follow_symlinks
            return False

        @staticmethod
        def is_file(*, follow_symlinks: bool) -> bool:
            del follow_symlinks
            return True

        @staticmethod
        def stat(*, follow_symlinks: bool):
            del follow_symlinks
            raise FileNotFoundError

    class ScanResult:
        def __enter__(self):
            return iter([VanishedEntry()])

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(docker_module.os, "scandir", lambda _directory: ScanResult())

    assert _directory_exceeds_limit(Path("workspace"), 1) is False


def test_docker_daemon_errors_are_classified_as_infrastructure_failures() -> None:
    assert _docker_infrastructure_detail(
        "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine"
    )
    assert _docker_infrastructure_detail("Error response from daemon: Container abc is not running")
    assert _docker_infrastructure_detail("application failed its assertion") is None
    assert _docker_infrastructure_detail("application is not running") is None


def test_clean_http_process_exit_cannot_false_pass_an_unhealthy_service() -> None:
    assert _healthcheck_exit_code(healthy=True, disk_exceeded=False, process_returncode=0) == 0
    assert _healthcheck_exit_code(healthy=False, disk_exceeded=True, process_returncode=0) == 137
    assert _healthcheck_exit_code(healthy=False, disk_exceeded=False, process_returncode=0) == 1
    assert _healthcheck_exit_code(healthy=False, disk_exceeded=False, process_returncode=42) == 42
    assert _healthcheck_exit_code(healthy=False, disk_exceeded=False, process_returncode=None) is None
