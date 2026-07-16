from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from camera_agent.commands import poll_command_once
from camera_agent.configuration import AgentConfig

COMMAND_ID = "123e4567-e89b-12d3-a456-426614174000"
AUDIO = b"normalized-mp3"


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


@pytest.mark.asyncio
async def test_command_poll_downloads_verifies_plays_and_completes(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    config.prepare_directories()
    reported_statuses: list[str] = []
    played: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commands/next"):
            return httpx.Response(
                200,
                json={
                    "id": COMMAND_ID,
                    "command_type": "play_audio",
                    "media_size_bytes": len(AUDIO),
                    "media_sha256": hashlib.sha256(AUDIO).hexdigest(),
                    "media_mime_type": "audio/mpeg",
                    "duration_seconds": 12,
                    "expires_at_utc": "2026-07-18T12:02:00Z",
                },
            )
        if request.url.path.endswith("/media"):
            return httpx.Response(200, content=AUDIO)
        if request.url.path.endswith("/result"):
            reported_statuses.append(json.loads(request.content)["status"])
            return httpx.Response(200, json={"id": COMMAND_ID, "status": "completed"})
        raise AssertionError(request.url)

    def player(path: Path, timeout_seconds: int) -> None:
        assert timeout_seconds == 300
        played.append(path.read_bytes())

    waited: list[float] = []

    async def playback_wait(seconds: float) -> None:
        waited.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        processed = await poll_command_once(
            config=config,
            client=client,
            player=player,
            playback_wait=playback_wait,
        )

    assert processed is True
    assert played == [AUDIO]
    assert waited == [12]
    assert reported_statuses == ["started", "completed"]
    assert list(config.temporary_directory.glob("*.mp3")) == []


@pytest.mark.asyncio
async def test_command_poll_reports_playback_failure_and_removes_media(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    config.prepare_directories()
    reported_results: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commands/next"):
            return httpx.Response(
                200,
                json={
                    "id": COMMAND_ID,
                    "command_type": "play_audio",
                    "media_size_bytes": len(AUDIO),
                    "media_sha256": hashlib.sha256(AUDIO).hexdigest(),
                    "media_mime_type": "audio/mpeg",
                    "duration_seconds": 12,
                    "expires_at_utc": "2026-07-18T12:02:00Z",
                },
            )
        if request.url.path.endswith("/media"):
            return httpx.Response(200, content=AUDIO)
        if request.url.path.endswith("/result"):
            reported_results.append(json.loads(request.content))
            return httpx.Response(200, json={"id": COMMAND_ID, "status": "failed"})
        raise AssertionError(request.url)

    def failing_player(path: Path, timeout_seconds: int) -> None:
        raise RuntimeError("player failed")

    async def unexpected_wait(seconds: float) -> None:
        raise AssertionError("failed playback must not wait")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        processed = await poll_command_once(
            config=config,
            client=client,
            player=failing_player,
            playback_wait=unexpected_wait,
        )

    assert processed is True
    assert reported_results[-1] == {
        "status": "failed",
        "error_code": "audio_playback_failed",
    }
    assert list(config.temporary_directory.glob("*.mp3")) == []
