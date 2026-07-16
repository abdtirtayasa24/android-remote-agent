from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import AuditEvent, Camera, ExportJob, ExportPart, Image
from timelapse.models.enums import CaptureSource, ImageStorageState, JobStatus
from timelapse.services.reconciliation import process_reconciliation_once

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


async def path_exists(path: Path) -> bool:
    return await asyncio.to_thread(path.exists)


async def list_files(path: Path, pattern: str) -> list[Path]:
    return await asyncio.to_thread(lambda: list(path.rglob(pattern)))


async def get_camera_id(slug: str):
    session_factory = get_session_factory()
    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == slug))
        assert camera is not None
        return camera.id


async def add_image(
    *,
    camera_id,
    storage_path: Path,
    content: bytes,
    captured_at: datetime = NOW,
    storage_state: ImageStorageState = ImageStorageState.STORED,
) -> Image:
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(storage_path.write_bytes, content)

    async with session_scope() as session:
        image = Image(
            capture_id=uuid4(),
            camera_id=camera_id,
            captured_at_utc=captured_at,
            received_at_utc=captured_at,
            capture_source=CaptureSource.SCHEDULED,
            storage_state=storage_state,
            storage_path=str(storage_path),
            mime_type="image/jpeg",
            file_size_bytes=len(content),
            width_pixels=1,
            height_pixels=1,
            sha256=hashlib.sha256(content).hexdigest(),
        )
        session.add(image)
        await session.flush()
        return image


async def test_reconciliation_marks_missing_database_files(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    stored = await add_image(
        camera_id=camera_id,
        storage_path=tmp_path / "images" / "missing.jpg",
        content=b"image",
    )
    await asyncio.to_thread(Path(stored.storage_path).unlink)

    async with session_scope() as session:
        result = await process_reconciliation_once(
            session=session,
            storage_root=tmp_path,
            now=NOW,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored_image = await session.get(Image, stored.id)
        audit = await session.scalar(select(AuditEvent))

    assert result.missing_files == 1
    assert stored_image.storage_state == ImageStorageState.MISSING
    assert audit.event_type == "reconciliation.image_missing"


async def test_reconciliation_quarantines_orphaned_image_files(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_camera(slug="front-door")
    orphan = tmp_path / "images" / "front-door" / "orphan.jpg"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(orphan.write_bytes, b"orphan")

    async with session_scope() as session:
        result = await process_reconciliation_once(
            session=session,
            storage_root=tmp_path,
            now=NOW,
        )

    quarantine_files = await list_files(tmp_path / "quarantine" / "orphans", "*.jpg")

    assert result.orphaned_files == 1
    assert not await path_exists(orphan)
    assert len(quarantine_files) == 1
    assert quarantine_files[0].read_bytes() == b"orphan"


async def test_reconciliation_detects_size_and_checksum_mismatch(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    stored = await add_image(
        camera_id=camera_id,
        storage_path=tmp_path / "images" / "front-door" / "bad.jpg",
        content=b"expected",
    )
    await asyncio.to_thread(Path(stored.storage_path).write_bytes, b"changed")

    async with session_scope() as session:
        result = await process_reconciliation_once(
            session=session,
            storage_root=tmp_path,
            now=NOW,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        audit_events = (await session.scalars(select(AuditEvent))).all()

    assert result.mismatched_files == 1
    assert [event.event_type for event in audit_events] == ["reconciliation.image_mismatch"]


async def test_reconciliation_removes_stale_temp_and_old_export_files(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_camera(slug="front-door")
    stale_temp = tmp_path / "tmp" / "uploads" / "old.part"
    fresh_temp = tmp_path / "tmp" / "uploads" / "fresh.part"
    stale_export = tmp_path / "exports" / "old.zip"
    fresh_export = tmp_path / "exports" / "fresh.zip"

    for path in (stale_temp, fresh_temp, stale_export, fresh_export):
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, b"content")

    old_timestamp = (NOW - timedelta(hours=12)).timestamp()
    fresh_timestamp = (NOW - timedelta(minutes=5)).timestamp()
    for path in (stale_temp, stale_export):
        path.touch()
        path.chmod(0o600)
        import os

        os.utime(path, (old_timestamp, old_timestamp))
    for path in (fresh_temp, fresh_export):
        import os

        os.utime(path, (fresh_timestamp, fresh_timestamp))

    async with session_scope() as session:
        result = await process_reconciliation_once(
            session=session,
            storage_root=tmp_path,
            now=NOW,
            stale_temp_age=timedelta(hours=1),
            stale_export_age=timedelta(hours=6),
        )

    assert result.stale_temp_files == 1
    assert result.stale_export_files == 1
    assert not await path_exists(stale_temp)
    assert await path_exists(fresh_temp)
    assert not await path_exists(stale_export)
    assert await path_exists(fresh_export)


async def test_reconciliation_marks_old_staging_rows_missing(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    staging = await add_image(
        camera_id=camera_id,
        storage_path=tmp_path / "images" / "front-door" / "stale-staging.jpg",
        content=b"staging",
        captured_at=NOW - timedelta(hours=2),
        storage_state=ImageStorageState.STAGING,
    )

    async with session_scope() as session:
        result = await process_reconciliation_once(
            session=session,
            storage_root=tmp_path,
            now=NOW,
            stale_staging_age=timedelta(hours=1),
        )

    quarantine_files = await list_files(tmp_path / "quarantine" / "orphans", "*.jpg")

    session_factory = get_session_factory()
    async with session_factory() as session:
        stored_image = await session.get(Image, staging.id)

    assert result.stale_staging_rows == 1
    assert result.orphaned_files == 1
    assert stored_image.storage_state == ImageStorageState.MISSING
    assert not await path_exists(Path(staging.storage_path))
    assert len(quarantine_files) == 1


async def test_reconciliation_keeps_referenced_export_parts(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    export_path = tmp_path / "exports" / "referenced.zip"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(export_path.write_bytes, b"zip")

    async with session_scope() as session:
        job = ExportJob(
            requested_by_user_id=123,
            destination_chat_id=456,
            camera_id=camera_id,
            start_at_utc=NOW - timedelta(hours=1),
            end_at_utc=NOW,
            status=JobStatus.PROCESSING,
            expires_at=NOW + timedelta(hours=1),
        )
        session.add(job)
        await session.flush()
        session.add(
            ExportPart(
                export_job_id=job.id,
                part_number=1,
                storage_path=str(export_path),
                file_size_bytes=3,
                sha256=hashlib.sha256(b"zip").hexdigest(),
                status="created",
            )
        )

    async with session_scope() as session:
        result = await process_reconciliation_once(
            session=session,
            storage_root=tmp_path,
            now=NOW,
            stale_export_age=timedelta(seconds=0),
        )

    assert result.stale_export_files == 0
    assert await path_exists(export_path)
