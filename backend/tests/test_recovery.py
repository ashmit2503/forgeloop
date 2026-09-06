import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from autocoder.api import create_app
from autocoder.config import Settings
from autocoder.database import Database
from autocoder.domain import TaskCreate, TaskStatus
from autocoder.orchestrator import Orchestrator
from autocoder.workspace import WorkspaceManager


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_in_memory_database_is_shared_across_request_threads(url: str) -> None:
    database = Database(url)
    database.initialize()
    try:
        task = database.create_task(TaskCreate(prompt="Test database sharing"))
        with ThreadPoolExecutor(max_workers=1) as executor:
            saved = executor.submit(database.get_task, task.id).result()
        assert saved is not None and saved.id == task.id
    finally:
        database.close()


async def test_restart_reconnect_receives_terminal_state_even_without_a_saved_completion(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    app.state.services.orchestrator.start = AsyncMock()
    async with app.router.lifespan_context(app):
        database = app.state.services.database
        task = database.create_task(TaskCreate(prompt="Interrupted task"))
        cursor = database.add_event(task.id, "state", {"status": "generating"})
        database.mark_unfinished_interrupted()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/tasks/{task.id}/events?after={cursor}")
        assert response.text.count("event: task_complete") == 1
        assert '"status": "interrupted"' in response.text


async def test_clarification_answer_is_accepted_only_once(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    app.state.services.orchestrator.start = AsyncMock()
    async with app.router.lifespan_context(app):
        services = app.state.services
        task = services.database.create_task(TaskCreate(prompt="Clarify the required format"))
        services.database.update_task(task.id, status=TaskStatus.AWAITING_CLARIFICATION)
        assert await services.orchestrator.answer_clarification(task.id, "JSON")
        assert not await services.orchestrator.answer_clarification(task.id, "XML")
        assert services.database.get_task(task.id).clarification_answer == "JSON"


async def test_cancelling_a_job_before_its_coroutine_starts_still_finishes_the_task(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.initialize()
    orchestrator = Orchestrator(
        settings=settings,
        database=database,
        workspaces=WorkspaceManager(settings),
        model_providers={},
        sandbox_providers={},
    )
    real_create_task = asyncio.create_task

    def cancel_before_start(coro, *, name=None):
        job = real_create_task(coro, name=name)
        if name and name.startswith("task-"):
            job.cancel()
        return job

    monkeypatch.setattr(asyncio, "create_task", cancel_before_start)
    task = database.create_task(TaskCreate(prompt="Cancelled before the first instruction"))
    try:
        await orchestrator.start()
        await orchestrator.enqueue(task.id)
        await asyncio.wait_for(orchestrator.queue.join(), timeout=2)
        assert database.get_task(task.id).status == TaskStatus.CANCELLED
    finally:
        await orchestrator.stop()
        database.close()
