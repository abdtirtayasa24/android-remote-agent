from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image as PillowImage
from sqlalchemy import select
from timelapse.configuration import get_settings
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import Camera, Image
from timelapse.models.enums import CaptureSource, ImageStorageState
from timelapse.services.export_requests import (
    ExportRequest,
    ExportRequestError,
    create_export_request,
)
from timelapse.services.retention import process_emergency_cleanup_once
from timelapse.services.storage_pressure import DiskUsage, StoragePressureState

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def create_jpeg() -> bytes:
    output = BytesIO()
    PillowImage.new("RGB", (1280, 720), "white").save(output, format="JPEG", quality=72)
    return output.getvalue()


async def upload(
    *,
    client: httpx.AsyncClient,
    camera_slug: str,
    credential: str,
    image_bytes: bytes,
) -> httpx.Response:
    capture_id = str(uuid4())
    return await client.post(
        f"/api/v1/cameras/{camera_slug}/images",
        headers={"Authorization": f"Bearer {credential}"},
        data={
            "capture_id": capture_id,
            "captured_at_utc": "2026-07-14T01:00:00Z",
            "capture_source": "scheduled",
            "sha256": hashlib.sha256(image_bytes).hexdigest(),
        },
        files={"image": ("capture.jpg", image_bytes, "image/jpeg")},
    )


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
    capture_source: CaptureSource,
) -> Image:
    capture_id = uuid4()
    image_path = storage_root / f"{capture_id}.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(f"{capture_source.value}-{captured_at.isoformat()}".encode())
    image_bytes = image_path.read_bytes()

    async with session_scope() as session:
        image = Image(
            capture_id=capture_id,
            camera_id=camera_id,
            captured_at_utc=captured_at,
            received_at_utc=captured_at,
            capture_source=capture_source,
            storage_state=ImageStorageState.STORED,
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


async def test_upload_returns_507_when_storage_is_below_hard_threshold(
    client: httpx.AsyncClient,
    create_camera,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = await create_camera(slug="front-door")

    monkeypatch.setattr(
        "timelapse.services.storage_pressure.get_disk_usage",
        lambda path: DiskUsage(total=1_000, used=950, free=50),
    )
    monkeypatch.setenv("STORAGE_HARD_MIN_FREE_BYTES", "100")
    monkeypatch.setenv("STORAGE_SEVERE_MIN_FREE_BYTES", "200")
    get_settings.cache_clear()

    response = await upload(
        client=client,
        camera_slug=camera.slug,
        credential=camera.credential,
        image_bytes=create_jpeg(),
    )

    assert response.status_code == 507
    assert response.json() == {"detail": {"code": "storage_pressure_hard_limit"}}


async def test_export_request_rejects_new_exports_under_severe_disk_pressure(
    create_camera,
) -> None:
    await create_camera(slug="front-door")

    with pytest.raises(ExportRequestError) as exc_info:
        async with session_scope() as session:
            await create_export_request(
                session=session,
                request=ExportRequest(
                    requested_by_user_id=123,
                    destination_chat_id=456,
                    start_at_utc=NOW - timedelta(hours=1),
                    end_at_utc=NOW,
                    camera_slug="front-door",
                ),
                storage_pressure_state=StoragePressureState.SEVERE,
            )

    assert exc_info.value.code == "storage_pressure_severe"


async def test_emergency_cleanup_stops_when_pressure_normalizes(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    old_scheduled = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=3),
        capture_source=CaptureSource.SCHEDULED,
    )
    newer_scheduled = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=2),
        capture_source=CaptureSource.SCHEDULED,
    )

    states = iter(
        [
            StoragePressureState.SEVERE,
            StoragePressureState.NORMAL,
        ]
    )

    async with session_scope() as session:
        deleted_count = await process_emergency_cleanup_once(
            session=session,
            now=NOW,
            batch_size=10,
            pressure_state=lambda: next(states),
        )

    assert deleted_count == 1
    assert not await asyncio.to_thread(Path(old_scheduled.storage_path).exists)
    assert await asyncio.to_thread(Path(newer_scheduled.storage_path).exists)


async def test_emergency_cleanup_stops_after_no_progress(
    create_camera,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    failed = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=3),
        capture_source=CaptureSource.SCHEDULED,
    )

    def fail_unlink(self: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    async with session_scope() as session:
        deleted_count = await asyncio.wait_for(
            process_emergency_cleanup_once(
                session=session,
                now=NOW,
                batch_size=10,
                pressure_state=lambda: StoragePressureState.SEVERE,
            ),
            timeout=1,
        )

    assert deleted_count == 0
    assert await asyncio.to_thread(Path(failed.storage_path).exists)


async def test_emergency_cleanup_deletes_oldest_scheduled_images_first(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    old_scheduled = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=3),
        capture_source=CaptureSource.SCHEDULED,
    )
    newer_scheduled = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=2),
        capture_source=CaptureSource.SCHEDULED,
    )
    old_manual = await add_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(days=4),
        capture_source=CaptureSource.MANUAL,
    )

    states = iter(
        [
            StoragePressureState.SEVERE,
            StoragePressureState.SEVERE,
            StoragePressureState.SEVERE,
            StoragePressureState.NORMAL,
        ]
    )

    async with session_scope() as session:
        deleted_count = await process_emergency_cleanup_once(
            session=session,
            now=NOW,
            batch_size=1,
            pressure_state=lambda: next(states),
        )

    assert deleted_count == 2
    assert not await asyncio.to_thread(Path(old_scheduled.storage_path).exists)
    assert not await asyncio.to_thread(Path(newer_scheduled.storage_path).exists)
    assert await asyncio.to_thread(Path(old_manual.storage_path).exists)

    session_factory = get_session_factory()
    async with session_factory() as session:
        old_scheduled_row = await session.get(Image, old_scheduled.id)
        newer_scheduled_row = await session.get(Image, newer_scheduled.id)
        old_manual_row = await session.get(Image, old_manual.id)

    assert old_scheduled_row.deleted_at == NOW
    assert newer_scheduled_row.deleted_at == NOW
    assert old_manual_row.deleted_at is None
