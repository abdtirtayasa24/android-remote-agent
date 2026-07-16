from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import CameraCommand
from timelapse.models.enums import CameraCommandStatus

PLAYBACK_COMPLETION_GRACE = timedelta(seconds=30)
ACTIVE_COMMAND_STATUSES = {
    CameraCommandStatus.PREPARING,
    CameraCommandStatus.PENDING,
    CameraCommandStatus.CLAIMED,
    CameraCommandStatus.STARTED,
}


class CameraCommandError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def claim_next_camera_command(
    *,
    session: AsyncSession,
    camera_id: UUID,
    now: datetime | None = None,
) -> CameraCommand | None:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    await expire_camera_commands_once(
        session=session,
        now=current_time,
        camera_id=camera_id,
    )

    command = await session.scalar(
        select(CameraCommand)
        .where(CameraCommand.camera_id == camera_id)
        .where(CameraCommand.status == CameraCommandStatus.PENDING)
        .where(CameraCommand.expires_at > current_time)
        .where(CameraCommand.media_storage_path.is_not(None))
        .where(CameraCommand.media_mime_type.is_not(None))
        .where(CameraCommand.media_size_bytes.is_not(None))
        .where(CameraCommand.media_sha256.is_not(None))
        .order_by(CameraCommand.created_at, CameraCommand.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    if command is not None:
        command.status = CameraCommandStatus.CLAIMED
        command.claimed_at = current_time
        command.error_code = None

    await session.commit()
    return command


async def expire_camera_commands_once(
    *,
    session: AsyncSession,
    now: datetime | None = None,
    camera_id: UUID | None = None,
    batch_size: int = 100,
) -> int:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    query = (
        select(CameraCommand)
        .where(CameraCommand.status.in_(ACTIVE_COMMAND_STATUSES))
        .where(CameraCommand.expires_at <= current_time)
        .order_by(CameraCommand.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    if camera_id is not None:
        query = query.where(CameraCommand.camera_id == camera_id)

    expired = (await session.scalars(query)).all()

    for command in expired:
        await _expire_command(command, current_time)

    await session.flush()
    return len(expired)


async def load_camera_command_media(
    *,
    session: AsyncSession,
    camera_id: UUID,
    command_id: UUID,
    now: datetime | None = None,
) -> CameraCommand:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    command = await session.get(CameraCommand, command_id, with_for_update=True)

    if command is not None and command.expires_at <= current_time:
        await _expire_command(command, current_time)
        await session.commit()
        raise CameraCommandError("command_media_not_found")

    if (
        command is None
        or command.camera_id != camera_id
        or command.status not in {CameraCommandStatus.CLAIMED, CameraCommandStatus.STARTED}
        or command.media_storage_path is None
    ):
        raise CameraCommandError("command_media_not_found")

    if not await asyncio.to_thread(Path(command.media_storage_path).is_file):
        raise CameraCommandError("command_media_not_found")

    return command


async def record_camera_command_result(
    *,
    session: AsyncSession,
    camera_id: UUID,
    command_id: UUID,
    status: CameraCommandStatus,
    error_code: str | None,
    now: datetime | None = None,
) -> CameraCommand:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    command = await session.get(CameraCommand, command_id, with_for_update=True)

    if command is None or command.camera_id != camera_id:
        raise CameraCommandError("command_not_found")

    if command.expires_at <= current_time:
        await _expire_command(command, current_time)
        await session.commit()
        raise CameraCommandError("command_expired")

    if status == CameraCommandStatus.STARTED:
        if command.status not in {
            CameraCommandStatus.CLAIMED,
            CameraCommandStatus.STARTED,
        }:
            raise CameraCommandError("invalid_command_transition")
        command.status = CameraCommandStatus.STARTED
        command.started_at = command.started_at or current_time
        duration_seconds = command.payload.get("duration_seconds", 0)

        if not isinstance(duration_seconds, int) or isinstance(duration_seconds, bool):
            duration_seconds = 0

        completion_deadline = (
            current_time
            + timedelta(seconds=max(0, min(duration_seconds, 300)))
            + PLAYBACK_COMPLETION_GRACE
        )
        command.expires_at = max(command.expires_at, completion_deadline)
    elif status in {CameraCommandStatus.COMPLETED, CameraCommandStatus.FAILED}:
        if command.status not in {
            CameraCommandStatus.CLAIMED,
            CameraCommandStatus.STARTED,
        }:
            raise CameraCommandError("invalid_command_transition")
        await _delete_command_media(command)
        command.status = status
        command.completed_at = current_time
        command.error_code = error_code if status == CameraCommandStatus.FAILED else None
    else:
        raise CameraCommandError("invalid_command_transition")

    await session.commit()
    return command


async def _expire_command(
    command: CameraCommand,
    current_time: datetime,
) -> None:
    await _delete_command_media(command)
    command.status = CameraCommandStatus.EXPIRED
    command.completed_at = current_time
    command.error_code = "command_expired"
    command.payload = {"source": command.payload.get("source", "unknown")}


async def _delete_command_media(command: CameraCommand) -> None:
    if command.media_storage_path is not None:
        await asyncio.to_thread(
            Path(command.media_storage_path).unlink,
            missing_ok=True,
        )
        command.media_storage_path = None
