from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from io import BytesIO

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from autocoder.database import utcnow
from autocoder.domain import (
    TERMINAL_STATUSES,
    ClarificationAnswer,
    TaskCreate,
    TaskDetail,
    TaskView,
)
from autocoder.services import ApplicationServices


def create_tasks_router(services: ApplicationServices) -> APIRouter:
    router = APIRouter(tags=["tasks"])

    @router.post("/api/tasks", response_model=TaskView, status_code=202)
    async def create_task(request: TaskCreate) -> TaskView:
        if "model" not in request.model_fields_set:
            request = request.model_copy(update={"model": services.settings.default_ollama_model})
        task = services.database.create_task(request)
        await services.orchestrator.enqueue(task.id)
        return task

    @router.get("/api/tasks", response_model=list[TaskView])
    async def list_tasks(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[TaskView]:
        return services.database.list_tasks(limit, offset)

    @router.get("/api/tasks/{task_id}", response_model=TaskDetail)
    async def get_task(task_id: str) -> TaskDetail:
        return _require_task(services, task_id)

    @router.post("/api/tasks/{task_id}/cancel", status_code=202)
    async def cancel_task(task_id: str) -> dict[str, bool]:
        if not services.database.get_task(task_id):
            raise HTTPException(status_code=404, detail="Task not found.")
        accepted = await services.orchestrator.cancel(task_id)
        if not accepted:
            raise HTTPException(status_code=409, detail="Task is already terminal.")
        return {"accepted": True}

    @router.post("/api/tasks/{task_id}/clarification", response_model=TaskDetail)
    async def answer_clarification(task_id: str, answer: ClarificationAnswer) -> TaskDetail:
        if not services.database.get_task(task_id):
            raise HTTPException(status_code=404, detail="Task not found.")
        accepted = await services.orchestrator.answer_clarification(task_id, answer.answer)
        if not accepted:
            raise HTTPException(status_code=409, detail="Task is not waiting for clarification.")
        return _require_task(services, task_id)

    @router.get("/api/tasks/{task_id}/events")
    async def task_events(
        request: Request,
        task_id: str,
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        _require_task(services, task_id)
        header_id = request.headers.get("last-event-id")
        try:
            cursor = max(after, int(header_id or "0"))
        except ValueError:
            cursor = after

        async def stream() -> AsyncIterator[str]:
            nonlocal cursor
            idle_ticks = 0
            completion_sent = False
            while True:
                if await request.is_disconnected():
                    return
                events = services.database.list_events(task_id, cursor)
                for event in events:
                    completion_sent |= event["event"] == "task_complete"
                    cursor = event["id"]
                    data = json.dumps(event["data"], separators=(",", ":"))
                    yield f"id: {cursor}\nevent: {event['event']}\ndata: {data}\n\n"
                if events:
                    idle_ticks = 0
                    # Drain persisted bursts (for example, generated-file chunks)
                    # without imposing half a second of latency per 250-event page.
                    await asyncio.sleep(0)
                    continue
                else:
                    idle_ticks += 1
                task = services.database.get_task(task_id)
                if not task:
                    return
                if task and task.status in TERMINAL_STATUSES and not events:
                    # Restarts and a reconnect after the final persisted event can
                    # leave the client with no completion event to refresh from.
                    if not completion_sent:
                        data = json.dumps(
                            {
                                "task_id": task_id,
                                "timestamp": utcnow().isoformat(),
                                "status": task.status.value,
                                "attempt": task.active_attempt,
                            }
                        )
                        yield f"event: task_complete\ndata: {data}\n\n"
                    return
                if idle_ticks >= 150:
                    idle_ticks = 0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/tasks/{task_id}/attempts/{attempt_number}/files")
    async def list_attempt_files(task_id: str, attempt_number: int):
        task = _require_task(services, task_id)
        _require_attempt(task, attempt_number)
        source = services.workspaces.source_path(task_id, attempt_number)
        return services.workspaces.list_files(source)

    @router.get("/api/tasks/{task_id}/attempts/{attempt_number}/files/{file_path:path}")
    async def read_attempt_file(task_id: str, attempt_number: int, file_path: str):
        task = _require_task(services, task_id)
        _require_attempt(task, attempt_number)
        source = services.workspaces.source_path(task_id, attempt_number)
        try:
            content = services.workspaces.read_file(source, file_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="File not found.") from exc
        return {"path": file_path, "content": content}

    @router.get("/api/tasks/{task_id}/download")
    async def download_task(task_id: str) -> StreamingResponse:
        task = _require_task(services, task_id)
        if task.status not in TERMINAL_STATUSES:
            raise HTTPException(
                status_code=409, detail="The workspace can be downloaded after the task finishes."
            )
        if not task.attempts:
            raise HTTPException(status_code=409, detail="No workspace has been generated yet.")
        source = services.workspaces.source_path(task_id, task.attempts[-1].number)
        archive = services.workspaces.zip_bytes(source)
        return StreamingResponse(
            BytesIO(archive),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="forgeloop-{task_id}.zip"'},
        )

    @router.delete("/api/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: str) -> Response:
        task = _require_task(services, task_id)
        if task.status not in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Cancel the running task before deleting it.")
        if services.database.task_in_active_benchmark(task_id):
            raise HTTPException(
                status_code=409,
                detail="This task belongs to a benchmark run that is still in progress.",
            )
        services.workspaces.delete_task(task_id)
        services.database.delete_task(task_id)
        return Response(status_code=204)

    return router


def _require_task(services: ApplicationServices, task_id: str) -> TaskDetail:
    task = services.database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


def _require_attempt(task: TaskDetail, attempt_number: int):
    attempt = next((item for item in task.attempts if item.number == attempt_number), None)
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found.")
    return attempt
