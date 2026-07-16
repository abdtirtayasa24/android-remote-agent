from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import Camera, CameraCommand
from timelapse.models.enums import CameraCommandStatus, CameraCommandType
from timelapse.services.camera_commands import (
    CameraCommandError,
    claim_next_camera_command,
    expire_camera_commands_once,
    load_camera_command_media,
    record_camera_command_result,
)


async def add_audio_command(
    *,
    camera_slug: str,
    media_path: Path,
    expires_at: datetime,
) -> CameraCommand:
    session_factory = get_session_factory()
    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_slug))
        camera_id = camera.id

    await asyncio.to_thread(media_path.write_bytes, b"mp3-audio")
    async with session_scope() as session:
        command = CameraCommand(
            camera_id=camera_id,
            command_type=CameraCommandType.PLAY_AUDIO,
            status=CameraCommandStatus.PENDING,
            payload={"source": "test", "duration_seconds": 12},
            media_storage_path=str(media_path),
            media_mime_type="audio/mpeg",
            media_size_bytes=len(b"mp3-audio"),
            media_sha256=hashlib.sha256(b"mp3-audio").hexdigest(),
            requested_by_telegram_user_id=999,
            requested_in_telegram_chat_id=222,
            telegram_message_id=321,
            expires_at=expires_at,
        )
        session.add(command)
        await session.flush()
        return command


