from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from autocoder.config import Settings
from autocoder.domain import CommandSpec, HttpExecution, PhaseName, RuntimeName
from autocoder.installers import derive_install_command
from autocoder.orchestrator import OFFICIAL_TEST_COMMAND, OFFICIAL_TEST_PASS_MARKER
from autocoder.providers.sandboxes.docker import DockerSandboxProvider


def _docker_ready(images: list[str]) -> bool:
    try:
        if subprocess.run(["docker", "info"], capture_output=True, check=False, timeout=10).returncode:
            return False
        return all(
            subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                check=False,
                timeout=10,
            ).returncode
            == 0
            for image in images
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


settings = Settings()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _docker_ready([settings.python_image]),
        reason="Docker and the Python sandbox image are required",
    ),
]


async def collect_output(stream: str, chunk: str) -> None:
    del stream, chunk


@pytest.mark.asyncio
async def test_python_sandbox_tests_executes_and_loses_network(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('sandbox-python')\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("def test_arithmetic():\n    assert 2 + 2 == 4\n", encoding="utf-8")
    session = await DockerSandboxProvider(settings).create(RuntimeName.PYTHON, tmp_path)
    name = session.name
    try:
        await session.disable_network()
        tests = await session.run(
            CommandSpec(argv=["python", "-m", "pytest", "-q"]),
            phase=PhaseName.TEST,
            timeout_seconds=30,
            on_output=collect_output,
        )
        execution = await session.run(
            CommandSpec(argv=["python", "app.py"]),
            phase=PhaseName.EXECUTE,
            timeout_seconds=10,
            on_output=collect_output,
        )
        network = await session.run(
            CommandSpec(
                argv=[
                    "python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('https://example.com', timeout=2)",
                ]
            ),
            phase=PhaseName.EXECUTE,
            timeout_seconds=5,
            on_output=collect_output,
        )
        assert tests.succeeded
        written = await session.run(
            CommandSpec(
                argv=["python", "-c", "from pathlib import Path; Path('created.txt').write_text('ok')"]
            ),
            phase=PhaseName.EXECUTE,
            timeout_seconds=10,
            on_output=collect_output,
        )
        assert written.succeeded
        assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "ok"
        assert execution.succeeded and "sandbox-python" in execution.stdout
        assert not network.succeeded
    finally:
        await session.destroy()
    inspect = await asyncio.create_subprocess_exec(
        "docker",
        "container",
        "inspect",
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert await inspect.wait() != 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hostile_file", "content"),
    [
        ("pytest.py", "raise RuntimeError('candidate pytest shadow was imported')\n"),
        ("conftest.py", "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n"),
    ],
)
async def test_official_runner_ignores_candidate_pytest_hijacks(
    tmp_path: Path, hostile_file: str, content: str
) -> None:
    (tmp_path / hostile_file).write_text(content, encoding="utf-8")
    private = tmp_path / ".autocoder_tests"
    private.mkdir()
    (private / "test_failure.py").write_text("def test_failure():\n    assert False\n", encoding="utf-8")
    session = await DockerSandboxProvider(settings).create(RuntimeName.PYTHON, tmp_path)
    try:
        await session.disable_network()
        result = await session.run(
            OFFICIAL_TEST_COMMAND,
            phase=PhaseName.TEST,
            timeout_seconds=30,
            on_output=collect_output,
        )
        assert not result.succeeded
        assert f"{OFFICIAL_TEST_PASS_MARKER}0" in result.stdout
    finally:
        await session.destroy()


@pytest.mark.asyncio
async def test_official_runner_rejects_a_suite_without_a_real_pass(tmp_path: Path) -> None:
    private = tmp_path / ".autocoder_tests"
    private.mkdir()
    (private / "test_skipped.py").write_text(
        "import pytest\n@pytest.mark.skip(reason='not a pass')\ndef test_skipped():\n    pass\n",
        encoding="utf-8",
    )
    session = await DockerSandboxProvider(settings).create(RuntimeName.PYTHON, tmp_path)
    try:
        await session.disable_network()
        result = await session.run(
            OFFICIAL_TEST_COMMAND,
            phase=PhaseName.TEST,
            timeout_seconds=30,
            on_output=collect_output,
        )
        assert not result.succeeded
        assert f"{OFFICIAL_TEST_PASS_MARKER}0" in result.stdout
    finally:
        await session.destroy()


@pytest.mark.asyncio
async def test_official_runner_records_a_real_passing_test(tmp_path: Path) -> None:
    private = tmp_path / ".autocoder_tests"
    private.mkdir()
    (private / "test_ok.py").write_text("def test_ok():\n    assert 2 + 2 == 4\n", encoding="utf-8")
    session = await DockerSandboxProvider(settings).create(RuntimeName.PYTHON, tmp_path)
    try:
        await session.disable_network()
        result = await session.run(
            OFFICIAL_TEST_COMMAND,
            phase=PhaseName.TEST,
            timeout_seconds=30,
            on_output=collect_output,
        )
        assert result.succeeded
        assert f"{OFFICIAL_TEST_PASS_MARKER}1" in result.stdout
    finally:
        await session.destroy()


@pytest.mark.asyncio
async def test_python_dependencies_install_into_ephemeral_container_storage(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("packaging==25.0\n", encoding="utf-8")
    command = derive_install_command(RuntimeName.PYTHON, tmp_path)
    assert command is not None
    session = await DockerSandboxProvider(settings).create(RuntimeName.PYTHON, tmp_path)
    try:
        install = await session.run(
            command,
            phase=PhaseName.INSTALL,
            timeout_seconds=60,
            on_output=collect_output,
        )
        imported = await session.run(
            CommandSpec(
                argv=[
                    "python",
                    "-c",
                    "import packaging; print(packaging.__file__)",
                ]
            ),
            phase=PhaseName.EXECUTE,
            timeout_seconds=10,
            on_output=collect_output,
        )

        assert install.succeeded
        assert imported.succeeded
        assert "/tmp/agent-deps/packaging/" in imported.stdout.replace("\\", "/")
        assert not (tmp_path / ".deps").exists()
    finally:
        await session.destroy()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _docker_ready([settings.node_image]),
    reason="The optional Node compatibility image is not built",
)
async def test_node_sandbox_uses_builtin_test_runner(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("console.log('sandbox-node')\n", encoding="utf-8")
    (tmp_path / "app.test.js").write_text(
        "const test = require('node:test');\n"
        "const assert = require('node:assert');\n"
        "test('arithmetic', () => assert.equal(2 + 2, 4));\n",
        encoding="utf-8",
    )
    session = await DockerSandboxProvider(settings).create(RuntimeName.NODE, tmp_path)
    try:
        await session.disable_network()
        tests = await session.run(
            CommandSpec(argv=["node", "--test"]),
            phase=PhaseName.TEST,
            timeout_seconds=30,
            on_output=collect_output,
        )
        execution = await session.run(
            CommandSpec(argv=["node", "app.js"]),
            phase=PhaseName.EXECUTE,
            timeout_seconds=10,
            on_output=collect_output,
        )
        assert tests.succeeded
        assert execution.succeeded and "sandbox-node" in execution.stdout
    finally:
        await session.destroy()


@pytest.mark.asyncio
async def test_http_service_is_accepted_by_in_sandbox_healthcheck(tmp_path: Path) -> None:
    (tmp_path / "server.py").write_text(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200 if self.path == '/health' else 404)\n"
        "        self.end_headers()\n"
        "    def log_message(self, *args):\n"
        "        pass\n"
        "HTTPServer(('0.0.0.0', 8765), Handler).serve_forever()\n",
        encoding="utf-8",
    )
    session = await DockerSandboxProvider(settings).create(RuntimeName.PYTHON, tmp_path)
    try:
        await session.disable_network()
        result = await session.start_and_probe(
            HttpExecution(
                start_command=CommandSpec(argv=["python", "server.py"]),
                port=8765,
                health_path="/health",
            ),
            timeout_seconds=10,
            on_output=collect_output,
        )
        assert result.succeeded
    finally:
        await session.destroy()


@pytest.mark.asyncio
async def test_http_command_that_exits_zero_without_listening_fails_healthcheck(tmp_path: Path) -> None:
    (tmp_path / "server.py").write_text("print('finished without serving')\n", encoding="utf-8")
    session = await DockerSandboxProvider(settings).create(RuntimeName.PYTHON, tmp_path)
    try:
        await session.disable_network()
        result = await session.start_and_probe(
            HttpExecution(
                start_command=CommandSpec(argv=["python", "server.py"]),
                port=8765,
                health_path="/health",
            ),
            timeout_seconds=5,
            on_output=collect_output,
        )
        assert not result.succeeded
        assert result.exit_code == 1
        assert "before the health check succeeded" in result.stderr
    finally:
        await session.destroy()
