from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import time
import uuid
from pathlib import Path

from autocoder.config import Settings
from autocoder.domain import (
    CommandSpec,
    ExecutionResult,
    HttpExecution,
    PhaseName,
    RuntimeName,
    SandboxCapabilities,
    SandboxLimits,
)
from autocoder.errors import InfrastructureError, SandboxExecutionError
from autocoder.providers.sandboxes.base import OutputCallback, SandboxProvider, SandboxSession


class DockerSandboxSession(SandboxSession):
    def __init__(
        self,
        settings: Settings,
        runtime: RuntimeName,
        workspace: Path,
        name: str,
        limits: SandboxLimits,
    ) -> None:
        self.settings = settings
        self.runtime = runtime
        self.workspace = workspace
        self.name = name
        self.workspace_limit_bytes = limits.disk_mb * 1024 * 1024
        self.destroyed = False
        self.network_disabled = False
        self._processes: set[asyncio.subprocess.Process] = set()

    @classmethod
    async def create(
        cls,
        settings: Settings,
        runtime: RuntimeName,
        workspace: Path,
        limits: SandboxLimits | None = None,
    ) -> DockerSandboxSession:
        name = f"autocoder-{uuid.uuid4().hex[:12]}"
        image = settings.python_image if runtime == RuntimeName.PYTHON else settings.node_image
        effective = limits or SandboxLimits(
            cpu=settings.docker_cpu_limit,
            memory_mb=_memory_megabytes(settings.docker_memory),
        )
        args = [
            "docker",
            "create",
            "--name",
            name,
            "--label",
            "com.codingsandbox.managed=true",
            "--label",
            _owner_label(settings),
            "--cpus",
            str(effective.cpu),
            "--memory",
            f"{effective.memory_mb}m",
            "--memory-swap",
            f"{effective.memory_mb}m",
            "--init",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,size={effective.disk_mb}m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            _sandbox_user(),
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={workspace.resolve()},target=/workspace",
            "--env",
            "HOME=/tmp/agent-home",
            "--env",
            "PYTHONPATH=/tmp/agent-deps",
            "--env",
            "PIP_TARGET=/tmp/agent-deps",
            "--env",
            "PATH=/tmp/agent-deps/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
            "--env",
            "npm_config_cache=/tmp/npm-cache",
            image,
            "tail",
            "-f",
            "/dev/null",
        ]
        try:
            code, _, stderr = await _run_control(args, timeout=30)
            if code != 0:
                raise InfrastructureError(
                    f"Docker could not create the sandbox from {image}: {stderr.strip()}"
                )
            code, _, stderr = await _run_control(["docker", "start", name], timeout=30)
            if code != 0:
                raise InfrastructureError(f"Docker could not start the sandbox: {stderr.strip()}")
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await asyncio.shield(_run_control(["docker", "rm", "-f", name], timeout=15))
            raise
        except OSError as exc:
            with contextlib.suppress(Exception):
                await _run_control(["docker", "rm", "-f", name], timeout=15)
            raise InfrastructureError(f"Docker CLI could not create the sandbox: {exc}") from exc
        except InfrastructureError:
            with contextlib.suppress(Exception):
                await _run_control(["docker", "rm", "-f", name], timeout=15)
            raise
        return cls(settings, runtime, workspace, name, effective)

    async def run(
        self,
        command: CommandSpec,
        *,
        phase: PhaseName,
        timeout_seconds: int,
        on_output: OutputCallback,
    ) -> ExecutionResult:
        args = ["docker", "exec", "--workdir", "/workspace"]
        if self.runtime == RuntimeName.PYTHON:
            args.extend(["--env", "PYTHONPATH=/tmp/agent-deps"])
        else:
            args.extend(["--env", "npm_config_cache=/tmp/npm-cache"])
        args.extend([self.name, *command.argv])
        return await self._run_streaming(
            args,
            display_argv=command.argv,
            phase=phase,
            timeout_seconds=timeout_seconds,
            on_output=on_output,
        )

    async def disable_network(self) -> None:
        if self.network_disabled:
            return
        code, _, stderr = await _run_control(
            ["docker", "network", "disconnect", "bridge", self.name], timeout=15
        )
        if code != 0 and "is not connected" not in stderr.lower():
            raise SandboxExecutionError(f"Could not disable sandbox network: {stderr.strip()}")
        self.network_disabled = True

    async def enable_network(self) -> None:
        if not self.network_disabled:
            return
        code, _, stderr = await _run_control(
            ["docker", "network", "connect", "bridge", self.name], timeout=15
        )
        if code != 0 and "already exists" not in stderr.lower():
            raise InfrastructureError(f"Could not restore sandbox install network: {stderr.strip()}")
        self.network_disabled = False

    async def start_and_probe(
        self,
        execution: HttpExecution,
        *,
        timeout_seconds: int,
        on_output: OutputCallback,
    ) -> ExecutionResult:
        started = time.monotonic()
        command = execution.start_command
        args = ["docker", "exec", "--workdir", "/workspace"]
        if self.runtime == RuntimeName.PYTHON:
            args.extend(["--env", "PYTHONPATH=/tmp/agent-deps"])
        else:
            args.extend(["--env", "npm_config_cache=/tmp/npm-cache"])
        args.extend([self.name, *command.argv])

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            raise InfrastructureError(f"Docker could not start the HTTP process: {exc}") from exc
        self._processes.add(process)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        stdout_task = asyncio.create_task(
            self._read_stream(process.stdout, "stdout", stdout_parts, on_output)
        )
        stderr_task = asyncio.create_task(
            self._read_stream(process.stderr, "stderr", stderr_parts, on_output)
        )
        healthy = False
        timed_out = False
        disk_exceeded = False
        try:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if process.returncode is not None:
                    break
                if await asyncio.to_thread(
                    _directory_exceeds_limit,
                    self.workspace,
                    self.workspace_limit_bytes,
                ):
                    disk_exceeded = True
                    break
                probe = self._probe_command(execution.port, execution.health_path)
                probe_result = await self.run(
                    probe,
                    phase=PhaseName.HEALTHCHECK,
                    timeout_seconds=3,
                    on_output=_discard_output,
                )
                if probe_result.succeeded:
                    healthy = True
                    break
                await asyncio.sleep(0.5)
            if not healthy and not disk_exceeded and process.returncode is None:
                timed_out = True
        finally:
            if process.returncode is None:
                await _run_control(["docker", "kill", self.name], timeout=10)
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (TimeoutError, ProcessLookupError):
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    with contextlib.suppress(ProcessLookupError, asyncio.TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=3)
            stream_results = await asyncio.gather(
                stdout_task,
                stderr_task,
                return_exceptions=True,
            )
            self._processes.discard(process)

        _raise_stream_failures(stream_results)

        if disk_exceeded:
            message = f"Workspace disk limit of {self.workspace_limit_bytes // (1024 * 1024)} MiB exceeded.\n"
            stderr_parts.append(message)
            await on_output("stderr", message)
        elif not healthy and not timed_out and process.returncode is not None:
            message = (
                f"HTTP process exited before the health check succeeded (exit code {process.returncode}).\n"
            )
            stderr_parts.append(message)
            await on_output("stderr", message)
        stderr = "".join(stderr_parts)
        infrastructure_detail = _docker_infrastructure_detail(stderr)
        if (
            not healthy
            and process.returncode not in {None, 0}
            and infrastructure_detail
            and not timed_out
            and not disk_exceeded
        ):
            raise InfrastructureError(infrastructure_detail)
        return ExecutionResult(
            phase=PhaseName.HEALTHCHECK,
            argv=command.argv,
            exit_code=_healthcheck_exit_code(healthy, disk_exceeded, process.returncode),
            stdout="".join(stdout_parts),
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=int((time.monotonic() - started) * 1000),
            truncated=any(result is True for result in stream_results),
        )

    async def destroy(self) -> None:
        if self.destroyed:
            return
        for process in list(self._processes):
            if process.returncode is None:
                process.kill()
        last_error = ""
        for cleanup_try in range(3):
            try:
                code, _, stderr = await _run_control(["docker", "rm", "-f", self.name], timeout=20)
            except FileNotFoundError as exc:
                raise InfrastructureError("Docker CLI disappeared before sandbox cleanup.") from exc
            if code == 0 or "no such container" in stderr.casefold():
                self.destroyed = True
                return
            last_error = stderr.strip()
            if cleanup_try < 2:
                await asyncio.sleep(0.25 * (cleanup_try + 1))
        raise InfrastructureError(f"Docker could not remove sandbox {self.name}: {last_error}")

    async def _run_streaming(
        self,
        args: list[str],
        *,
        display_argv: list[str],
        phase: PhaseName,
        timeout_seconds: int,
        on_output: OutputCallback,
    ) -> ExecutionResult:
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            raise InfrastructureError(f"Docker CLI could not start: {exc}") from exc
        self._processes.add(process)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        stdout_task = asyncio.create_task(
            self._read_stream(process.stdout, "stdout", stdout_parts, on_output)
        )
        stderr_task = asyncio.create_task(
            self._read_stream(process.stderr, "stderr", stderr_parts, on_output)
        )
        timed_out = False
        disk_exceeded = False
        wait_task = asyncio.create_task(process.wait())
        try:
            deadline = time.monotonic() + timeout_seconds
            while not wait_task.done():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                done, _ = await asyncio.wait({wait_task}, timeout=min(0.25, remaining))
                if done:
                    break
                if await asyncio.to_thread(
                    _directory_exceeds_limit,
                    self.workspace,
                    self.workspace_limit_bytes,
                ):
                    disk_exceeded = True
                    break
            if timed_out or disk_exceeded:
                try:
                    await self._restart_container()
                finally:
                    if process.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            process.kill()
                with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(wait_task, timeout=3)
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
            wait_task.cancel()
            raise
        finally:
            stream_results = await asyncio.gather(
                stdout_task,
                stderr_task,
                return_exceptions=True,
            )
            self._processes.discard(process)
        _raise_stream_failures(stream_results)
        if disk_exceeded:
            message = f"Workspace disk limit of {self.workspace_limit_bytes // (1024 * 1024)} MiB exceeded.\n"
            stderr_parts.append(message)
            await on_output("stderr", message)
        stderr = "".join(stderr_parts)
        infrastructure_detail = _docker_infrastructure_detail(stderr)
        if (
            process.returncode not in {None, 0}
            and infrastructure_detail
            and not timed_out
            and not disk_exceeded
        ):
            raise InfrastructureError(infrastructure_detail)
        return ExecutionResult(
            phase=phase,
            argv=display_argv,
            exit_code=None if timed_out else (137 if disk_exceeded else process.returncode),
            stdout="".join(stdout_parts),
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=int((time.monotonic() - started) * 1000),
            truncated=any(result is True for result in stream_results),
        )

    async def _restart_container(self) -> None:
        await _run_control(["docker", "kill", self.name], timeout=10)
        code, _, stderr = await _run_control(["docker", "start", self.name], timeout=15)
        if code != 0:
            raise InfrastructureError(f"Docker could not restart the sandbox after a limit: {stderr.strip()}")
        if self.network_disabled:
            self.network_disabled = False
            await self.disable_network()

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        name: str,
        collector: list[str],
        on_output: OutputCallback,
    ) -> bool:
        if stream is None:
            return False
        captured = 0
        truncated = False
        while chunk := await stream.read(4096):
            text = chunk.decode("utf-8", errors="replace")
            captured += len(chunk)
            collector.append(text)
            _retain_utf8_tail(collector, self.settings.max_log_bytes)
            if not truncated:
                await on_output(name, text)
            if captured > self.settings.max_log_bytes:
                truncated = True
        return truncated

    def _probe_command(self, port: int, path: str) -> CommandSpec:
        url = f"http://127.0.0.1:{port}{path}"
        if self.runtime == RuntimeName.PYTHON:
            script = (
                "import sys,urllib.request; "
                f"r=urllib.request.urlopen({url!r}, timeout=2); "
                "sys.exit(0 if 200 <= r.status < 300 else 1)"
            )
            return CommandSpec(argv=["python", "-c", script])
        script = f"fetch({url!r}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
        return CommandSpec(argv=["node", "-e", script])


