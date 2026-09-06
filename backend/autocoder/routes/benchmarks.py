from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from autocoder.database import utcnow
from autocoder.domain import (
    TERMINAL_STATUSES,
    BenchmarkReport,
    BenchmarkRunCreate,
    BenchmarkRunView,
    BenchmarkSetCreate,
    BenchmarkSetView,
    BenchmarkTaskDefinition,
    RuntimeRequest,
    TaskCreate,
    TaskStatus,
)
from autocoder.services import ApplicationServices


def create_benchmarks_router(services: ApplicationServices) -> APIRouter:
    router = APIRouter(tags=["benchmarks"])

    @router.post("/api/benchmarks/sets", response_model=BenchmarkSetView, status_code=201)
    async def import_benchmark_set(request: BenchmarkSetCreate) -> BenchmarkSetView:
        async with services.benchmark_lifecycle_lock:
            return services.database.create_benchmark_set(request)

    @router.get("/api/benchmarks/sets", response_model=list[BenchmarkSetView])
    async def list_benchmark_sets() -> list[BenchmarkSetView]:
        return services.database.list_benchmark_sets()

    @router.post(
        "/api/benchmarks/sets/{benchmark_set_id}/tasks",
        response_model=BenchmarkSetView,
        status_code=201,
    )
    async def add_benchmark_task(benchmark_set_id: str, task: BenchmarkTaskDefinition) -> BenchmarkSetView:
        async with services.benchmark_lifecycle_lock:
            try:
                return services.database.add_benchmark_task(benchmark_set_id, task)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Benchmark set not found.") from exc

    @router.get("/api/benchmarks/runs", response_model=list[BenchmarkRunView])
    async def list_benchmark_runs() -> list[BenchmarkRunView]:
        return services.database.list_benchmark_runs()

    @router.get("/api/benchmarks/runs/{run_id}", response_model=BenchmarkRunView)
    async def get_benchmark_run(run_id: str) -> BenchmarkRunView:
        run = services.database.get_benchmark_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Benchmark run not found.")
        return run

    @router.post("/api/benchmarks/runs/{run_id}/cancel", response_model=BenchmarkRunView)
    async def cancel_benchmark(run_id: str) -> BenchmarkRunView:
        async with services.benchmark_lifecycle_lock:
            run = services.database.get_benchmark_run(run_id)
            if not run:
                raise HTTPException(status_code=404, detail="Benchmark run not found.")
            if run.status not in {"queued", "running"}:
                raise HTTPException(status_code=409, detail="Benchmark run is already terminal.")
            run = services.database.update_benchmark_run(run_id, status="cancelling")
        for task_id in run.task_ids:
            await services.orchestrator.cancel(task_id)
        return run

    @router.post(
        "/api/benchmarks/sets/{benchmark_set_id}/runs",
        response_model=BenchmarkRunView,
        status_code=202,
    )
    async def run_benchmark(benchmark_set_id: str, request: BenchmarkRunCreate) -> BenchmarkRunView:
        async with services.benchmark_lifecycle_lock:
            benchmark_set = services.database.get_benchmark_set(benchmark_set_id)
            if not benchmark_set:
                raise HTTPException(status_code=404, detail="Benchmark set not found.")
            task_requests = [
                TaskCreate(
                    prompt=definition.prompt,
                    runtime=RuntimeRequest.PYTHON,
                    model_provider="ollama",
                    model=request.model,
                    sandbox_provider="docker",
                    max_retries=request.max_retries,
                    attachments=definition.attachments,
                    execution_timeout_seconds=request.execution_timeout_seconds,
                    memory_mb=request.memory_mb,
                    cpu_limit=request.cpu_limit,
                    disk_mb=request.disk_mb,
                    execution_network=request.execution_network,
                )
                for definition in benchmark_set.tasks
            ]
            try:
                run = services.database.create_benchmark_batch(benchmark_set_id, task_requests)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            for task_id in run.task_ids:
                await services.orchestrator.enqueue(task_id)
        monitor = asyncio.create_task(_monitor_benchmark(services, run.id), name=f"benchmark-{run.id}")
        services.benchmark_monitors.add(monitor)
        monitor.add_done_callback(services.benchmark_monitors.discard)
        return run

    return router


async def _monitor_benchmark(services: ApplicationServices, run_id: str) -> None:
    started = asyncio.get_running_loop().time()
    while True:
        async with services.benchmark_lifecycle_lock:
            run = services.database.get_benchmark_run(run_id)
            if not run or run.status == "cancelled":
                return
            tasks = [services.database.get_task(task_id) for task_id in run.task_ids]
            if not all(task and task.status in TERMINAL_STATUSES for task in tasks):
                pass
            elif run.status == "cancelling":
                services.database.update_benchmark_run(
                    run_id,
                    status="cancelled",
                    completed_at=utcnow(),
                )
                return
            else:
                completed = [task for task in tasks if task]
                passed = [task for task in completed if task.status == TaskStatus.SUCCEEDED]
                attempts_to_pass = [len(task.attempts) for task in passed]
                inner_iterations = [
                    attempt.inner_iterations for task in completed for attempt in task.attempts
                ]
                report = BenchmarkReport(
                    pass_rate=(len(passed) / len(completed)) if completed else 0,
                    average_attempts_to_pass=(
                        sum(attempts_to_pass) / len(attempts_to_pass) if attempts_to_pass else 0
                    ),
                    average_inner_iterations=(
                        sum(inner_iterations) / len(inner_iterations) if inner_iterations else 0
                    ),
                    total_time_ms=int((asyncio.get_running_loop().time() - started) * 1000),
                )
                services.database.update_benchmark_run(
                    run_id,
                    status="completed",
                    report=report,
                    completed_at=services.database.get_task(run.task_ids[-1]).completed_at
                    if run.task_ids
                    else None,
                )
                return
        await asyncio.sleep(1)
