from pathlib import Path
from unittest.mock import AsyncMock

import httpx
from autocoder.api import create_app
from autocoder.config import Settings
from autocoder.database import utcnow
from autocoder.domain import TaskCreate, TaskStatus


def test_openapi_contains_public_task_interfaces(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
        )
    )
    document = app.openapi()
    paths = document["paths"]
    assert "/api/tasks" in paths
    assert "/api/tasks/{task_id}/events" in paths
    assert "/api/tasks/{task_id}/download" in paths
    assert "/api/system/status" in paths
    task_schema = document["components"]["schemas"]["TaskCreate"]["properties"]
    assert task_schema["runtime"]["const"] == "python"
    assert task_schema["model_provider"]["const"] == "ollama"
    assert task_schema["sandbox_provider"]["const"] == "docker"


async def test_task_api_rejects_unsupported_providers_instead_of_silently_overriding(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/tasks",
                json={
                    "prompt": "Build a small program",
                    "runtime": "node",
                    "model_provider": "openai",
                    "model": "example",
                    "sandbox_provider": "e2b",
                    "max_retries": 0,
                },
            )

    assert response.status_code == 422
    details = response.json()["detail"]
    assert any(item["loc"][-1] == "runtime" for item in details)
    assert any(item["loc"][-1] == "model_provider" for item in details)
    assert any(item["loc"][-1] == "sandbox_provider" for item in details)


async def test_task_api_uses_the_configured_default_model_when_omitted(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            default_ollama_model="local-coder:latest",
        )
    )
    async with app.router.lifespan_context(app):
        app.state.services.orchestrator.enqueue = AsyncMock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/tasks", json={"prompt": "Build a small program"})

    assert response.status_code == 202
    assert response.json()["model"] == "local-coder:latest"


async def test_terminal_sse_stream_drains_more_than_one_database_page(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
        )
    )
    async with app.router.lifespan_context(app):
        services = app.state.services
        task = services.database.create_task(TaskCreate(prompt="Stream a large event burst"))
        for index in range(300):
            services.database.add_event(task.id, "tool", {"index": index + 1})
        services.database.update_task(
            task.id,
            status=TaskStatus.CANCELLED,
            completed_at=utcnow(),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(f"/api/tasks/{task.id}/events?after=0")

    assert response.status_code == 200
    assert response.text.count("event: tool\n") == 300
    event_ids = [
        int(line.removeprefix("id: ")) for line in response.text.splitlines() if line.startswith("id: ")
    ]
    assert event_ids == sorted(set(event_ids))