class DockerSandboxProvider(SandboxProvider):
    name = "docker"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def cleanup_orphans(self) -> None:
        owner = _owner_label(self.settings)
        try:
            code, stdout, _ = await _run_control(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    "label=com.codingsandbox.managed=true",
                    "--filter",
                    f"label={owner}",
                ],
                timeout=10,
            )
        except (FileNotFoundError, OSError):
            return
        container_ids = [value for value in stdout.splitlines() if value]
        if code != 0 or not container_ids:
            return
        await _run_control(["docker", "rm", "-f", *container_ids], timeout=30)

    async def capabilities(self) -> SandboxCapabilities:
        try:
            code, stdout, stderr = await _run_control(
                ["docker", "version", "--format", "{{.Server.Version}}"], timeout=8
            )
        except FileNotFoundError:
            return SandboxCapabilities(
                provider=self.name,
                available=False,
                install_only_network_isolation=True,
                detail="Docker CLI is not installed.",
            )
        except OSError as exc:
            return SandboxCapabilities(
                provider=self.name,
                available=False,
                install_only_network_isolation=True,
                detail=f"Docker could not be checked: {exc}",
            )
        return SandboxCapabilities(
            provider=self.name,
            available=code == 0,
            install_only_network_isolation=True,
            detail=stdout.strip() if code == 0 else stderr.strip(),
        )

    async def create(
        self, runtime: RuntimeName, workspace: Path, limits: SandboxLimits | None = None
    ) -> DockerSandboxSession:
        capabilities = await self.capabilities()
        if not capabilities.available:
            raise InfrastructureError(capabilities.detail or "Docker is unavailable.")
        return await DockerSandboxSession.create(self.settings, runtime, workspace, limits)


