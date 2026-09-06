from __future__ import annotations

import asyncio

from autocoder.config import Settings
from autocoder.database import Database
from autocoder.orchestrator import Orchestrator
from autocoder.providers.models import OllamaModelProvider
from autocoder.providers.sandboxes import DockerSandboxProvider
from autocoder.workspace import WorkspaceManager


class ApplicationServices:
    def __init__(self, settings: Settings) -> None:
        settings.ensure_directories()
        self.settings = settings
        self.database = Database(settings.database_url)
        self.workspaces = WorkspaceManager(settings)
        self.orchestrator = Orchestrator(
            settings=settings,
            database=self.database,
            workspaces=self.workspaces,
            model_providers={
                "ollama": OllamaModelProvider(settings.ollama_base_url),
            },
            sandbox_providers={
                "docker": DockerSandboxProvider(settings),
            },
        )
        self.benchmark_monitors: set[asyncio.Task[None]] = set()
        self.benchmark_lifecycle_lock = asyncio.Lock()
