from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def copy_camera_agent_tree(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    destination = tmp_path / "camera-agent"
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache")
    shutil.copytree(source, destination, ignore=ignore)
    return destination


def write_fake_commands(directory: Path) -> None:
    directory.mkdir()

    for command_name in ("pkg", "python"):
        command_path = directory / command_name
        command_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        command_path.chmod(0o700)


def test_termux_installer_creates_runtime_import_layout(tmp_path: Path) -> None:
    camera_agent_directory = copy_camera_agent_tree(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    write_fake_commands(fake_bin)
    home = tmp_path / "home"
    home.mkdir()

    environment = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    shell_path = shutil.which("sh")
    assert shell_path is not None

    subprocess.run(  # noqa: S603 - test runs the repository installer in a temp HOME
        [shell_path, "scripts/install-termux.sh"],
        cwd=camera_agent_directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    runtime_directory = home / "timelapse"

    assert (runtime_directory / "app" / "camera_agent" / "main.py").is_file()
    assert (runtime_directory / "bin" / "camera-self-test.sh").is_file()
    assert (runtime_directory / "bin" / "start-agent.sh").is_file()
    assert (runtime_directory / "validation-captures").is_dir()
