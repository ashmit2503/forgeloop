from pathlib import Path

import pytest
from autocoder.database import Database
from autocoder.domain import BenchmarkSetCreate, BenchmarkTaskDefinition, TaskCreate


def test_versioned_benchmark_sets_accept_individual_tasks(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'benchmarks.db'}")
    database.initialize()
    try:
        benchmark = database.create_benchmark_set(
            BenchmarkSetCreate(
                name="Python basics",
                version="1.0",
                tasks=[BenchmarkTaskDefinition(id="one", prompt="Build a Python counter")],
            )
        )
        updated = database.add_benchmark_task(
            benchmark.id,
            BenchmarkTaskDefinition(id="two", prompt="Build a Python parser"),
        )
        assert [task.id for task in updated.tasks] == ["one", "two"]
        with pytest.raises(ValueError, match="already exists"):
            database.add_benchmark_task(
                benchmark.id,
                BenchmarkTaskDefinition(id="two", prompt="Build something else"),
            )
        database.create_benchmark_run(benchmark.id, [])
        with pytest.raises(ValueError, match="immutable"):
            database.add_benchmark_task(
                benchmark.id,
                BenchmarkTaskDefinition(id="three", prompt="Build a Python cache"),
            )
    finally:
        database.close()


def test_benchmark_versions_are_unique_and_running_runs_cancel_on_restart(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite:///{tmp_path / 'benchmarks.db'}")
    database.initialize()
    request = BenchmarkSetCreate(
        name="Python basics",
        version="1.0",
        tasks=[BenchmarkTaskDefinition(id="one", prompt="Build a Python counter")],
    )
    try:
        benchmark = database.create_benchmark_set(request)
        with pytest.raises(ValueError, match="already exists"):
            database.create_benchmark_set(request)
        run = database.create_benchmark_run(benchmark.id, [])

        assert database.mark_unfinished_benchmarks_cancelled() == 1
        refreshed = database.get_benchmark_run(run.id)
        assert refreshed is not None
        assert refreshed.status == "cancelled"
        assert refreshed.completed_at is not None
    finally:
        database.close()


def test_benchmark_batch_is_atomic_and_rejects_a_second_active_run(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'benchmarks.db'}")
    database.initialize()
    try:
        benchmark = database.create_benchmark_set(
            BenchmarkSetCreate(
                name="Atomic batch",
                version="1.0",
                tasks=[BenchmarkTaskDefinition(id="one", prompt="Build a Python counter")],
            )
        )
        run = database.create_benchmark_batch(
            benchmark.id,
            [
                TaskCreate(prompt="Build a Python counter"),
                TaskCreate(prompt="Build a Python parser"),
            ],
        )
        assert len(run.task_ids) == 2
        assert all(database.get_task(task_id) is not None for task_id in run.task_ids)

        with pytest.raises(ValueError, match="active benchmark"):
            database.create_benchmark_batch(
                benchmark.id,
                [TaskCreate(prompt="Build another Python task")],
            )
        assert len(database.list_tasks()) == 2
    finally:
        database.close()
