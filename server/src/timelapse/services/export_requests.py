from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import Camera, ExportJob, ExportJobImage, ExportPart, Image
from timelapse.models.enums import ImageStorageState, JobStatus


class ExportRequestError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExportRequest:
    requested_by_user_id: int
    destination_chat_id: int
    start_at_utc: datetime
    end_at_utc: datetime
    camera_slug: str | None = None


async def create_export_request(
    *,
    session: AsyncSession,
    request: ExportRequest,
) -> ExportJob:
    camera = await _resolve_export_camera(session=session, camera_slug=request.camera_slug)
    image_ids = (
        await session.scalars(
            select(Image.id)
            .where(Image.camera_id == camera.id)
            .where(Image.storage_state == ImageStorageState.STORED)
            .where(Image.deleted_at.is_(None))
            .where(Image.captured_at_utc >= request.start_at_utc)
            .where(Image.captured_at_utc < request.end_at_utc)
            .order_by(Image.captured_at_utc, Image.id)
        )
    ).all()
    job = ExportJob(
        requested_by_user_id=request.requested_by_user_id,
        destination_chat_id=request.destination_chat_id,
        camera_id=camera.id,
        start_at_utc=request.start_at_utc,
        end_at_utc=request.end_at_utc,
        status=JobStatus.PENDING,
        matching_image_count=len(image_ids),
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    session.add(job)
    await session.flush()

    for ordinal, image_id in enumerate(image_ids, start=1):
        session.add(
            ExportJobImage(
                export_job_id=job.id,
                image_id=image_id,
                ordinal=ordinal,
            )
        )

    return job


async def list_recent_export_jobs(
    *,
    session: AsyncSession,
    requested_by_user_id: int,
    limit: int = 5,
) -> list[ExportJob]:
    return list(
        (
            await session.scalars(
                select(ExportJob)
                .where(ExportJob.requested_by_user_id == requested_by_user_id)
                .order_by(ExportJob.created_at.desc(), ExportJob.id.desc())
                .limit(limit)
            )
        ).all()
    )


async def cancel_export_job(
    *,
    session: AsyncSession,
    job_id: UUID,
    requester_role: str,
) -> ExportJob:
    if requester_role != "administrator":
        raise ExportRequestError("administrator_required")

    job = await session.get(ExportJob, job_id, with_for_update=True)

    if job is None:
        raise ExportRequestError("export_not_found")

    sent_part = await session.scalar(
        select(ExportPart)
        .where(ExportPart.export_job_id == job.id)
        .where(ExportPart.status.in_(["uploading", "sent", "deleted"]))
        .limit(1)
    )

    if (
        sent_part is not None
        or job.completed_part_count > 0
        or job.status
        in {
            JobStatus.UPLOADING,
            JobStatus.COMPLETED,
        }
    ):
        raise ExportRequestError("export_upload_started")

    job.status = JobStatus.CANCELLED
    return job


async def _resolve_export_camera(
    *,
    session: AsyncSession,
    camera_slug: str | None,
) -> Camera:
    query = select(Camera).order_by(Camera.slug).limit(1)

    if camera_slug is not None:
        query = select(Camera).where(Camera.slug == camera_slug)

    camera = await session.scalar(query)

    if camera is None:
        raise ExportRequestError("camera_not_found")

    return camera
