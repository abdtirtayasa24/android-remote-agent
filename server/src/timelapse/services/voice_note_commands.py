from __future__ import annotations

import asyncio
import hashlib
import logging
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.bot.authorization import AuthorizedTelegramUser
from timelapse.models.entities import Camera, CameraCommand, TelegramPrincipal
from timelapse.models.enums import CameraCommandStatus, CameraCommandType

MAXIMUM_VOICE_DURATION_SECONDS = 60
MAXIMUM_VOICE_FILE_BYTES = 5 * 1024 * 1024
VOICE_COMMAND_TTL = timedelta(minutes=2)
PREPARATION_LEASE = timedelta(minutes=5)
LOGGER = logging.getLogger(__name__)
VoiceDownloader = Callable[[str, Path], Awaitable[None]]
VoiceFailureNotifier = Callable[[int, str], Awaitable[None]]
AudioNormalizer = Callable[[Path, Path], "NormalizedAudio"]


class VoiceNoteCommandError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VoiceNoteRequest:
    file_id: str
    duration_seconds: int
    file_size_bytes: int | None
    telegram_message_id: int


@dataclass(frozen=True)
class NormalizedAudio:
    path: Path
    file_size_bytes: int
    sha256: str


async def queue_voice_note_command(
    *,
    session: AsyncSession,
    user: AuthorizedTelegramUser,
    request: VoiceNoteRequest,
    now: datetime | None = None,
    maximum_duration_seconds: int = MAXIMUM_VOICE_DURATION_SECONDS,
    maximum_file_bytes: int = MAXIMUM_VOICE_FILE_BYTES,
    command_ttl: timedelta = VOICE_COMMAND_TTL,
) -> CameraCommand:
    if request.duration_seconds > maximum_duration_seconds:
        raise VoiceNoteCommandError("voice_duration_exceeded")

    if request.file_size_bytes is not None and request.file_size_bytes > maximum_file_bytes:
        raise VoiceNoteCommandError("voice_file_too_large")

    principal = await session.scalar(
        select(TelegramPrincipal)
        .where(TelegramPrincipal.telegram_user_id == user.telegram_user_id)
        .where(TelegramPrincipal.telegram_chat_id == user.telegram_chat_id)
        .limit(1)
    )

    if principal is None or principal.voice_playback_camera_id is None:
        raise VoiceNoteCommandError("voice_camera_not_configured")

    camera = await session.scalar(
        select(Camera)
        .where(Camera.id == principal.voice_playback_camera_id)
        .where(Camera.enabled.is_(True))
    )

    if camera is None:
        raise VoiceNoteCommandError("voice_camera_not_available")

    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    command = CameraCommand(
        camera_id=camera.id,
        command_type=CameraCommandType.PLAY_AUDIO,
        status=CameraCommandStatus.PREPARING,
        payload={
            "source": "telegram_voice",
            "duration_seconds": request.duration_seconds,
            "telegram_file_id": request.file_id,
        },
        media_storage_path=None,
        media_mime_type=None,
        media_size_bytes=None,
        media_sha256=None,
        requested_by_telegram_user_id=user.telegram_user_id,
        requested_in_telegram_chat_id=user.telegram_chat_id,
        telegram_message_id=request.telegram_message_id,
        expires_at=current_time + command_ttl,
    )
    session.add(command)
    await session.flush()
    return command


async def prepare_voice_note_commands_once(
    *,
    session: AsyncSession,
    media_directory: Path,
    download_voice: VoiceDownloader,
    normalize_audio: AudioNormalizer | None = None,
    notify_failure: VoiceFailureNotifier | None = None,
    now: datetime | None = None,
    maximum_file_bytes: int = MAXIMUM_VOICE_FILE_BYTES,
    preparation_lease: timedelta = PREPARATION_LEASE,
) -> int:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    command = await session.scalar(
        select(CameraCommand)
        .where(CameraCommand.status == CameraCommandStatus.PREPARING)
        .where(CameraCommand.expires_at > current_time)
        .where(
            or_(
                CameraCommand.claimed_at.is_(None),
                CameraCommand.claimed_at <= current_time - preparation_lease,
            )
        )
        .order_by(CameraCommand.created_at, CameraCommand.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    if command is None:
        return 0

    command.claimed_at = current_time
    await session.commit()
    command_id = command.id
    destination_chat_id = command.requested_in_telegram_chat_id
    file_id = str(command.payload.get("telegram_file_id", ""))
    await asyncio.to_thread(media_directory.mkdir, parents=True, exist_ok=True, mode=0o750)
    source_path = media_directory / f".{command_id}.ogg"
    output_path = media_directory / f"{command_id}.mp3"
    normalizer = normalize_audio or normalize_voice_audio

    try:
        if not file_id:
            raise VoiceNoteCommandError("voice_file_id_missing")

        await download_voice(file_id, source_path)
        source_size = await asyncio.to_thread(lambda: source_path.stat().st_size)

        if source_size > maximum_file_bytes:
            raise VoiceNoteCommandError("voice_file_too_large")

        normalized = await asyncio.to_thread(normalizer, source_path, output_path)
        command = await session.get(CameraCommand, command_id, with_for_update=True)

        if command is None or command.status != CameraCommandStatus.PREPARING:
            raise VoiceNoteCommandError("voice_command_unavailable")

        command.status = CameraCommandStatus.PENDING
        command.claimed_at = None
        command.media_storage_path = str(normalized.path)
        command.media_mime_type = "audio/mpeg"
        command.media_size_bytes = normalized.file_size_bytes
        command.media_sha256 = normalized.sha256
        command.payload = {
            "source": "telegram_voice",
            "duration_seconds": command.payload.get("duration_seconds"),
        }
        await session.flush()
    except Exception as error:
        await asyncio.to_thread(output_path.unlink, missing_ok=True)
        command = await session.get(CameraCommand, command_id, with_for_update=True)

        if command is not None and command.status == CameraCommandStatus.PREPARING:
            command.status = CameraCommandStatus.FAILED
            command.completed_at = current_time
            command.error_code = (
                error.code
                if isinstance(error, VoiceNoteCommandError)
                else "voice_processing_failed"
            )
            command.payload = {"source": "telegram_voice"}
            command.claimed_at = None
            await session.flush()

        if notify_failure is not None:
            try:
                await notify_failure(
                    destination_chat_id,
                    "Voice note could not be prepared for playback.",
                )
            except Exception:
                LOGGER.warning("voice_failure_notification_failed")
    finally:
        await asyncio.to_thread(source_path.unlink, missing_ok=True)

    return 1


def normalize_voice_audio(source_path: Path, output_path: Path) -> NormalizedAudio:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "96k",
        str(output_path),
    ]

    try:
        result = subprocess.run(  # noqa: S603 - fixed executable and argument list
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise VoiceNoteCommandError("voice_normalization_failed") from None

    if result.returncode != 0 or not output_path.is_file():
        raise VoiceNoteCommandError("voice_normalization_failed")

    digest = hashlib.sha256()

    with output_path.open("rb") as audio_file:
        for chunk in iter(lambda: audio_file.read(1024 * 1024), b""):
            digest.update(chunk)

    return NormalizedAudio(
        path=output_path,
        file_size_bytes=output_path.stat().st_size,
        sha256=digest.hexdigest(),
    )
