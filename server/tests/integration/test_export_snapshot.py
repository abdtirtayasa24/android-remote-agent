from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from sqlalchemy import select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import Camera, ExportJob, ExportJobImage, Image
from timelapse.models.enums import CaptureSource, ImageStorageState, JobStatus
from timelapse.services.export_requests import ExportRequest, create_export_request

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


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


async def test_export_snapshot_skips_locked_images(
    create_camera,
    tmp_path: Path,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    locked = await add_image(camera_id=camera_id, storage_root=tmp_path, captured_at=NOW)

    async with session_factory() as locking_session:
        async with locking_session.begin():
            await locking_session.scalar(
                select(Image).where(Image.id == locked.id).with_for_update()
            )

            async with session_scope() as session:
                job = await asyncio.wait_for(
                    create_export_request(
                        session=session,
                        request=ExportRequest(
                            requested_by_user_id=123,
                            destination_chat_id=456,
                            start_at_utc=NOW - timedelta(minutes=1),
                            end_at_utc=NOW + timedelta(minutes=1),
                            camera_slug="front-door",
                        ),
                    ),
                    timeout=1,
                )

    async with session_factory() as session:
        snapshot_rows = (await session.scalars(select(ExportJobImage))).all()

    assert job.matching_image_count == 0
    assert snapshot_rows == []


async def test_export_snapshot_is_deterministic_and_half_open(
    create_camera, tmp_path: Path
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    before = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=1),
    )
    first = await add_image(camera_id=camera_id, storage_root=tmp_path, captured_at=NOW)
    second = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW + timedelta(minutes=1),
    )
    at_end = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW + timedelta(minutes=2),
    )

    async with session_scope() as session:
        job = await create_export_request(
            session=session,
            request=ExportRequest(
                requested_by_user_id=123,
                destination_chat_id=456,
                start_at_utc=NOW,
                end_at_utc=NOW + timedelta(minutes=2),
                camera_slug="front-door",
            ),
        )

    async with session_factory() as session:
        stored_job = await session.get(ExportJob, job.id)
        snapshot_rows = (
            await session.scalars(select(ExportJobImage).order_by(ExportJobImage.ordinal))
        ).all()

    assert before.id is not None
    assert at_end.id is not None
    assert stored_job.status == JobStatus.PENDING
    assert stored_job.matching_image_count == 2
    assert [row.ordinal for row in snapshot_rows] == [1, 2]
    assert [row.image_id for row in snapshot_rows] == [first.id, second.id]
