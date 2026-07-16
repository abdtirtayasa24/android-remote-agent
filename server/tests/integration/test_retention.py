from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import (
    AuditEvent,
    Camera,
    ExportJob,
    ExportJobImage,
    Image,
    MotionAnalysis,
)
from timelapse.models.enums import AnalysisStatus, CaptureSource, ImageStorageState, JobStatus
from timelapse.services.retention import process_retention_once

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


async def get_camera_id(slug: str):
    session_factory = get_session_factory()
    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == slug))
        assert camera is not None
        return camera.id


async def add_image(
    *,
    camera_id,
    storage_root: Path,
    captured_at: datetime,
    capture_source: CaptureSource = CaptureSource.SCHEDULED,
    storage_state: ImageStorageState = ImageStorageState.STORED,
) -> Image:
    capture_id = uuid4()
    image_path = storage_root / f"{capture_id}.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"jpeg-bytes")
    image_bytes = image_path.read_bytes()

    async with session_scope() as session:
        image = Image(
            capture_id=capture_id,
            camera_id=camera_id,
            captured_at_utc=captured_at,
            received_at_utc=captured_at,
            capture_source=capture_source,
            storage_state=storage_state,
            storage_path=str(image_path),
            mime_type="image/jpeg",
            file_size_bytes=len(image_bytes),
            width_pixels=1,
            height_pixels=1,
            sha256=hashlib.sha256(image_bytes).hexdigest(),
        )
        session.add(image)
        await session.flush()
        return image


async def test_retention_deletes_expired_image_and_tombstones_metadata(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    expired = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=8),
    )

    async with session_scope() as session:
        deleted_count = await process_retention_once(session=session, now=NOW, batch_size=10)

    assert deleted_count == 1
    assert not await asyncio.to_thread(Path(expired.storage_path).exists)

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored_image = await session.get(Image, expired.id)
        audit_count = await session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "retention.image_deleted")
        )

    assert stored_image.storage_state == ImageStorageState.MISSING
    assert stored_image.deleted_at == NOW
    assert audit_count == 1


async def test_retention_skips_locked_images(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    locked = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=8),
    )
    session_factory = get_session_factory()

    async with session_factory() as locking_session:
        async with locking_session.begin():
            await locking_session.scalar(
                select(Image).where(Image.id == locked.id).with_for_update()
            )

            async with session_scope() as session:
                deleted_count = await asyncio.wait_for(
                    process_retention_once(session=session, now=NOW, batch_size=10),
                    timeout=1,
                )

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored_image = await session.get(Image, locked.id)

    assert deleted_count == 0
    assert await asyncio.to_thread(Path(locked.storage_path).exists)
    assert stored_image.storage_state == ImageStorageState.STORED
    assert stored_image.deleted_at is None


async def test_retention_keeps_images_referenced_by_active_export(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    active = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=8),
    )

    async with session_scope() as session:
        job = ExportJob(
            requested_by_user_id=123,
            destination_chat_id=456,
            camera_id=camera_id,
            start_at_utc=NOW - timedelta(days=8, hours=1),
            end_at_utc=NOW - timedelta(days=8) + timedelta(hours=1),
            status=JobStatus.PENDING,
            expires_at=NOW + timedelta(hours=1),
        )
        session.add(job)
        await session.flush()
        session.add(ExportJobImage(export_job_id=job.id, image_id=active.id, ordinal=1))

    async with session_scope() as session:
        deleted_count = await process_retention_once(session=session, now=NOW, batch_size=10)

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored_image = await session.get(Image, active.id)

    assert deleted_count == 0
    assert await asyncio.to_thread(Path(active.storage_path).exists)
    assert stored_image.storage_state == ImageStorageState.STORED
    assert stored_image.deleted_at is None


async def test_retention_treats_missing_file_as_success(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    expired = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=8),
    )
    await asyncio.to_thread(Path(expired.storage_path).unlink)

    async with session_scope() as session:
        deleted_count = await process_retention_once(session=session, now=NOW, batch_size=10)

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored_image = await session.get(Image, expired.id)
        audit = await session.scalar(select(AuditEvent))

    assert deleted_count == 1
    assert stored_image.storage_state == ImageStorageState.MISSING
    assert stored_image.deleted_at == NOW
    assert audit.details["reason"] == "file_missing"


async def test_retention_restores_stored_state_after_filesystem_error(
    create_camera,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    expired = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=8),
    )

    def fail_unlink(self: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    async with session_scope() as session:
        deleted_count = await process_retention_once(session=session, now=NOW, batch_size=10)

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored_image = await session.get(Image, expired.id)
        audit = await session.scalar(select(AuditEvent))

    assert deleted_count == 0
    assert stored_image.storage_state == ImageStorageState.STORED
    assert stored_image.deleted_at is None
    assert audit.event_type == "retention.image_delete_failed"


async def test_retention_keeps_images_with_pending_analysis(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    expired = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=8),
    )

    async with session_scope() as session:
        session.add(
            MotionAnalysis(
                image_id=expired.id,
                status=AnalysisStatus.PENDING,
                algorithm_version="frame-diff-v1",
            )
        )

    async with session_scope() as session:
        deleted_count = await process_retention_once(session=session, now=NOW, batch_size=10)

    assert deleted_count == 0
    assert await asyncio.to_thread(Path(expired.storage_path).exists)
