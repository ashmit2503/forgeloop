from pathlib import Path

from autocoder.database import Database
from autocoder.domain import TaskCreate


def test_sqlite_task_timestamps_are_returned_as_utc(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'timestamps.db'}")
    database.initialize()
    try:
        created = database.create_task(TaskCreate(prompt="Build a timestamp test"))
        loaded = database.get_task(created.id)
        assert loaded is not None
        assert loaded.created_at.utcoffset() is not None
        assert loaded.created_at.isoformat().endswith("+00:00")
    finally:
        database.close()
