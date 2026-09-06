from __future__ import annotations

from pathlib import Path

from autocoder.domain import CommandSpec, RuntimeName


def derive_install_command(runtime: RuntimeName, workspace: Path) -> CommandSpec | None:
    if runtime == RuntimeName.PYTHON:
        if (workspace / "requirements.txt").is_file():
            return CommandSpec(
                argv=[
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "--target",
                    "/tmp/agent-deps",
                    "-r",
                    "requirements.txt",
                ]
            )
        if (workspace / "pyproject.toml").is_file():
            return CommandSpec(
                argv=[
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "--target",
                    "/tmp/agent-deps",
                    ".",
                ]
            )
        return None

    if (workspace / "package-lock.json").is_file():
        return CommandSpec(argv=["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"])
    if (workspace / "package.json").is_file():
        return CommandSpec(argv=["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"])
    return None
