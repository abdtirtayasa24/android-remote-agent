from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import (
    Camera,
    Image,
    TimelapseVideoJob,
    TimelapseVideoJobImage,
)
from timelapse.models.enums import CaptureSource, ImageStorageState, JobStatus
from timelapse.services.timelapse_video_requests import create_due_video_jobs_once

DUE_AT = datetime(2026, 7, 16, 17, 10, tzinfo=UTC)
DAY_START = datetime(2026, 7, 15, 17, 0, tzinfo=UTC)
DAY_END = datetime(2026, 7, 16, 17, 0, tzinfo=UTC)


async def add_image(*, camera_id, storage_root: Path, captured_at: datetime) -> Image:
    capture_id = uuid4()
    image_path = storage_root / f"{capture_id}.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"test-jpeg")
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


async def camera_id_for_slug(slug: str):
    session_factory = get_session_factory()
    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == slug))
        return camera.id


async def test_daily_video_snapshot_uses_previous_jakarta_day_and_is_idempotent(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_camera(slug="front-door")
    camera_id = await camera_id_for_slug("front-door")

    before = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=DAY_START - timedelta(seconds=1),
    )
    first = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=DAY_START,
    )
    second = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=DAY_END - timedelta(seconds=1),
    )
    at_end = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=DAY_END,
    )

    async with session_scope() as session:
        first_count = await create_due_video_jobs_once(
            session=session,
            now=DUE_AT,
            recipient_chat_ids=(123,),
        )
    async with session_scope() as session:
        second_count = await create_due_video_jobs_once(
            session=session,
            now=DUE_AT,
            recipient_chat_ids=(123,),
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        jobs = (await session.scalars(select(TimelapseVideoJob))).all()
        snapshot = (
            await session.scalars(
                select(TimelapseVideoJobImage).order_by(TimelapseVideoJobImage.ordinal)
            )
        ).all()

    assert before.id is not None
    assert at_end.id is not None
    assert first_count == 1
    assert second_count == 0
    assert len(jobs) == 1
    assert jobs[0].local_date_jakarta.isoformat() == "2026-07-16"
    assert jobs[0].start_at_utc == DAY_START
    assert jobs[0].end_at_utc == DAY_END
    assert jobs[0].status == JobStatus.PENDING
    assert jobs[0].image_count == 2
    assert [row.image_id for row in snapshot] == [first.id, second.id]
    assert [row.ordinal for row in snapshot] == [1, 2]


async def test_daily_video_snapshot_defers_when_any_image_is_locked(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_camera(slug="front-door")
    camera_id = await camera_id_for_slug("front-door")
    locked = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=DAY_START,
    )
    await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=DAY_START + timedelta(minutes=1),
    )
    session_factory = get_session_factory()

    async with session_factory() as locking_session:
        async with locking_session.begin():
            await locking_session.scalar(
                select(Image).where(Image.id == locked.id).with_for_update()
            )

            async with session_scope() as session:
                created_count = await asyncio.wait_for(
                    create_due_video_jobs_once(
                        session=session,
                        now=DUE_AT,
                        recipient_chat_ids=(123,),
                    ),
                    timeout=1,
                )

    async with session_factory() as session:
        jobs = (await session.scalars(select(TimelapseVideoJob))).all()

    assert created_count == 0
    assert jobs == []


async def test_daily_video_job_is_not_created_without_recipients(
    create_camera,
) -> None:
    await create_camera(slug="front-door")

    async with session_scope() as session:
        created_count = await create_due_video_jobs_once(
            session=session,
            now=DUE_AT,
            recipient_chat_ids=(),
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        jobs = (await session.scalars(select(TimelapseVideoJob))).all()

    assert created_count == 0
    assert jobs == []


async def test_daily_video_job_fails_stably_when_day_has_no_images(
    create_camera,
) -> None:
    await create_camera(slug="empty-camera")

    async with session_scope() as session:
        created_count = await create_due_video_jobs_once(
            session=session,
            now=DUE_AT,
            recipient_chat_ids=(123,),
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await session.scalar(select(TimelapseVideoJob))

    assert created_count == 1
    assert job.status == JobStatus.FAILED
    assert job.error_code == "no_images"
    assert job.image_count == 0
