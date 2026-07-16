from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import (
    AuditEvent,
    Camera,
    ExportJob,
    ExportJobImage,
    Image,
    MotionAnalysis,
)
from timelapse.models.enums import (
    AnalysisStatus,
    CaptureSource,
    ImageStorageState,
    JobStatus,
)
from timelapse.services.storage_pressure import StoragePressureState

ACTIVE_EXPORT_STATUSES = {
    JobStatus.PENDING,
    JobStatus.PROCESSING,
    JobStatus.UPLOADING,
}
ACTIVE_ANALYSIS_STATUSES = {
    AnalysisStatus.PENDING,
    AnalysisStatus.PROCESSING,
}


async def process_retention_once(
    *,
    session: AsyncSession,
    now: datetime,
    batch_size: int = 100,
) -> int:
    candidates = await _load_retention_candidates(
        session=session,
        now=now,
        batch_size=batch_size,
    )
    deleted_count = 0

    for image in candidates:
        if await _delete_image_file_and_tombstone(
            session=session,
            image=image,
            now=now,
            event_prefix="retention",
        ):
            deleted_count += 1

    return deleted_count


async def process_emergency_cleanup_once(
    *,
    session: AsyncSession,
    now: datetime,
    pressure_state: Callable[[], StoragePressureState],
    batch_size: int = 100,
) -> int:
    deleted_count = 0

    while pressure_state() != StoragePressureState.NORMAL:
        candidates = await _load_emergency_cleanup_candidates(
            session=session,
            batch_size=batch_size,
        )

        if not candidates:
            break

        pass_deleted_count = 0

        for image in candidates:
            if await _delete_image_file_and_tombstone(
                session=session,
                image=image,
                now=now,
                event_prefix="retention.emergency",
            ):
                deleted_count += 1
                pass_deleted_count += 1

                if pressure_state() == StoragePressureState.NORMAL:
                    return deleted_count

        if pass_deleted_count == 0:
            break

    return deleted_count


async def _load_retention_candidates(
    *,
    session: AsyncSession,
    now: datetime,
    batch_size: int,
) -> list[Image]:
    rows = (
        await session.execute(
            select(Image, Camera)
            .join(Camera, Camera.id == Image.camera_id)
            .where(Image.storage_state == ImageStorageState.STORED)
            .where(Image.deleted_at.is_(None))
            .order_by(Image.captured_at_utc, Image.id)
            .limit(batch_size * 5)
            .with_for_update(of=Image, skip_locked=True)
        )
    ).all()

    candidates: list[Image] = []

    for image, camera in rows:
        retention_cutoff = now - timedelta(days=camera.retention_days)
        if image.captured_at_utc >= retention_cutoff:
            continue
        if await _is_protected(session=session, image=image, now=now):
            continue

        candidates.append(image)
        if len(candidates) >= batch_size:
            break

    return candidates


async def _load_emergency_cleanup_candidates(
    *,
    session: AsyncSession,
    batch_size: int,
) -> list[Image]:
    rows = (
        await session.scalars(
            select(Image)
            .where(Image.storage_state == ImageStorageState.STORED)
            .where(Image.deleted_at.is_(None))
            .where(Image.capture_source == CaptureSource.SCHEDULED)
            .order_by(Image.captured_at_utc, Image.id)
            .limit(batch_size * 5)
            .with_for_update(of=Image, skip_locked=True)
        )
    ).all()

    candidates: list[Image] = []

    for image in rows:
        if await _is_protected(session=session, image=image, now=None):
            continue

        candidates.append(image)
        if len(candidates) >= batch_size:
            break

    return candidates


async def _is_protected(
    *,
    session: AsyncSession,
    image: Image,
    now: datetime | None,
) -> bool:
    export_query = (
        select(ExportJobImage.image_id)
        .join(ExportJob, ExportJob.id == ExportJobImage.export_job_id)
        .where(ExportJobImage.image_id == image.id)
        .where(ExportJob.status.in_(ACTIVE_EXPORT_STATUSES))
        .limit(1)
    )

    if now is not None:
        export_query = export_query.where(ExportJob.expires_at > now)

    if await session.scalar(export_query) is not None:
        return True

    if (
        await session.scalar(
            select(MotionAnalysis.image_id)
            .where(MotionAnalysis.image_id == image.id)
            .where(MotionAnalysis.status.in_(ACTIVE_ANALYSIS_STATUSES))
            .limit(1)
        )
        is not None
    ):
        return True

    return False


async def _delete_image_file_and_tombstone(
    *,
    session: AsyncSession,
    image: Image,
    now: datetime,
    event_prefix: str,
) -> bool:
    image.storage_state = ImageStorageState.DELETING
    await session.flush()

    path = Path(image.storage_path)

    try:
        await asyncio.to_thread(path.unlink)
    except FileNotFoundError:
        image.storage_state = ImageStorageState.MISSING
        image.deleted_at = now
        session.add(
            AuditEvent(
                occurred_at=now,
                event_type=f"{event_prefix}.image_deleted",
                camera_id=image.camera_id,
                outcome="success",
                details={
                    "image_id": str(image.id),
                    "reason": "file_missing",
                },
            )
        )
        return True
    except OSError as exc:
        image.storage_state = ImageStorageState.STORED
        image.deleted_at = None
        session.add(
            AuditEvent(
                occurred_at=now,
                event_type=f"{event_prefix}.image_delete_failed",
                camera_id=image.camera_id,
                outcome="failure",
                details={
                    "image_id": str(image.id),
                    "error_code": type(exc).__name__,
                },
            )
        )
        return False

    image.storage_state = ImageStorageState.MISSING
    image.deleted_at = now
    session.add(
        AuditEvent(
            occurred_at=now,
            event_type=f"{event_prefix}.image_deleted",
            camera_id=image.camera_id,
            outcome="success",
            details={
                "image_id": str(image.id),
                "reason": "retention_expired",
            },
        )
    )
    return True
