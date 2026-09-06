from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from autocoder.config import Settings, get_settings
from autocoder.errors import AutocoderError
from autocoder.routes.benchmarks import create_benchmarks_router
from autocoder.routes.system import create_system_router
from autocoder.routes.tasks import create_tasks_router
from autocoder.services import ApplicationServices


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    services = ApplicationServices(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services.database.initialize()
        services.database.mark_unfinished_interrupted()
        services.database.mark_unfinished_benchmarks_cancelled()
        await services.orchestrator.start()
        with suppress(OSError, AutocoderError):
            services.workspaces.clean_stale_runs()
        app.state.services = services
        try:
            yield
        finally:
            monitors = list(services.benchmark_monitors)
            for monitor in monitors:
                monitor.cancel()
            if monitors:
                await asyncio.gather(*monitors, return_exceptions=True)
            await services.orchestrator.stop()
            services.database.close()

    app = FastAPI(
        title="ForgeLoop",
        version="0.1.0",
        description="Generate, execute, observe, and repair code in bounded isolated sandboxes.",
        lifespan=lifespan,
    )
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def prevent_stale_api_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(create_tasks_router(services))
    app.include_router(create_benchmarks_router(services))
    app.include_router(create_system_router(services))

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        del request
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(AutocoderError)
    async def autocoder_error_handler(request: Request, exc: AutocoderError) -> JSONResponse:
        del request
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app
