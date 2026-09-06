from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from autocoder.config import Settings
from autocoder.domain import (
    FileInfo,
    FilePayload,
    WorkspacePatch,
    WorkspacePlan,
    validate_path_set,
    validate_workspace_path,
)
from autocoder.errors import WorkspaceValidationError


class WorkspaceManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create_initial(self, task_id: str, plan: WorkspacePlan) -> Path:
        source = self.source_path(task_id, 1)
        if source.exists():
            shutil.rmtree(source)
        source.mkdir(parents=True, exist_ok=False)
        self._write_files(source, plan.files)
        self.validate_tree(source)
        return source

    def create_attempt(
        self,
        task_id: str,
        attempt_number: int,
        *,
        attachments: list[FilePayload] | None = None,
    ) -> Path:
        """Create an immutable-at-completion source snapshot for an agent attempt."""
        source = self.source_path(task_id, attempt_number)
        if source.exists():
            shutil.rmtree(source)
        if attempt_number > 1:
            previous = self.source_path(task_id, attempt_number - 1)
            if not previous.exists():
                raise WorkspaceValidationError("previous workspace snapshot is missing")
            shutil.copytree(previous, source, symlinks=True)
            self._reject_symlinks(source)
        else:
            source.mkdir(parents=True, exist_ok=False)
            self._write_files(source, attachments or [])
        self.validate_tree(source)
        return source

    def write_file(self, source: Path, file: FilePayload, *, max_workspace_bytes: int | None = None) -> None:
        self._assert_managed(source)
        try:
            validate_path_set(
                [
                    *[item.path for item in self.list_files(source) if item.path != file.path],
                    file.path,
                ]
            )
        except ValueError as exc:
            raise WorkspaceValidationError(str(exc)) from exc
        target = self._safe_target(source, file.path)
        existed = target.is_file() and not target.is_symlink()
        previous = target.read_bytes() if existed else None
        try:
            self._write_files(source, [file])
            self.validate_tree(source, max_workspace_bytes=max_workspace_bytes)
        except Exception:
            # A rejected tool write must not leave the persisted attempt in an invalid,
            # oversized state. Restore the old file (or remove the new one) atomically
            # from the agent's point of view before reporting the tool error.
            if existed and previous is not None:
                target.write_bytes(previous)
            elif target.exists() and target.is_file():
                target.unlink()
                self._prune_empty_parents(target.parent, source)
            raise

    def remove_file(self, source: Path, relative_path: str) -> None:
        target = self._safe_target(source, relative_path)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        self.validate_tree(source)

    def apply_patch(self, task_id: str, attempt_number: int, patch: WorkspacePatch) -> Path:
        if attempt_number <= 1:
            raise ValueError("patches require a previous attempt")
        previous = self.source_path(task_id, attempt_number - 1)
        source = self.source_path(task_id, attempt_number)
        if not previous.exists():
            raise WorkspaceValidationError("previous workspace snapshot is missing")
        if source.exists():
            shutil.rmtree(source)
        shutil.copytree(previous, source, symlinks=True)
        self._reject_symlinks(source)
        for path in patch.deletes:
            target = self._safe_target(source, path)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        self._write_files(source, patch.upserts)
        self.validate_tree(source)
        return source

    def prepare_run(self, task_id: str, attempt_number: int) -> Path:
        source = self.source_path(task_id, attempt_number)
        run = source.parent / "run"
        if run.exists():
            shutil.rmtree(run)
        self._reject_symlinks(source)
        shutil.copytree(source, run)
        return run

    def delete_run(self, task_id: str, attempt_number: int) -> None:
        run = self.source_path(task_id, attempt_number).parent / "run"
        self._assert_managed(run)
        if run.exists():
            shutil.rmtree(run)

    def clean_stale_runs(self) -> None:
        """Remove disposable execution copies left by an interrupted process."""
        self.settings.tasks_dir.mkdir(parents=True, exist_ok=True)
        for run in self.settings.tasks_dir.glob("*/attempt-*/run"):
            self._assert_managed(run)
            if run.is_dir() and not run.is_symlink():
                shutil.rmtree(run)

    def snapshot(self, source: Path) -> dict[str, str]:
        """Return content fingerprints for detecting tool-created edits."""
        self.validate_tree(source)
        return {
            item.path: hashlib.sha256((source / Path(item.path)).read_bytes()).hexdigest()
            for item in self.list_files(source)
        }

    def changed_since(self, source: Path, before: dict[str, str]) -> list[str]:
        after = self.snapshot(source)
        return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))

    def source_path(self, task_id: str, attempt_number: int) -> Path:
        return self._task_root(task_id) / f"attempt-{attempt_number}" / "source"

    def list_files(self, source: Path) -> list[FileInfo]:
        self.validate_tree(source)
        return [
            FileInfo(path=path.relative_to(source).as_posix(), size=path.stat().st_size)
            for path in sorted(source.rglob("*"))
            if path.is_file()
        ]

    def list_directory(self, source: Path, relative_path: str | None = None) -> list[str]:
        self._assert_managed(source)
        directory = source if relative_path is None else self._safe_target(source, relative_path)
        if not directory.is_dir() or directory.is_symlink():
            raise FileNotFoundError(relative_path or ".")
        self._reject_symlinks(source)
        return [
            f"{path.relative_to(source).as_posix()}{'/' if path.is_dir() else ''}"
            for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        ]

    def read_file(self, source: Path, relative_path: str) -> str:
        target = self._safe_target(source, relative_path)
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError(relative_path)
        if target.stat().st_size > self.settings.max_file_bytes:
            raise WorkspaceValidationError("file exceeds the readable size limit")
        return target.read_text(encoding="utf-8", errors="replace")

    def read_payloads(self, source: Path) -> list[FilePayload]:
        return [
            FilePayload(path=file.path, content=self.read_file(source, file.path))
            for file in self.list_files(source)
        ]

    def zip_bytes(self, source: Path) -> bytes:
        self.validate_tree(source)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in self.list_files(source):
                archive.write(source / Path(file.path), arcname=file.path)
        return buffer.getvalue()

    def delete_task(self, task_id: str) -> None:
        root = self._task_root(task_id)
        self._assert_managed(root)
        if root.exists():
            shutil.rmtree(root)

    def clean_runtime_artifacts(self, source: Path) -> None:
        """Remove sandbox-created caches and installed dependencies from a source snapshot."""
        self._assert_managed(source)
        self._reject_symlinks(source)
        removable_directories = {
            ".autocoder_tests",
            ".deps",
            ".npm-cache",
            ".pytest_cache",
            "__pycache__",
            ".mypy_cache",
            ".ruff_cache",
        }
        for path in sorted(source.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir() and path.name.casefold() in removable_directories:
                shutil.rmtree(path)
            elif path.is_file() and (path.suffix == ".pyc" or path.name == ".coverage"):
                path.unlink()
        self.validate_tree(source)

    def validate_tree(self, root: Path, *, max_workspace_bytes: int | None = None) -> None:
        self._assert_managed(root)
        if not root.is_dir():
            raise WorkspaceValidationError("workspace snapshot is missing")
        self._reject_symlinks(root)
        files = [path for path in root.rglob("*") if path.is_file()]
        try:
            validate_path_set([path.relative_to(root).as_posix() for path in files])
        except ValueError as exc:
            raise WorkspaceValidationError(str(exc)) from exc
        if len(files) > self.settings.max_files:
            raise WorkspaceValidationError(f"workspace exceeds {self.settings.max_files} files")
        total = 0
        for path in files:
            size = path.stat().st_size
            if size > self.settings.max_file_bytes:
                raise WorkspaceValidationError(f"{path.name} exceeds the per-file size limit")
            total += size
        byte_limit = min(
            max_workspace_bytes or self.settings.max_workspace_bytes, self.settings.max_workspace_bytes
        )
        if total > byte_limit:
            raise WorkspaceValidationError("workspace exceeds the total size limit")

    def _write_files(self, root: Path, files: list[FilePayload]) -> None:
        for file in files:
            target = self._safe_target(root, file.path)
            encoded = file.content.encode("utf-8")
            if len(encoded) > self.settings.max_file_bytes:
                raise WorkspaceValidationError(f"{file.path} exceeds the per-file size limit")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file.content, encoding="utf-8", newline="\n")

    @staticmethod
    def _prune_empty_parents(directory: Path, root: Path) -> None:
        resolved_root = root.resolve()
        current = directory
        while current.resolve() != resolved_root:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _safe_target(self, root: Path, relative_path: str) -> Path:
        self._assert_managed(root)
        candidate = root
        if candidate.is_symlink() or candidate.is_junction():
            raise WorkspaceValidationError("symbolic links are not allowed in workspace snapshots")
        for part in PurePosixPath(relative_path).parts:
            candidate = candidate / part
            if candidate.is_symlink() or candidate.is_junction():
                raise WorkspaceValidationError("symbolic links are not allowed in workspace snapshots")
        try:
            validate_workspace_path(relative_path)
        except ValueError as exc:
            raise WorkspaceValidationError(f"unsafe path: {relative_path!r}") from exc
        pure = PurePosixPath(relative_path)
        target = (root / Path(*pure.parts)).resolve()
        resolved_root = root.resolve()
        if not target.is_relative_to(resolved_root):
            raise WorkspaceValidationError(f"path escapes workspace: {relative_path!r}")
        return target

    def _task_root(self, task_id: str) -> Path:
        if not task_id or any(char not in "0123456789abcdef-" for char in task_id.lower()):
            raise WorkspaceValidationError("invalid task identifier")
        root = self.settings.tasks_dir / task_id
        self._assert_managed(root)
        return root

    def _assert_managed(self, path: Path) -> None:
        data_root = self.settings.tasks_dir.resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(data_root):
            raise WorkspaceValidationError("path is outside the managed task directory")

    @staticmethod
    def _reject_symlinks(root: Path) -> None:
        if (
            root.is_symlink()
            or root.is_junction()
            or any(path.is_symlink() or path.is_junction() for path in root.rglob("*"))
        ):
            raise WorkspaceValidationError("symbolic links are not allowed in workspace snapshots")
