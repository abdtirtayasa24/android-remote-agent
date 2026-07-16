"""Verify the claim API survives a PENDING command without duration_seconds."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import Camera, CameraCommand
from timelapse.models.enums import CameraCommandStatus, CameraCommandType


async def test_claim_api_survives_command_without_duration_seconds(
    client,
    create_camera,
    tmp_path: Path,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    media_path = tmp_path / "no-duration.mp3"
    await asyncio.to_thread(media_path.write_bytes, b"mp3-audio")
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    async with session_scope() as session:
        command = CameraCommand(
            camera_id=camera_id,
            command_type=CameraCommandType.PLAY_AUDIO,
            status=CameraCommandStatus.PENDING,
            payload={"source": "unknown"},
            media_storage_path=str(media_path),
            media_mime_type="audio/mpeg",
            media_size_bytes=len(b"mp3-audio"),
            media_sha256=hashlib.sha256(b"mp3-audio").hexdigest(),
            requested_by_telegram_user_id=999,
            requested_in_telegram_chat_id=222,
            telegram_message_id=321,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(command)
        await session.flush()

    response = await client.get(
        f"/api/v1/cameras/{camera_fixture.slug}/commands/next",
        headers={"Authorization": f"Bearer {camera_fixture.credential}"},
    )

    assert response.status_code == 200
    assert response.json()["duration_seconds"] == 0
    assert response.json()["command_type"] == "play_audio"
