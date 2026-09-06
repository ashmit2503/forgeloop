from pathlib import Path

from autocoder.domain import RuntimeName
from autocoder.installers import derive_install_command


def test_python_dependencies_install_outside_the_persisted_workspace(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")

    command = derive_install_command(RuntimeName.PYTHON, tmp_path)

    assert command is not None
    assert "/tmp/agent-deps" in command.argv
    assert not any("/workspace/.deps" in argument for argument in command.argv)


def test_python_install_is_derived_only_from_supported_manifests(tmp_path: Path) -> None:
    assert derive_install_command(RuntimeName.PYTHON, tmp_path) is None

    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\nversion='1.0'\n", encoding="utf-8")
    command = derive_install_command(RuntimeName.PYTHON, tmp_path)

    assert command is not None
    assert command.argv[-1] == "."