async def test_camera_claims_downloads_and_completes_audio_command(
    client,
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    command = await add_audio_command(
        camera_slug=camera.slug,
        media_path=tmp_path / "audio.mp3",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    headers = {"Authorization": f"Bearer {camera.credential}"}

    claimed = await client.get(
        f"/api/v1/cameras/{camera.slug}/commands/next",
        headers=headers,
    )

    assert claimed.status_code == 200
    assert claimed.json() == {
        "id": str(command.id),
        "command_type": "play_audio",
        "media_size_bytes": len(b"mp3-audio"),
        "media_sha256": hashlib.sha256(b"mp3-audio").hexdigest(),
        "media_mime_type": "audio/mpeg",
        "duration_seconds": 12,
        "expires_at_utc": claimed.json()["expires_at_utc"],
    }

    media = await client.get(
        f"/api/v1/cameras/{camera.slug}/commands/{command.id}/media",
        headers=headers,
    )
    assert media.status_code == 200
    assert media.content == b"mp3-audio"
    assert media.headers["content-type"] == "audio/mpeg"

    started = await client.post(
        f"/api/v1/cameras/{camera.slug}/commands/{command.id}/result",
        headers=headers,
        json={"status": "started"},
    )
    assert started.status_code == 200

    completed = await client.post(
        f"/api/v1/cameras/{camera.slug}/commands/{command.id}/result",
        headers=headers,
        json={"status": "completed"},
    )
    assert completed.status_code == 200

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored = await session.get(CameraCommand, command.id)

    assert stored.status == CameraCommandStatus.COMPLETED
    assert stored.media_storage_path is None
    assert stored.completed_at is not None
    assert not await asyncio.to_thread((tmp_path / "audio.mp3").exists)


async def test_expiry_sweep_removes_audio_without_camera_poll(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    command = await add_audio_command(
        camera_slug=camera.slug,
        media_path=tmp_path / "offline-camera.mp3",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    async with session_scope() as session:
        preparing = await session.get(CameraCommand, command.id)
        preparing.status = CameraCommandStatus.PREPARING
        preparing.payload = {
            "source": "telegram_voice",
            "telegram_file_id": "sensitive-file-id",
        }

    async with session_scope() as session:
        expired_count = await expire_camera_commands_once(
            session=session,
            now=datetime.now(UTC),
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored = await session.get(CameraCommand, command.id)

    assert expired_count == 1
    assert stored.status == CameraCommandStatus.EXPIRED
    assert stored.media_storage_path is None
    assert stored.payload == {"source": "telegram_voice"}
    assert not await asyncio.to_thread((tmp_path / "offline-camera.mp3").exists)


async def test_camera_cannot_download_another_cameras_command(
    client,
    create_camera,
    tmp_path: Path,
) -> None:
    front_door = await create_camera(slug="front-door")
    garage = await create_camera(slug="garage")
    command = await add_audio_command(
        camera_slug=front_door.slug,
        media_path=tmp_path / "front-door.mp3",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    await client.get(
        f"/api/v1/cameras/{front_door.slug}/commands/next",
        headers={"Authorization": f"Bearer {front_door.credential}"},
    )

    response = await client.get(
        f"/api/v1/cameras/{garage.slug}/commands/{command.id}/media",
        headers={"Authorization": f"Bearer {garage.credential}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "command_media_not_found"


async def test_claimed_command_media_is_rejected_after_expiry(
    create_camera,
    tmp_path: Path,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    expires_at = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    command = await add_audio_command(
        camera_slug=camera_fixture.slug,
        media_path=tmp_path / "expired-after-claim.mp3",
        expires_at=expires_at,
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    async with session_scope() as session:
        await claim_next_camera_command(
            session=session,
            camera_id=camera_id,
            now=expires_at - timedelta(seconds=1),
        )

    async with session_scope() as session:
        with pytest.raises(CameraCommandError, match="command_media_not_found"):
            await load_camera_command_media(
                session=session,
                camera_id=camera_id,
                command_id=command.id,
                now=expires_at,
            )

    async with session_factory() as session:
        stored = await session.get(CameraCommand, command.id)

    assert stored.status == CameraCommandStatus.EXPIRED
    assert stored.media_storage_path is None
    assert not await asyncio.to_thread((tmp_path / "expired-after-claim.mp3").exists)


async def test_started_playback_can_complete_after_original_expiry(
    create_camera,
    tmp_path: Path,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    expires_at = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    command = await add_audio_command(
        camera_slug=camera_fixture.slug,
        media_path=tmp_path / "started-playback.mp3",
        expires_at=expires_at,
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    async with session_scope() as session:
        await claim_next_camera_command(
            session=session,
            camera_id=camera_id,
            now=expires_at - timedelta(seconds=1),
        )
    async with session_scope() as session:
        started = await record_camera_command_result(
            session=session,
            camera_id=camera_id,
            command_id=command.id,
            status=CameraCommandStatus.STARTED,
            error_code=None,
            now=expires_at - timedelta(milliseconds=500),
        )

    assert started.expires_at > expires_at

    async with session_scope() as session:
        completed = await record_camera_command_result(
            session=session,
            camera_id=camera_id,
            command_id=command.id,
            status=CameraCommandStatus.COMPLETED,
            error_code=None,
            now=expires_at + timedelta(seconds=12),
        )

    assert completed.status == CameraCommandStatus.COMPLETED


async def test_result_after_expiry_marks_command_expired(
    create_camera,
    tmp_path: Path,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    expires_at = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    command = await add_audio_command(
        camera_slug=camera_fixture.slug,
        media_path=tmp_path / "expired-result.mp3",
        expires_at=expires_at,
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    async with session_scope() as session:
        await claim_next_camera_command(
            session=session,
            camera_id=camera_id,
            now=expires_at - timedelta(seconds=1),
        )

    async with session_scope() as session:
        with pytest.raises(CameraCommandError, match="command_expired"):
            await record_camera_command_result(
                session=session,
                camera_id=camera_id,
                command_id=command.id,
                status=CameraCommandStatus.COMPLETED,
                error_code=None,
                now=expires_at,
            )

    async with session_factory() as session:
        stored = await session.get(CameraCommand, command.id)

    assert stored.status == CameraCommandStatus.EXPIRED
    assert stored.media_storage_path is None


async def test_failed_result_requires_stable_error_code(
    client,
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    command = await add_audio_command(
        camera_slug=camera.slug,
        media_path=tmp_path / "audio.mp3",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    headers = {"Authorization": f"Bearer {camera.credential}"}
    await client.get(
        f"/api/v1/cameras/{camera.slug}/commands/next",
        headers=headers,
    )

    response = await client.post(
        f"/api/v1/cameras/{camera.slug}/commands/{command.id}/result",
        headers=headers,
        json={"status": "failed"},
    )

    assert response.status_code == 422


async def test_expired_audio_command_is_not_delivered_and_file_is_deleted(
    client,
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    command = await add_audio_command(
        camera_slug=camera.slug,
        media_path=tmp_path / "expired.mp3",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    response = await client.get(
        f"/api/v1/cameras/{camera.slug}/commands/next",
        headers={"Authorization": f"Bearer {camera.credential}"},
    )

    assert response.status_code == 204

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored = await session.get(CameraCommand, command.id)

    assert stored.status == CameraCommandStatus.EXPIRED
    assert stored.media_storage_path is None
    assert not await asyncio.to_thread((tmp_path / "expired.mp3").exists)
