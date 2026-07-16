from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest
import timelapse.services.export_worker as export_worker
from sqlalchemy import select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import Camera, ExportJob, ExportPart, Image
from timelapse.models.enums import CaptureSource, ImageStorageState, JobStatus
from timelapse.services.export_requests import ExportRequest, create_export_request
from timelapse.services.export_worker import process_due_export_jobs_once

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@dataclass
class FakeDocumentSender:
    documents: list[tuple[int, Path, str | None]] = field(default_factory=list)

    async def send_document(
        self,
        *,
        chat_id: int,
        document_path: Path,
        caption: str | None = None,
    ) -> int:
        self.documents.append((chat_id, document_path, caption))
        return len(self.documents)


def write_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full((180, 320, 3), 40, dtype=np.uint8)
    assert cv2.imwrite(str(path), image)


async def add_image(*, camera_id, storage_root: Path, captured_at: datetime) -> Image:
    capture_id = uuid4()
    image_path = storage_root / f"{capture_id}.jpg"
    write_jpeg(image_path)
    image_bytes = image_path.read_bytes()

    async with session_scope() as session:
        image = Image(
            capture_id=capture_id,
            camera_id=camera_id,
            captured_at_utc=captured_at,
            received_at_utc=captured_at,
            capture_source=CaptureSource.SCHEDULED,
            storage_state=ImageStorageState.STORED,
            storage_path=str(image_path),
            mime_type="image/jpeg",
            file_size_bytes=len(image_bytes),
            width_pixels=320,
            height_pixels=180,
            sha256=hashlib.sha256(image_bytes).hexdigest(),
        )
        session.add(image)
        await session.flush()
        return image


async def create_job(create_camera, tmp_path: Path) -> ExportJob:
    camera_fixture = await create_camera(slug="front-door")
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    await add_image(camera_id=camera_id, storage_root=tmp_path, captured_at=NOW)
    await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW + timedelta(minutes=1),
    )

    async with session_scope() as session:
        return await create_export_request(
            session=session,
            request=ExportRequest(
                requested_by_user_id=123,
                destination_chat_id=456,
                start_at_utc=NOW,
                end_at_utc=NOW + timedelta(minutes=2),
                camera_slug="front-door",
            ),
        )


async def test_export_worker_builds_manifest_sends_and_deletes_parts(
    create_camera,
    tmp_path: Path,
) -> None:
    job = await create_job(create_camera, tmp_path)
    sender = FakeDocumentSender()

    async with session_scope() as session:
        processed_count = await process_due_export_jobs_once(
            session=session,
            storage_root=tmp_path / "exports",
            sender=sender,
            now=NOW,
        )

    assert processed_count == 1
    assert len(sender.documents) == 1
    sent_path = sender.documents[0][1]
    assert not sent_path.exists()

    session_factory = get_session_factory()

    async with session_factory() as session:
        stored_job = await session.get(ExportJob, job.id)
        parts = (await session.scalars(select(ExportPart))).all()

    assert stored_job.status == JobStatus.COMPLETED
    assert stored_job.completed_part_count == 1
    assert parts[0].status == "deleted"
    assert parts[0].file_size_bytes <= 45 * 1024 * 1024


@dataclass
class InspectingDocumentSender:
    observed_statuses: list[str | None] = field(default_factory=list)

    async def send_document(
        self,
        *,
        chat_id: int,
        document_path: Path,
        caption: str | None = None,
    ) -> int:
        session_factory = get_session_factory()

        async with session_factory() as session:
            part = await session.scalar(
                select(ExportPart).where(ExportPart.storage_path == str(document_path))
            )
            self.observed_statuses.append(part.status if part is not None else None)

        return len(self.observed_statuses)


async def test_export_worker_commits_uploading_state_before_network_send(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_job(create_camera, tmp_path)
    sender = InspectingDocumentSender()

    async with session_scope() as session:
        await process_due_export_jobs_once(
            session=session,
            storage_root=tmp_path / "exports",
            sender=sender,
            now=NOW,
        )

    assert sender.observed_statuses == ["uploading"]


async def test_export_worker_does_not_resend_after_send_succeeds_but_delete_fails(
    create_camera,
    tmp_path: Path,
    monkeypatch,
) -> None:
    job = await create_job(create_camera, tmp_path)
    sender = FakeDocumentSender()

    async def fail_delete(function, /, *args, **kwargs):
        raise OSError("delete failed")

    monkeypatch.setattr(export_worker.asyncio, "to_thread", fail_delete)

    with pytest.raises(OSError, match="delete failed"):
        async with session_scope() as session:
            await process_due_export_jobs_once(
                session=session,
                storage_root=tmp_path / "exports",
                sender=sender,
                now=NOW,
            )

    assert len(sender.documents) == 1

    session_factory = get_session_factory()

    async with session_factory() as session:
        part = await session.scalar(
            select(ExportPart).where(ExportPart.export_job_id == job.id)
        )

    assert part.status == "sent"

    monkeypatch.undo()

    async with session_scope() as session:
        await process_due_export_jobs_once(
            session=session,
            storage_root=tmp_path / "exports",
            sender=sender,
            now=NOW + timedelta(minutes=1),
        )

    assert len(sender.documents) == 1

    async with session_factory() as session:
        part = await session.scalar(
            select(ExportPart).where(ExportPart.export_job_id == job.id)
        )
        stored_job = await session.get(ExportJob, job.id)

    assert part.status == "deleted"
    assert stored_job.status == JobStatus.COMPLETED


async def test_export_worker_resumes_first_unsent_part(create_camera, tmp_path: Path) -> None:
    job = await create_job(create_camera, tmp_path)
    sender = FakeDocumentSender()

    async with session_scope() as session:
        first_count = await process_due_export_jobs_once(
            session=session,
            storage_root=tmp_path / "exports",
            sender=None,
            now=NOW,
        )

    assert first_count == 1

    async with session_scope() as session:
        second_count = await process_due_export_jobs_once(
            session=session,
            storage_root=tmp_path / "exports",
            sender=sender,
            now=NOW + timedelta(minutes=1),
        )

    assert second_count == 1
    assert len(sender.documents) == 1

    session_factory = get_session_factory()

    async with session_factory() as session:
        stored_job = await session.get(ExportJob, job.id)

    assert stored_job.status == JobStatus.COMPLETED
