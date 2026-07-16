from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from camera_agent.configuration import AgentConfig
from camera_agent.main import parse_arguments, run_self_test_captures


def make_config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        agent_version="0.4.0",
        api_base_url="https://camera.example.com",
        camera_slug="front-door",
        camera_credential="cam_replace_with_real_credential",
        camera_id=0,
        root_directory=tmp_path,
        capture_interval_seconds=60,
        heartbeat_interval_seconds=300,
        upload_poll_seconds=2,
        cleanup_interval_seconds=300,
        capture_timeout_seconds=30,
        request_timeout_seconds=30,
        maximum_width=1280,
        maximum_height=720,
        jpeg_quality=72,
        queue_max_bytes=1024 * 1024,
        queue_max_age_hours=48,
        allow_insecure_http=False,
    )


def test_parse_arguments_accepts_self_test_once_flags() -> None:
    arguments = parse_arguments(
        [
            "--config",
            "config.json",
            "--camera-id",
            "1",
            "--once",
        ]
    )

    assert arguments.config == Path("config.json")
    assert arguments.camera_id == 1
    assert arguments.once is True
    assert arguments.count is None


def test_parse_arguments_accepts_self_test_count_flags() -> None:
    arguments = parse_arguments(
        [
            "--camera-id",
            "0",
            "--count",
            "10",
            "--fail-fast",
        ]
    )

    assert arguments.camera_id == 0
    assert arguments.once is False
    assert arguments.count == 10
    assert arguments.fail_fast is True


def test_run_self_test_captures_writes_requested_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_camera_ids: list[int] = []

    def fake_capture(config: AgentConfig) -> object:
        captured_camera_ids.append(config.camera_id)
        return SimpleNamespace(
            capture_id="capture-1",
            file_path=tmp_path / "capture.jpg",
            file_size_bytes=123,
        )

    monkeypatch.setattr("camera_agent.main.capture_validation_image", fake_capture)

    failures = run_self_test_captures(
        make_config(tmp_path),
        count=3,
        fail_fast=True,
    )

    assert failures == 0
    assert captured_camera_ids == [0, 0, 0]
