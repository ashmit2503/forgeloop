from __future__ import annotations

from fastapi import APIRouter

from autocoder.services import ApplicationServices
from autocoder.system_status import collect_system_status


def create_system_router(services: ApplicationServices) -> APIRouter:
    router = APIRouter(tags=["system"])

    @router.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/system/status")
    async def system_status():
        return await collect_system_status(services.settings)

    return router
