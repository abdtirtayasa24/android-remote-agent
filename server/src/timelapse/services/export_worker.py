from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import ExportJob, ExportJobImage, ExportPart, Image
from timelapse.models.enums import JobStatus
from timelapse.services.export_zip import ExportZipImage, build_export_zip_parts

TELEGRAM_EXPORT_PART_BYTES = 45 * 1024 * 1024


class ExportDocumentSender(Protocol):
    async def send_document(
        self,
        *,
        chat_id: int,
        document_path: Path,
        caption: str | None = None,
    ) -> int | None: ...


async def process_due_export_jobs_once(
    *,
    session: AsyncSession,
    storage_root: Path,
    sender: ExportDocumentSender | None,
    now: datetime | None = None,
    batch_size: int = 5,
    maximum_part_bytes: int = TELEGRAM_EXPORT_PART_BYTES,
) -> int:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    jobs = (
        await session.scalars(
            select(ExportJob)
            .where(ExportJob.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]))
            .order_by(ExportJob.created_at, ExportJob.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    processed_count = 0

    for job in jobs:
        await process_export_job(
            session=session,
            job=job,
            storage_root=storage_root,
            sender=sender,
            now=current_time,
            maximum_part_bytes=maximum_part_bytes,
        )
        processed_count += 1

    return processed_count


async def process_export_job(
    *,
    session: AsyncSession,
    job: ExportJob,
    storage_root: Path,
    sender: ExportDocumentSender | None,
    now: datetime,
    maximum_part_bytes: int = TELEGRAM_EXPORT_PART_BYTES,
) -> None:
    job.status = JobStatus.PROCESSING
    job.claimed_at = now

    existing_parts = (
        await session.scalars(
            select(ExportPart)
            .where(ExportPart.export_job_id == job.id)
            .order_by(ExportPart.part_number)
        )
    ).all()

    if not existing_parts:
        existing_parts = await _build_and_store_parts(
            session=session,
            job=job,
            storage_root=storage_root,
            maximum_part_bytes=maximum_part_bytes,
        )
        await session.commit()

    if sender is None:
        await session.commit()
        return

    for part in existing_parts:
        if part.status == "deleted":
            continue

        if part.status == "sent":
            await _delete_sent_part(
                session=session,
                job=job,
                part=part,
                part_count=len(existing_parts),
                now=now,
            )
            continue

        part.status = "uploading"
        await session.commit()

        message_id = await sender.send_document(
            chat_id=job.destination_chat_id,
            document_path=Path(part.storage_path),
            caption=f"Export {job.id} part {part.part_number}",
        )
        part.telegram_message_id = message_id
        part.sent_at = now
        part.status = "sent"
        job.completed_part_count += 1
        await session.commit()

        await _delete_sent_part(
            session=session,
            job=job,
            part=part,
            part_count=len(existing_parts),
            now=now,
        )

    if job.completed_part_count >= len(existing_parts):
        job.status = JobStatus.COMPLETED
        job.completed_at = now
        await session.commit()


async def _delete_sent_part(
    *,
    session: AsyncSession,
    job: ExportJob,
    part: ExportPart,
    part_count: int,
    now: datetime,
) -> None:
    await asyncio.to_thread(Path(part.storage_path).unlink, missing_ok=True)
    part.status = "deleted"

    if job.completed_part_count >= part_count:
        job.status = JobStatus.COMPLETED
        job.completed_at = now

    await session.commit()


async def _build_and_store_parts(
    *,
    session: AsyncSession,
    job: ExportJob,
    storage_root: Path,
    maximum_part_bytes: int,
) -> list[ExportPart]:
    snapshot_rows = (
        await session.scalars(
            select(ExportJobImage)
            .where(ExportJobImage.export_job_id == job.id)
            .order_by(ExportJobImage.ordinal)
        )
    ).all()
    zip_images: list[ExportZipImage] = []

    for snapshot_row in snapshot_rows:
        image = await session.get(Image, snapshot_row.image_id)

        if image is None:
            continue

        zip_images.append(
            ExportZipImage(
                ordinal=snapshot_row.ordinal,
                filename=f"{snapshot_row.ordinal:06d}_{image.capture_id}.jpg",
                path=Path(image.storage_path),
                sha256=image.sha256,
            )
        )

    built_parts = build_export_zip_parts(
        output_dir=storage_root / str(job.id),
        job_id=str(job.id),
        images=tuple(zip_images),
        maximum_part_bytes=maximum_part_bytes,
    )
    stored_parts: list[ExportPart] = []

    for built_part in built_parts:
        part = ExportPart(
            export_job_id=job.id,
            part_number=built_part.part_number,
            storage_path=str(built_part.path),
            file_size_bytes=built_part.file_size_bytes,
            sha256=built_part.sha256,
            status="created",
        )
        session.add(part)
        stored_parts.append(part)

    await session.flush()
    return stored_parts
