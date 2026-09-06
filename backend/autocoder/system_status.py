from __future__ import annotations

import asyncio
import os

import httpx

from autocoder.config import Settings
from autocoder.domain import SystemStatus


async def collect_system_status(settings: Settings) -> SystemStatus:
    docker, images, ollama = await asyncio.gather(
        _docker_status(),
        _image_status(settings),
        _ollama_status(settings),
    )
    models = ollama.pop("models", [])
    return SystemStatus(
        docker=docker,
        ollama=ollama,
        models=models,
        default_model=settings.default_ollama_model,
        sandbox_images=images,
        providers={
            "ollama": {"configured": ollama.get("available", False), "enabled": True},
            "docker": {"configured": docker.get("available", False), "enabled": True},
        },
    )


async def _docker_status() -> dict[str, object]:
    try:
        code, stdout, stderr = await _subprocess(
            ["docker", "version", "--format", "{{.Server.Version}}"], timeout=8
        )
    except FileNotFoundError:
        return {"available": False, "detail": "Docker CLI is not installed."}
    except OSError as exc:
        return {"available": False, "detail": f"Docker could not be checked: {exc}"}
    return {
        "available": code == 0,
        "version": stdout.strip() if code == 0 else None,
        "detail": "Docker daemon is running." if code == 0 else stderr.strip(),
    }


async def _image_status(settings: Settings) -> dict[str, bool]:
    async def inspect(image: str) -> bool:
        try:
            code, _, _ = await _subprocess(["docker", "image", "inspect", image], timeout=8)
            return code == 0
        except OSError:
            return False

    python = await inspect(settings.python_image)
    return {settings.python_image: python}


async def _ollama_status(settings: Settings) -> dict[str, object]:
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            version_response, tags_response = await asyncio.gather(
                client.get(f"{settings.ollama_base_url.rstrip('/')}/api/version"),
                client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags"),
            )
            version_response.raise_for_status()
            tags_response.raise_for_status()
            version_payload = version_response.json()
            tags_payload = tags_response.json()
            if not isinstance(version_payload, dict) or not isinstance(tags_payload, dict):
                raise ValueError("Ollama returned an unexpected status payload")
            tags = tags_payload.get("models", [])
            if not isinstance(tags, list):
                raise ValueError("Ollama returned an invalid model list")
            models: list[str] = []
            for item in tags:
                if not isinstance(item, dict):
                    continue
                candidate = item.get("name") or item.get("model")
                if isinstance(candidate, str) and candidate and candidate not in models:
                    models.append(candidate)
            return {
                "available": True,
                "version": version_payload.get("version"),
                "detail": "Ollama is ready.",
                "models": models,
            }
    except (httpx.HTTPError, ValueError):
        return {
            "available": False,
            "version": None,
            "detail": f"Ollama is unavailable at {settings.ollama_base_url}.",
            "models": [],
        }


async def _subprocess(args: list[str], timeout: int) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=0x08000000 if os.name == "nt" else 0,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return 124, "", "operation timed out"
    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )
