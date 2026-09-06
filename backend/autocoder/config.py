from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AUTOCODER_",
        extra="ignore",
    )

    data_dir: Path = Path(".autocoder-data")
    database_url: str = ""

    ollama_base_url: str = "http://127.0.0.1:11434"
    default_ollama_model: str = "qwen2.5-coder:7b"

    python_image: str = "autocoder-python:3.12"
    node_image: str = "autocoder-node:22"

    max_files: int = Field(default=100, ge=1)
    max_file_bytes: int = Field(default=512 * 1024, ge=1)
    max_workspace_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    max_log_bytes: int = Field(default=1024 * 1024, ge=1)
    repair_log_tail_bytes: int = Field(default=16 * 1024, ge=1)
    agent_loop_timeout_seconds: int = Field(default=30 * 60, ge=1)

    install_timeout_seconds: int = Field(default=120, ge=1)
    test_timeout_seconds: int = Field(default=60, ge=1)
    command_timeout_seconds: int = Field(default=30, ge=1)
    http_startup_timeout_seconds: int = Field(default=30, ge=1)

    docker_cpu_limit: float = 1.0
    docker_memory: str = "1g"

    @model_validator(mode="after")
    def resolve_database(self) -> Settings:
        self.data_dir = self.data_dir.expanduser()
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_dir / 'autocoder.db').as_posix()}"
        return self

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        database_url = make_url(self.database_url)
        if (
            database_url.drivername.startswith("sqlite")
            and database_url.database
            and database_url.database != ":memory:"
        ):
            Path(database_url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
