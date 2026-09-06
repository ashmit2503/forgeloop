from pathlib import Path

from autocoder.database import Database
from autocoder.domain import RuntimeName, TaskCreate, TaskStatus


def database_for(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    database.initialize()
    return database


def test_log_limit_is_byte_accurate_and_stays_truncated(tmp_path: Path) -> None:
    database = database_for(tmp_path)
    try:
        task = database.create_task(TaskCreate(prompt="Build a logger"))
        attempt = database.create_attempt(
            task.id,
            1,
            RuntimeName.PYTHON,
            tmp_path,
            "Log output",
            [],
        )

        assert database.append_attempt_log(attempt.id, "stdout", "éé", 5)
        assert not database.append_attempt_log(attempt.id, "stdout", "é", 5)
        assert not database.append_attempt_log(attempt.id, "stdout", "x", 5)

        detail = database.get_task(task.id)
        assert detail is not None
        assert detail.attempts[0].stdout == "éé"
        assert detail.attempts[0].logs_truncated
        assert detail.attempts[0].stdout_truncated
        assert not detail.attempts[0].stderr_truncated
    finally:
        database.close()


def test_stdout_and_stderr_each_keep_their_own_byte_budget(tmp_path: Path) -> None:
    database = database_for(tmp_path)
    try:
        task = database.create_task(TaskCreate(prompt="Build independent log streams"))
        attempt = database.create_attempt(
            task.id,
            1,
            RuntimeName.PYTHON,
            tmp_path,
            "Log output",
            [],
        )

        assert database.append_attempt_log(attempt.id, "stdout", "12345", 4) == "1234"
        assert database.append_attempt_log(attempt.id, "stderr", "abc", 4) == "abc"

        detail = database.get_task(task.id)
        assert detail is not None
        assert detail.attempts[0].stdout == "1234"
        assert detail.attempts[0].stderr == "abc"
        assert detail.attempts[0].logs_truncated
        assert detail.attempts[0].stdout_truncated
        assert not detail.attempts[0].stderr_truncated
    finally:
        database.close()


def test_restart_interrupts_tasks_and_closes_running_attempts(tmp_path: Path) -> None:
    database = database_for(tmp_path)
    try:
        task = database.create_task(TaskCreate(prompt="Build a long-running worker"))
        database.update_task(task.id, status=TaskStatus.EXECUTING)
        database.create_attempt(
            task.id,
            1,
            RuntimeName.PYTHON,
            tmp_path,
            "Run worker",
            [],
        )

        assert database.mark_unfinished_interrupted() == 1
        detail = database.get_task(task.id)
        assert detail is not None
        assert detail.status == TaskStatus.INTERRUPTED
        assert detail.attempts[0].status.value == "cancelled"
        assert detail.attempts[0].completed_at is not None
    finally:
        database.close()


def test_task_detail_exposes_a_safe_sse_resume_cursor(tmp_path: Path) -> None:
    database = database_for(tmp_path)
    try:
        task = database.create_task(TaskCreate(prompt="Build an event stream"))
        first = database.add_event(task.id, "state", {"status": "queued"})
        second = database.add_event(task.id, "state", {"status": "planning"})

        detail = database.get_task(task.id)
        assert detail is not None
        assert detail.event_cursor == second
        assert second > first
    finally:
        database.close()


def test_terminal_event_compaction_keeps_replayable_state_and_completion(tmp_path: Path) -> None:
    database = database_for(tmp_path)
    try:
        task = database.create_task(TaskCreate(prompt="Build a streamed workspace"))
        state_id = database.add_event(task.id, "state", {"status": "executing"})
        database.add_event(task.id, "log", {"stream": "stdout", "chunk": "hello"})
        database.add_event(task.id, "file_stream_start", {"path": "main.py"})
        database.add_event(task.id, "file_stream_chunk", {"path": "main.py", "chunk": "print(1)"})
        database.add_event(task.id, "file_stream_complete", {"path": "main.py"})
        tool_id = database.add_event(task.id, "tool", {"tool": "run_command"})
        complete_id = database.add_event(task.id, "task_complete", {"status": "succeeded"})

        assert database.prune_transient_events(task.id) == 4

        events = database.list_events(task.id, after_id=0)
        assert [event["event"] for event in events] == ["state", "tool", "task_complete"]
        assert [event["id"] for event in events] == [state_id, tool_id, complete_id]
        assert database.list_events(task.id, after_id=state_id)[-1]["id"] == complete_id
    finally:
        database.close()


def test_task_history_can_page_through_every_saved_run(tmp_path: Path) -> None:
    database = database_for(tmp_path)
    try:
        created = [database.create_task(TaskCreate(prompt=f"Build saved task {index}")) for index in range(5)]

        first_page = database.list_tasks(limit=2, offset=0)
        second_page = database.list_tasks(limit=2, offset=2)
        final_page = database.list_tasks(limit=2, offset=4)

        assert [task.id for task in first_page] == [created[4].id, created[3].id]
        assert [task.id for task in second_page] == [created[2].id, created[1].id]
        assert [task.id for task in final_page] == [created[0].id]
    finally:
        database.close()
