from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import (
    Camera,
    Image,
    TimelapseVideoDelivery,
    TimelapseVideoJob,
    TimelapseVideoJobImage,
)
from timelapse.models.enums import JobStatus
from timelapse.services.storage_pressure import StoragePressureState
from timelapse.services.timelapse_video_generator import (
    GeneratedTimelapseVideo,
    TimelapseVideoGenerationError,
    build_timelapse_video,
)

LOGGER = logging.getLogger(__name__)
TELEGRAM_VIDEO_MAXIMUM_BYTES = 45 * 1024 * 1024
DEFAULT_STALE_AFTER = timedelta(hours=1)
VideoBuilder = Callable[..., GeneratedTimelapseVideo]


class TimelapseVideoSender(Protocol):
    async def send_video(
        self,
        *,
        chat_id: int,
        video_path: Path,
        caption: str | None = None,
    ) -> int | None: ...


async def process_due_video_jobs_once(
    *,
    session: AsyncSession,
    output_directory: Path,
    sender: TimelapseVideoSender | None,
    admin_user_id: int | None,
    now: datetime | None = None,
    frame_rate: int = 24,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    storage_pressure_state: StoragePressureState | None = None,
    batch_size: int = 2,
    maximum_video_bytes: int = TELEGRAM_VIDEO_MAXIMUM_BYTES,
    video_builder: VideoBuilder = build_timelapse_video,
) -> int:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    stale_before = current_time - stale_after
    under_pressure = storage_pressure_state in {
        StoragePressureState.SEVERE,
        StoragePressureState.HARD_LIMIT,
    }
    completed_cleanup = (
        TimelapseVideoJob.status == JobStatus.COMPLETED
    ) & TimelapseVideoJob.storage_path.is_not(None)
    lease_available = or_(
        TimelapseVideoJob.claimed_at.is_(None),
        TimelapseVideoJob.claimed_at < stale_before,
    )
    eligibility = completed_cleanup

    if under_pressure:
        pressure_cleanup = and_(
            TimelapseVideoJob.status.in_([JobStatus.PROCESSING, JobStatus.UPLOADING]),
            TimelapseVideoJob.storage_path.is_not(None),
            lease_available,
        )
        eligibility = or_(eligibility, pressure_cleanup)

    if sender is not None:
        active_claim = or_(
            TimelapseVideoJob.status == JobStatus.PENDING,
            and_(
                TimelapseVideoJob.status.in_([JobStatus.PROCESSING, JobStatus.UPLOADING]),
                lease_available,
            ),
        )
        eligibility = or_(completed_cleanup, active_claim)

    jobs = (
        await session.scalars(
            select(TimelapseVideoJob)
            .where(eligibility)
            .order_by(TimelapseVideoJob.created_at, TimelapseVideoJob.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()

    for job in jobs:
        if job.status != JobStatus.COMPLETED:
            job.status = JobStatus.PROCESSING
            job.claimed_at = current_time
            job.started_at = job.started_at or current_time

    await session.commit()
    processed_count = 0

    for job in jobs:
        if job.status == JobStatus.COMPLETED:
            await _delete_completed_video(
                session=session,
                job=job,
                now=current_time,
            )
            processed_count += 1
            continue

        if sender is None:
            if under_pressure:
                await _defer_job_for_pressure(
                    session=session,
                    job=job,
                    now=current_time,
                )
                processed_count += 1
            continue

        await _process_video_job(
            session=session,
            job=job,
            output_directory=output_directory,
            sender=sender,
            now=current_time,
            frame_rate=frame_rate,
            maximum_video_bytes=maximum_video_bytes,
            storage_pressure_state=storage_pressure_state,
            video_builder=video_builder,
        )
        processed_count += 1

    return processed_count


async def _process_video_job(
    *,
    session: AsyncSession,
    job: TimelapseVideoJob,
    output_directory: Path,
    sender: TimelapseVideoSender,
    now: datetime,
    frame_rate: int,
    maximum_video_bytes: int,
    storage_pressure_state: StoragePressureState | None,
    video_builder: VideoBuilder,
) -> None:
    camera = await session.get(Camera, job.camera_id)

    if camera is None:
        await _fail_job(
            session=session,
            job=job,
            error_code="camera_missing",
            now=now,
        )
        return

    generated = _load_existing_video(job)

    if storage_pressure_state in {
        StoragePressureState.SEVERE,
        StoragePressureState.HARD_LIMIT,
    }:
        await _defer_job_for_pressure(
            session=session,
            job=job,
            now=now,
        )
        return

    if generated is None:
        image_paths = await _snapshot_image_paths(session=session, job=job)

        if len(image_paths) != job.image_count:
            await _fail_job(
                session=session,
                job=job,
                error_code="snapshot_image_missing",
                now=now,
            )
            return

        try:
            generated = await asyncio.to_thread(
                video_builder,
                output_directory=output_directory,
                job_id=str(job.id),
                image_paths=image_paths,
                frame_rate=frame_rate,
            )
        except TimelapseVideoGenerationError as error:
            await _fail_job(
                session=session,
                job=job,
                error_code=error.code,
                now=now,
            )
            return

        job.storage_path = str(generated.path)
        job.file_size_bytes = generated.file_size_bytes
        job.sha256 = generated.sha256

    if generated.file_size_bytes > maximum_video_bytes:
        await asyncio.to_thread(generated.path.unlink, missing_ok=True)
        job.storage_path = None
        job.file_deleted_at = now
        await _fail_job(
            session=session,
            job=job,
            error_code="video_too_large",
            now=now,
        )
        return

    job.status = JobStatus.UPLOADING
    await session.commit()
    deliveries = (
        await session.scalars(
            select(TimelapseVideoDelivery)
            .where(TimelapseVideoDelivery.job_id == job.id)
            .order_by(TimelapseVideoDelivery.telegram_chat_id)
        )
    ).all()

    if not deliveries:
        await _fail_job(
            session=session,
            job=job,
            error_code="no_recipient",
            now=now,
        )
        return

    caption = (
        f"Daily time-lapse for {camera.display_name} "
        f"({camera.slug}) on {job.local_date_jakarta.isoformat()} "
        "Asia/Jakarta"
    )

    for delivery in deliveries:
        if delivery.status == "sent":
            continue

        try:
            message_id = await sender.send_video(
                chat_id=delivery.telegram_chat_id,
                video_path=generated.path,
                caption=caption,
            )
        except Exception as error:
            LOGGER.warning(
                "timelapse_video_delivery_failed "
                "job_id=%s camera_id=%s chat_id=%s error_type=%s",
                job.id,
                job.camera_id,
                delivery.telegram_chat_id,
                type(error).__name__,
            )
            delivery.error_code = "telegram_delivery_failed"
            job.status = JobStatus.UPLOADING
            job.claimed_at = None
            await session.commit()
            return

        delivery.status = "sent"
        delivery.telegram_message_id = message_id
        delivery.sent_at = now
        delivery.error_code = None
        job.telegram_message_id = message_id
        job.claimed_at = datetime.now(UTC)
        await session.commit()

    job.status = JobStatus.COMPLETED
    job.completed_at = now
    job.claimed_at = None
    job.error_code = None
    await session.commit()
    await _delete_completed_video(session=session, job=job, now=now)


def _load_existing_video(
    job: TimelapseVideoJob,
) -> GeneratedTimelapseVideo | None:
    if job.storage_path is None or job.file_size_bytes is None or job.sha256 is None:
        return None

    path = Path(job.storage_path)

    if not path.is_file():
        return None

    return GeneratedTimelapseVideo(
        path=path,
        file_size_bytes=job.file_size_bytes,
        sha256=job.sha256,
    )


async def _snapshot_image_paths(
    *,
    session: AsyncSession,
    job: TimelapseVideoJob,
) -> tuple[Path, ...]:
    rows = (
        await session.execute(
            select(Image.storage_path)
            .join(TimelapseVideoJobImage, TimelapseVideoJobImage.image_id == Image.id)
            .where(TimelapseVideoJobImage.job_id == job.id)
            .order_by(TimelapseVideoJobImage.ordinal)
        )
    ).scalars()
    return tuple(Path(path) for path in rows)


async def _defer_job_for_pressure(
    *,
    session: AsyncSession,
    job: TimelapseVideoJob,
    now: datetime,
) -> None:
    if job.storage_path is not None:
        await asyncio.to_thread(Path(job.storage_path).unlink, missing_ok=True)
        job.storage_path = None
        job.file_deleted_at = now

    job.status = JobStatus.PENDING
    job.claimed_at = None
    job.error_code = "storage_pressure_severe"
    await session.commit()


async def _delete_completed_video(
    *,
    session: AsyncSession,
    job: TimelapseVideoJob,
    now: datetime,
) -> None:
    if job.storage_path is not None:
        await asyncio.to_thread(Path(job.storage_path).unlink, missing_ok=True)

    job.storage_path = None
    job.file_deleted_at = now
    await session.commit()


async def _fail_job(
    *,
    session: AsyncSession,
    job: TimelapseVideoJob,
    error_code: str,
    now: datetime,
) -> None:
    if job.storage_path is not None:
        await asyncio.to_thread(Path(job.storage_path).unlink, missing_ok=True)
        job.storage_path = None
        job.file_deleted_at = now

    job.status = JobStatus.FAILED
    job.claimed_at = None
    job.error_code = error_code
    job.completed_at = now
    await session.commit()
