from pathlib import Path

from autocoder.config import Settings


def test_settings_create_the_configured_sqlite_parent_and_artifact_directories(tmp_path: Path) -> None:
    database_path = tmp_path / "database" / "coding-sandbox.db"
    settings = Settings(
        data_dir=tmp_path / "artifacts",
        database_url=f"sqlite:///{database_path.as_posix()}",
    )

    settings.ensure_directories()

    assert settings.tasks_dir.is_dir()
    assert database_path.parent.is_dir()


def test_database_follows_data_directory_unless_explicitly_configured(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "custom")
    settings.ensure_directories()
    assert settings.database_url == f"sqlite:///{(tmp_path / 'custom/autocoder.db').as_posix()}"