def _sandbox_user() -> str:
    # Linux bind mounts retain host ownership, including private (0700) task
    # directories. Match a non-root host user so the sandbox can read and write
    # its workspace without widening file permissions. Docker Desktop on Windows
    # and a root host process use the image's unprivileged account.
    if hasattr(os, "getuid") and os.getuid() != 0:
        return f"{os.getuid()}:{os.getgid()}"
    return "1000:1000"


def _memory_megabytes(value: str) -> int:
    normalized = value.strip().lower()
    if normalized.endswith("g"):
        return int(float(normalized[:-1]) * 1024)
    if normalized.endswith("m"):
        return int(float(normalized[:-1]))
    return 1024


def _owner_label(settings: Settings) -> str:
    data_path = os.path.normcase(str(settings.data_dir.resolve()))
    owner = hashlib.sha256(data_path.encode("utf-8")).hexdigest()[:16]
    return f"com.codingsandbox.owner={owner}"


async def _run_control(args: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_creation_flags(),
        )
    except FileNotFoundError:
        raise
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return 124, "", "operation timed out"
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        with contextlib.suppress(ProcessLookupError):
            await process.wait()
        raise
    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _creation_flags() -> int:
    return 0x08000000 if os.name == "nt" else 0


async def _discard_output(stream: str, chunk: str) -> None:
    del stream, chunk


