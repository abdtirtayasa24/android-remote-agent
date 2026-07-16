from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from timelapse.bot.authorization import AuthorizedTelegramUser
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import Camera, CameraCommand, TelegramPrincipal
from timelapse.models.enums import CameraCommandStatus, CameraCommandType
from timelapse.services.voice_note_commands import (
    NormalizedAudio,
    VoiceNoteCommandError,
    VoiceNoteRequest,
    prepare_voice_note_commands_once,
    queue_voice_note_command,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
USER = AuthorizedTelegramUser(
    telegram_user_id=999,
    telegram_chat_id=222,
    role="administrator",
)


async def configure_voice_camera(create_camera) -> None:
    camera_fixture = await create_camera(slug="front-door")
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))

    async with session_scope() as session:
        session.add(
            TelegramPrincipal(
                telegram_user_id=USER.telegram_user_id,
                telegram_chat_id=USER.telegram_chat_id,
                display_name="Admin",
                role=USER.role,
                voice_playback_camera_id=camera.id,
            )
        )


async def test_voice_note_is_queued_then_prepared_by_worker(
    create_camera,
    tmp_path: Path,
) -> None:
    await configure_voice_camera(create_camera)

    async with session_scope() as session:
        command = await queue_voice_note_command(
            session=session,
            user=USER,
            request=VoiceNoteRequest(
                file_id="telegram-file-id",
                duration_seconds=12,
                file_size_bytes=len(b"telegram-ogg"),
                telegram_message_id=321,
            ),
            now=NOW,
        )

    assert command.status == CameraCommandStatus.PREPARING
    assert command.media_storage_path is None

    downloaded_paths: list[Path] = []

    async def download_voice(file_id: str, path: Path) -> None:
        assert file_id == "telegram-file-id"
        downloaded_paths.append(path)
        await asyncio.to_thread(path.write_bytes, b"telegram-ogg")

    def normalize_audio(source: Path, destination: Path) -> NormalizedAudio:
        assert source.read_bytes() == b"telegram-ogg"
        destination.write_bytes(b"normalized-mp3")
        return NormalizedAudio(
            path=destination,
            file_size_bytes=destination.stat().st_size,
            sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
        )

    async with session_scope() as session:
        prepared_count = await prepare_voice_note_commands_once(
            session=session,
            media_directory=tmp_path / "audio-commands",
            download_voice=download_voice,
            normalize_audio=normalize_audio,
            now=NOW,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored = await session.get(CameraCommand, command.id)
        camera = await session.get(Camera, stored.camera_id)

    assert prepared_count == 1
    assert camera.slug == "front-door"
    assert stored.command_type == CameraCommandType.PLAY_AUDIO
    assert stored.status == CameraCommandStatus.PENDING
    assert stored.media_mime_type == "audio/mpeg"
    assert stored.media_size_bytes == len(b"normalized-mp3")
    assert stored.media_sha256 == hashlib.sha256(b"normalized-mp3").hexdigest()
    assert (
        await asyncio.to_thread(Path(stored.media_storage_path).read_bytes)
        == b"normalized-mp3"
    )
    assert stored.expires_at == datetime(2026, 7, 18, 12, 2, tzinfo=UTC)
    assert downloaded_paths and not downloaded_paths[0].exists()


async def test_voice_download_failure_marks_command_failed_and_notifies_user(
    create_camera,
    tmp_path: Path,
) -> None:
    await configure_voice_camera(create_camera)

    async with session_scope() as session:
        command = await queue_voice_note_command(
            session=session,
            user=USER,
            request=VoiceNoteRequest(
                file_id="missing-file",
                duration_seconds=5,
                file_size_bytes=100,
                telegram_message_id=321,
            ),
            now=NOW,
        )

    async def failing_download(file_id: str, path: Path) -> None:
        raise RuntimeError("telegram unavailable")

    notifications: list[tuple[int, str]] = []

    async def notify_failure(chat_id: int, text: str) -> None:
        notifications.append((chat_id, text))

    async with session_scope() as session:
        await prepare_voice_note_commands_once(
            session=session,
            media_directory=tmp_path / "audio-commands",
            download_voice=failing_download,
            notify_failure=notify_failure,
            now=NOW,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored = await session.get(CameraCommand, command.id)

    assert stored.status == CameraCommandStatus.FAILED
    assert stored.error_code == "voice_processing_failed"
    assert stored.media_storage_path is None
    assert notifications == [
        (USER.telegram_chat_id, "Voice note could not be prepared for playback.")
    ]


async def test_voice_note_limit_is_rejected_before_command_creation(
    create_camera,
) -> None:
    await configure_voice_camera(create_camera)

    async with session_scope() as session:
        with pytest.raises(VoiceNoteCommandError, match="voice_duration_exceeded"):
            await queue_voice_note_command(
                session=session,
                user=USER,
                request=VoiceNoteRequest(
                    file_id="telegram-file-id",
                    duration_seconds=61,
                    file_size_bytes=100,
                    telegram_message_id=321,
                ),
                now=NOW,
            )
