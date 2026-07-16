from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest
from sqlalchemy import select
from timelapse.bot.authorization import AuthorizedTelegramUser
from timelapse.bot.commands import (
    handle_cancel_command,
    handle_exports_command,
    handle_images_command,
)
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import Camera, ExportJob, ExportPart, Image
from timelapse.models.enums import CaptureSource, ImageStorageState, JobStatus
from timelapse.services.storage_pressure import StoragePressureState

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
ADMIN = AuthorizedTelegramUser(
    telegram_user_id=123, telegram_chat_id=456, role="administrator"
)
VIEWER = AuthorizedTelegramUser(telegram_user_id=321, telegram_chat_id=654, role="viewer")


def write_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full((180, 320, 3), 40, dtype=np.uint8)
    assert cv2.imwrite(str(path), image)


async def add_image(*, camera_id, storage_root: Path, captured_at: datetime) -> None:
    capture_id = uuid4()
    image_path = storage_root / f"{capture_id}.jpg"
    write_jpeg(image_path)
    image_bytes = image_path.read_bytes()

    async with session_scope() as session:
        session.add(
            Image(
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
        )


async def test_images_command_creates_export_job(create_camera, tmp_path: Path) -> None:
    camera_fixture = await create_camera(slug="front-door")
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=datetime(2026, 7, 16, 16, 45, tzinfo=UTC),
    )

    async with session_scope() as session:
        text = await handle_images_command(
            session=session,
            args=["2026-07-16", "23:30", "2026-07-17", "00:30", "front-door"],
            user=ADMIN,
        )

    assert "Export queued" in text
    assert "1 images" in text

    async with session_factory() as session:
        job = await session.scalar(select(ExportJob))

    assert job.requested_by_user_id == ADMIN.telegram_user_id
    assert job.destination_chat_id == ADMIN.telegram_chat_id


async def test_images_command_reports_storage_pressure_without_creating_export(
    create_camera,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_camera(slug="front-door")
    monkeypatch.setattr(
        "timelapse.bot.commands.get_storage_pressure_state",
        lambda *, settings: StoragePressureState.SEVERE,
    )

    async with session_scope() as session:
        text = await handle_images_command(
            session=session,
            args=["2026-07-16", "23:30", "2026-07-17", "00:30", "front-door"],
            user=ADMIN,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await session.scalar(select(ExportJob))

    assert text == "Export request rejected: storage_pressure_severe"
    assert job is None


async def test_exports_command_lists_only_requesting_user_jobs(create_camera) -> None:
    camera_fixture = await create_camera(slug="front-door")
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    async with session_scope() as session:
        session.add_all(
            [
                ExportJob(
                    requested_by_user_id=ADMIN.telegram_user_id,
                    destination_chat_id=ADMIN.telegram_chat_id,
                    camera_id=camera_id,
                    start_at_utc=NOW,
                    end_at_utc=NOW.replace(hour=13),
                    status=JobStatus.PENDING,
                    matching_image_count=2,
                    expires_at=NOW.replace(hour=18),
                ),
                ExportJob(
                    requested_by_user_id=VIEWER.telegram_user_id,
                    destination_chat_id=VIEWER.telegram_chat_id,
                    camera_id=camera_id,
                    start_at_utc=NOW,
                    end_at_utc=NOW.replace(hour=13),
                    status=JobStatus.PENDING,
                    matching_image_count=9,
                    expires_at=NOW.replace(hour=18),
                ),
            ]
        )

    async with session_scope() as session:
        text = await handle_exports_command(session=session, user=ADMIN)

    assert "2 images" in text
    assert "9 images" not in text


async def test_cancel_requires_admin_and_rejects_after_upload_started(
    create_camera,
    tmp_path: Path,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    async with session_scope() as session:
        job = ExportJob(
            requested_by_user_id=VIEWER.telegram_user_id,
            destination_chat_id=VIEWER.telegram_chat_id,
            camera_id=camera_id,
            start_at_utc=NOW,
            end_at_utc=NOW.replace(hour=13),
            status=JobStatus.PENDING,
            matching_image_count=1,
            expires_at=NOW.replace(hour=18),
        )
        session.add(job)
        await session.flush()
        job_id = job.id

    async with session_scope() as session:
        with pytest.raises(PermissionError):
            await handle_cancel_command(session=session, args=[str(job_id)], user=VIEWER)

    async with session_scope() as session:
        text = await handle_cancel_command(session=session, args=[str(job_id)], user=ADMIN)

    assert "cancelled" in text.lower()

    async with session_scope() as session:
        job = await session.get(ExportJob, job_id)
        job.status = JobStatus.PENDING
        session.add(
            ExportPart(
                export_job_id=job_id,
                part_number=1,
                storage_path=str(tmp_path / "part.zip"),
                file_size_bytes=1,
                sha256="0" * 64,
                status="sent",
            )
        )

    async with session_scope() as session:
        with pytest.raises(ValueError, match="export_upload_started"):
            await handle_cancel_command(session=session, args=[str(job_id)], user=ADMIN)