def _directory_exceeds_limit(root: Path, limit: int) -> bool:
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            if total > limit:
                                return True
                    except FileNotFoundError:
                        # Test runners create and remove cache files concurrently;
                        # a vanished entry does not mean the disk budget was exceeded.
                        continue
        except FileNotFoundError:
            continue
        except OSError:
            return True
    return False


def _docker_infrastructure_detail(stderr: str) -> str | None:
    normalized = stderr.lower()
    markers = (
        "cannot connect to the docker daemon",
        "error during connect",
        "failed to connect to the docker api",
        "error response from daemon: no such container",
    )
    container_stopped = "container" in normalized and "is not running" in normalized
    if any(marker in normalized for marker in markers) or container_stopped:
        detail = stderr.strip()
        return f"Docker became unavailable while running the sandbox: {detail[:1000]}"
    return None


def _healthcheck_exit_code(
    healthy: bool,
    disk_exceeded: bool,
    process_returncode: int | None,
) -> int | None:
    if healthy:
        return 0
    if disk_exceeded:
        return 137
    # A server command can exit cleanly without ever opening its health endpoint.
    # That is a failed health gate, not a successful command execution.
    return 1 if process_returncode == 0 else process_returncode


def _retain_utf8_tail(parts: list[str], limit_bytes: int) -> None:
    """Keep the most useful end of a process stream within its capture budget."""
    encoded = "".join(parts).encode("utf-8")
    if len(encoded) <= limit_bytes:
        return
    parts[:] = [encoded[-limit_bytes:].decode("utf-8", errors="ignore")]


def _raise_stream_failures(results: list[object]) -> None:
    failure = next((result for result in results if isinstance(result, Exception)), None)
    if failure is not None:
        raise InfrastructureError(f"Sandbox output could not be persisted: {failure}") from failure
