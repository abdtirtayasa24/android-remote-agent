from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import Camera, CameraHeartbeat, Image, MotionEvent
from timelapse.models.enums import ImageStorageState


@dataclass(frozen=True)
class CameraStatusSummary:
    slug: str
    display_name: str
    health_state: str
    last_capture_at: datetime | None
    last_upload_at: datetime | None
    last_heartbeat_at: datetime | None
    pending_queue_count: int | None
    battery_percent: int | None
    available_storage_bytes: int | None
    latest_motion_at: datetime | None


async def load_camera_status(
    *,
    session: AsyncSession,
    camera_slug: str | None = None,
) -> list[CameraStatusSummary]:
    query = select(Camera).order_by(Camera.slug)

    if camera_slug is not None:
        query = query.where(Camera.slug == camera_slug)

    cameras = (await session.scalars(query)).all()
    summaries: list[CameraStatusSummary] = []

    for camera in cameras:
        heartbeat = await session.scalar(
            select(CameraHeartbeat)
            .where(CameraHeartbeat.camera_id == camera.id)
            .order_by(CameraHeartbeat.received_at.desc())
            .limit(1)
        )
        latest_motion = await session.scalar(
            select(MotionEvent)
            .where(MotionEvent.camera_id == camera.id)
            .order_by(MotionEvent.last_detected_at_utc.desc())
            .limit(1)
        )
        summaries.append(
            CameraStatusSummary(
                slug=camera.slug,
                display_name=camera.display_name,
                health_state=camera.health_state.value,
                last_capture_at=camera.last_capture_at,
                last_upload_at=camera.last_upload_at,
                last_heartbeat_at=heartbeat.received_at if heartbeat else None,
                pending_queue_count=heartbeat.pending_image_count if heartbeat else None,
                battery_percent=heartbeat.battery_percent if heartbeat else None,
                available_storage_bytes=(
                    heartbeat.available_storage_bytes if heartbeat else None
                ),
                latest_motion_at=(
                    latest_motion.last_detected_at_utc if latest_motion else None
                ),
            )
        )

    return summaries


async def load_latest_stored_image(
    *,
    session: AsyncSession,
    camera_slug: str | None = None,
) -> Image | None:
    query = (
        select(Image)
        .join(Camera, Camera.id == Image.camera_id)
        .where(Image.storage_state == ImageStorageState.STORED)
        .where(Image.deleted_at.is_(None))
        .order_by(Image.captured_at_utc.desc(), Image.id.desc())
        .limit(1)
    )

    if camera_slug is not None:
        query = query.where(Camera.slug == camera_slug)

    return await session.scalar(query)


def format_camera_status(summaries: list[CameraStatusSummary]) -> str:
    if not summaries:
        return "No cameras found."

    return "\n\n".join(_format_one_camera_status(summary) for summary in summaries)


def _format_one_camera_status(summary: CameraStatusSummary) -> str:
    lines = [
        f"Camera status — {summary.display_name}",
        f"Camera: {summary.slug}",
        f"Health: {summary.health_state}",
        f"Last capture: {_format_optional_utc(summary.last_capture_at)}",
        f"Last upload: {_format_optional_utc(summary.last_upload_at)}",
        f"Last heartbeat: {_format_optional_utc(summary.last_heartbeat_at)}",
        f"Queue: {_format_optional_int(summary.pending_queue_count)} pending",
        f"Battery: {_format_optional_int(summary.battery_percent)}%",
        f"Phone storage: {_format_optional_bytes(summary.available_storage_bytes)}",
        f"Latest motion: {_format_optional_utc(summary.latest_motion_at)}",
    ]
    return "\n".join(lines)


def _format_optional_utc(value: datetime | None) -> str:
    if value is None:
        return "unknown"

    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_optional_int(value: int | None) -> str:
    if value is None:
        return "unknown"

    return str(value)


def _format_optional_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"

    gib = value / (1024 * 1024 * 1024)
    return f"{gib:.1f} GiB"
