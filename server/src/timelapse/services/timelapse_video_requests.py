from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import (
    Camera,
    Image,
    TimelapseVideoDelivery,
    TimelapseVideoJob,
    TimelapseVideoJobImage,
)
from timelapse.models.enums import CaptureSource, ImageStorageState, JobStatus
from timelapse.services.time_formatting import JAKARTA_TIME_ZONE


def jakarta_day_utc_window(local_date: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(local_date, time.min, tzinfo=JAKARTA_TIME_ZONE)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


async def create_due_video_jobs_once(
    *,
    session: AsyncSession,
    now: datetime,
    recipient_chat_ids: Sequence[int],
    send_hour_jakarta: int = 0,
    send_minute_jakarta: int = 10,
) -> int:
    if not recipient_chat_ids:
        return 0

    current_time = now.astimezone(UTC)
    local_now = current_time.astimezone(JAKARTA_TIME_ZONE)
    send_time = time(hour=send_hour_jakarta, minute=send_minute_jakarta)

    if local_now.time().replace(tzinfo=None) < send_time:
        return 0

    local_date = local_now.date() - timedelta(days=1)
    start_at_utc, end_at_utc = jakarta_day_utc_window(local_date)
    cameras = (
        await session.scalars(
            select(Camera)
            .where(Camera.enabled.is_(True))
            .order_by(Camera.slug)
            .with_for_update(skip_locked=True)
        )
    ).all()
    created_count = 0

    for camera in cameras:
        existing_job = await session.scalar(
            select(TimelapseVideoJob.id)
            .where(TimelapseVideoJob.camera_id == camera.id)
            .where(TimelapseVideoJob.local_date_jakarta == local_date)
            .limit(1)
        )

        if existing_job is not None:
            continue

        expected_image_ids = (
            await session.scalars(
                select(Image.id)
                .where(Image.camera_id == camera.id)
                .where(Image.capture_source == CaptureSource.SCHEDULED)
                .where(Image.storage_state == ImageStorageState.STORED)
                .where(Image.deleted_at.is_(None))
                .where(Image.captured_at_utc >= start_at_utc)
                .where(Image.captured_at_utc < end_at_utc)
                .order_by(Image.captured_at_utc, Image.id)
            )
        ).all()
        images = (
            await session.scalars(
                select(Image)
                .where(Image.id.in_(expected_image_ids))
                .order_by(Image.captured_at_utc, Image.id)
                .with_for_update(of=Image, skip_locked=True)
            )
        ).all()

        if [image.id for image in images] != expected_image_ids:
            continue
        job = TimelapseVideoJob(
            camera_id=camera.id,
            local_date_jakarta=local_date,
            start_at_utc=start_at_utc,
            end_at_utc=end_at_utc,
            status=JobStatus.PENDING if images else JobStatus.FAILED,
            image_count=len(images),
            completed_at=current_time if not images else None,
            error_code=None if images else "no_images",
        )
        session.add(job)
        await session.flush()

        if images:
            for chat_id in dict.fromkeys(recipient_chat_ids):
                session.add(
                    TimelapseVideoDelivery(
                        job_id=job.id,
                        telegram_chat_id=chat_id,
                    )
                )

        for ordinal, image in enumerate(images, start=1):
            session.add(
                TimelapseVideoJobImage(
                    job_id=job.id,
                    image_id=image.id,
                    ordinal=ordinal,
                )
            )

        created_count += 1

    return created_count
