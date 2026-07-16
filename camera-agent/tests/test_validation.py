from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from camera_agent.validation import main
from PIL import Image


def write_config(path: Path, root_directory: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "agent_version": "0.4.0",
                "api_base_url": "https://camera.example.com",
                "camera_slug": "front-door",
                "camera_credential": "cam_replace_with_real_credential",
                "camera_id": 0,
                "root_directory": str(root_directory),
                "capture_interval_seconds": 60,
                "heartbeat_interval_seconds": 300,
                "upload_poll_seconds": 2,
                "cleanup_interval_seconds": 300,
                "capture_timeout_seconds": 30,
                "request_timeout_seconds": 30,
                "maximum_width": 1280,
                "maximum_height": 720,
                "jpeg_quality": 72,
                "queue_max_bytes": 2147483648,
                "queue_max_age_hours": 48,
                "allow_insecure_http": False,
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_validation_uses_runtime_validation_capture_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    write_config(config_path, tmp_path)

    capture_time = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    capture_directory = tmp_path / "validation-captures" / "front-door" / "2026-07-16"
    capture_directory.mkdir(parents=True)
    capture_path = (
        capture_directory
        / f"{capture_time:%Y%m%dT%H%M%SZ}_00000000-0000-0000-0000-000000000001.jpg"
    )
    Image.new("RGB", (64, 32), "white").save(capture_path, format="JPEG")

    result = main(
        [
            "--config",
            str(config_path),
            "--since-utc",
            "2026-07-16T11:59:00Z",
            "--minimum-count",
            "1",
        ]
    )

    assert result == 0
