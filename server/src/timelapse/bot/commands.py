from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.bot.authorization import AuthorizedTelegramUser
from timelapse.bot.date_parser import ExportDateParseError, parse_images_command_args
from timelapse.configuration import get_settings
from timelapse.services.camera_status import (
    format_camera_status,
    load_camera_status,
    load_latest_stored_image,
)
from timelapse.services.export_requests import (
    ExportRequest,
    ExportRequestError,
    cancel_export_job,
    create_export_request,
    list_recent_export_jobs,
)
from timelapse.services.storage_pressure import get_storage_pressure_state


class TelegramCommandSender(Protocol):
    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
    ) -> int | None: ...

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str | None = None,
    ) -> int | None: ...

    async def send_document(
        self,
        *,
        chat_id: int,
        document_path: Path,
        caption: str | None = None,
    ) -> int | None: ...


HELP_TEXT = "\n".join(
    (
        "Android Time-Lapse Camera commands:",
        "/help - Show this help message",
        "/status [camera] - Show camera health summary",
        "/latest [camera] - Send latest stored image",
        "/images YYYY-MM-DD HH:mm YYYY-MM-DD HH:mm [camera] - Request ZIP export",
        "/exports - List your recent export jobs",
        "/cancel <job-id> - Cancel a pending export (administrator only)",
    )
)


def handle_help_command(*, role: str) -> str:
    if role == "administrator":
        return HELP_TEXT

    return "\n".join(line for line in HELP_TEXT.splitlines() if not line.startswith("/cancel"))


async def handle_status_command(
    *,
    session: AsyncSession,
    args: list[str],
) -> str:
    summaries = await load_camera_status(
        session=session,
        camera_slug=args[0] if args else None,
    )
    return format_camera_status(summaries)


async def handle_latest_command(
    *,
    session: AsyncSession,
    args: list[str],
    chat_id: int,
    sender: TelegramCommandSender,
) -> str:
    image = await load_latest_stored_image(
        session=session,
        camera_slug=args[0] if args else None,
    )

    if image is None:
        return "No stored images found."

    caption = f"Latest image captured at {image.captured_at_utc:%Y-%m-%d %H:%M:%S} UTC"
    await sender.send_photo(
        chat_id=chat_id,
        photo_path=Path(image.storage_path),
        caption=caption,
    )
    return "Latest image sent."


async def handle_images_command(
    *,
    session: AsyncSession,
    args: list[str],
    user: AuthorizedTelegramUser,
) -> str:
    try:
        parsed = parse_images_command_args(args)
    except ExportDateParseError as error:
        return f"Invalid export request: {error.code}\nUsage: {error.usage}"

    settings = get_settings()
    try:
        job = await create_export_request(
            session=session,
            storage_pressure_state=get_storage_pressure_state(settings=settings),
            request=ExportRequest(
                requested_by_user_id=user.telegram_user_id,
                destination_chat_id=user.telegram_chat_id,
                start_at_utc=parsed.start_at_utc,
                end_at_utc=parsed.end_at_utc,
                camera_slug=parsed.camera_slug,
            ),
        )
    except ExportRequestError as error:
        return f"Export request rejected: {error.code}"

    return f"Export queued: {job.id} ({job.matching_image_count or 0} images)."


async def handle_exports_command(
    *,
    session: AsyncSession,
    user: AuthorizedTelegramUser,
) -> str:
    jobs = await list_recent_export_jobs(
        session=session,
        requested_by_user_id=user.telegram_user_id,
        limit=5,
    )

    if not jobs:
        return "No export jobs found."

    return "\n".join(
        f"{job.id} — {job.status.value} — {job.matching_image_count or 0} images"
        for job in jobs
    )


async def handle_cancel_command(
    *,
    session: AsyncSession,
    args: list[str],
    user: AuthorizedTelegramUser,
) -> str:
    if user.role != "administrator":
        raise PermissionError("administrator_required")

    if len(args) != 1:
        return "Usage: /cancel <job-id>"

    try:
        job_id = UUID(args[0])
    except ValueError:
        return "Usage: /cancel <job-id>"

    try:
        job = await cancel_export_job(
            session=session,
            job_id=job_id,
            requester_role=user.role,
        )
    except ExportRequestError as error:
        raise ValueError(error.code) from error

    return f"Export cancelled: {job.id}."
