from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path

from autocoder.domain import (
    CommandSpec,
    ExecutionResult,
    HttpExecution,
    PhaseName,
    RuntimeName,
    SandboxCapabilities,
    SandboxLimits,
)

OutputCallback = Callable[[str, str], Awaitable[None]]


class SandboxSession(ABC):
    @abstractmethod
    async def run(
        self,
        command: CommandSpec,
        *,
        phase: PhaseName,
        timeout_seconds: int,
        on_output: OutputCallback,
    ) -> ExecutionResult:
        raise NotImplementedError

    @abstractmethod
    async def disable_network(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def enable_network(self) -> None:
        """Restore sandbox networking for an orchestrator-approved dependency install."""
        raise NotImplementedError

    @abstractmethod
    async def start_and_probe(
        self,
        execution: HttpExecution,
        *,
        timeout_seconds: int,
        on_output: OutputCallback,
    ) -> ExecutionResult:
        raise NotImplementedError

    @abstractmethod
    async def destroy(self) -> None:
        raise NotImplementedError


class SandboxProvider(ABC):
    name: str

    async def cleanup_orphans(self) -> None:
        """Best-effort cleanup hook for sandboxes left by an interrupted process."""
        return None

    @abstractmethod
    async def capabilities(self) -> SandboxCapabilities:
        raise NotImplementedError

    @abstractmethod
    async def create(
        self, runtime: RuntimeName, workspace: Path, limits: SandboxLimits | None = None
    ) -> SandboxSession:
        raise NotImplementedError
