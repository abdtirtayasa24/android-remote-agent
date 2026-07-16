from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest
import timelapse.services.motion_worker as motion_worker
from sqlalchemy import func, select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import (
    Camera,
    Image,
    MotionAnalysis,
    MotionEvent,
    MotionEventImage,
    TelegramPrincipal,
)
from timelapse.models.enums import AnalysisStatus, CaptureSource, ImageStorageState
from timelapse.services.motion_detection import FRAME_DIFF_V1
from timelapse.services.motion_worker import process_due_motion_analyses_once

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@dataclass
class FakeMotionAlertSender:
    photos: list[tuple[int, Path, str | None]] = field(default_factory=list)
    fail: bool = False

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str | None = None,
    ) -> int:
        if self.fail:
            raise RuntimeError("telegram unavailable")

        self.photos.append((chat_id, photo_path, caption))
        return len(self.photos)


def write_jpeg(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), image)


def static_image() -> np.ndarray:
    return np.full((180, 320, 3), 40, dtype=np.uint8)


def motion_image() -> np.ndarray:
    image = static_image()
    image[50:110, 120:180] = 255
    return image


async def add_stored_image(
    *,
    camera_id,
    storage_root: Path,
    captured_at: datetime,
    image: np.ndarray,
    create_analysis: bool,
) -> Image:
    capture_id = uuid4()
    image_path = storage_root / f"{capture_id}.jpg"
    write_jpeg(image_path, image)
    image_bytes = image_path.read_bytes()

    async with session_scope() as session:
        stored_image = Image(
            capture_id=capture_id,
            camera_id=camera_id,
            captured_at_utc=captured_at,
            received_at_utc=captured_at + timedelta(seconds=5),
            capture_source=CaptureSource.SCHEDULED,
            storage_state=ImageStorageState.STORED,
            storage_path=str(image_path),
            mime_type="image/jpeg",
            file_size_bytes=len(image_bytes),
            width_pixels=320,
            height_pixels=180,
            sha256=hashlib.sha256(image_bytes).hexdigest(),
        )
        session.add(stored_image)
        await session.flush()

        if create_analysis:
            session.add(
                MotionAnalysis(
                    image_id=stored_image.id,
                    status=AnalysisStatus.PENDING,
                    algorithm_version=FRAME_DIFF_V1,
                )
            )

        return stored_image


async def add_telegram_recipient() -> None:
    async with session_scope() as session:
        session.add(
            TelegramPrincipal(
                telegram_user_id=123,
                telegram_chat_id=456,
                display_name="Operator",
                role="administrator",
            )
        )


async def get_camera_id(slug: str):
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == slug))
        assert camera is not None
        return camera.id


async def set_capture_interval_seconds(
    *,
    camera_id,
    capture_interval_seconds: int,
) -> None:
    async with session_scope() as session:
        camera = await session.get(Camera, camera_id)
        assert camera is not None
        camera.capture_interval_seconds = capture_interval_seconds


async def test_motion_worker_completes_pending_analysis_and_keeps_image(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    previous = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=1),
        image=static_image(),
        create_analysis=False,
    )
    current = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )

    async with session_scope() as session:
        processed_count = await process_due_motion_analyses_once(session=session, now=NOW)

    assert processed_count == 1

    session_factory = get_session_factory()

    async with session_factory() as session:
        analysis = await session.get(MotionAnalysis, current.id)
        image = await session.get(Image, current.id)

    assert analysis.status == AnalysisStatus.COMPLETED
    assert analysis.previous_image_id == previous.id
    assert analysis.motion_detected is True
    assert analysis.changed_pixel_ratio is not None
    assert image.motion_detected is True
    assert image.deleted_at is None


async def test_motion_worker_skips_without_previous_image_and_keeps_image(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    current = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )

    async with session_scope() as session:
        processed_count = await process_due_motion_analyses_once(session=session, now=NOW)

    assert processed_count == 1

    session_factory = get_session_factory()

    async with session_factory() as session:
        analysis = await session.get(MotionAnalysis, current.id)
        image = await session.get(Image, current.id)

    assert analysis.status == AnalysisStatus.SKIPPED
    assert analysis.suppression_reason == "no_previous_image"
    assert image.deleted_at is None


async def test_motion_worker_runs_detection_in_thread(
    create_camera,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=1),
        image=static_image(),
        create_analysis=False,
    )
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )
    calls = []

    async def fake_to_thread(function, /, *args, **kwargs):
        calls.append(function)
        return function(*args, **kwargs)

    monkeypatch.setattr(motion_worker.asyncio, "to_thread", fake_to_thread)

    async with session_scope() as session:
        await process_due_motion_analyses_once(session=session, now=NOW)

    assert calls == [motion_worker.detect_motion]


async def test_motion_worker_bubbles_event_persistence_failure(
    create_camera,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=1),
        image=static_image(),
        create_analysis=False,
    )
    current = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )

    async def fail_record_motion_detection(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "timelapse.services.motion_worker.record_motion_detection",
        fail_record_motion_detection,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        async with session_scope() as session:
            await process_due_motion_analyses_once(session=session, now=NOW)

    session_factory = get_session_factory()

    async with session_factory() as session:
        analysis = await session.get(MotionAnalysis, current.id)
        image = await session.get(Image, current.id)

    assert analysis.status == AnalysisStatus.PENDING
    assert image.motion_detected is None


async def test_motion_worker_recovers_stale_processing_analysis(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=1),
        image=static_image(),
        create_analysis=False,
    )
    current = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )

    async with session_scope() as session:
        analysis = await session.get(MotionAnalysis, current.id)
        analysis.status = AnalysisStatus.PROCESSING
        analysis.claimed_at = NOW - timedelta(minutes=10)

    async with session_scope() as session:
        processed_count = await process_due_motion_analyses_once(
            session=session,
            now=NOW,
            stale_after=timedelta(minutes=5),
        )

    assert processed_count == 1

    session_factory = get_session_factory()

    async with session_factory() as session:
        analysis = await session.get(MotionAnalysis, current.id)

    assert analysis.status == AnalysisStatus.COMPLETED
    assert analysis.claimed_at == NOW


async def test_motion_detections_within_five_minutes_share_one_event_and_one_alert(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    await set_capture_interval_seconds(
        camera_id=camera_id,
        capture_interval_seconds=120,
    )
    await add_telegram_recipient()
    sender = FakeMotionAlertSender()
    first = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=2),
        image=static_image(),
        create_analysis=False,
    )
    second = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )
    third = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW + timedelta(minutes=4),
        image=static_image(),
        create_analysis=True,
    )

    async with session_scope() as session:
        processed_count = await process_due_motion_analyses_once(
            session=session,
            now=NOW + timedelta(minutes=4),
            sender=sender,
        )

    assert processed_count == 2
    assert len(sender.photos) == 1
    assert sender.photos[0][0] == 456
    assert sender.photos[0][1] == Path(second.storage_path)

    session_factory = get_session_factory()

    async with session_factory() as session:
        event_count = await session.scalar(select(func.count()).select_from(MotionEvent))
        link_count = await session.scalar(select(func.count()).select_from(MotionEventImage))
        event = await session.scalar(select(MotionEvent))

    assert first.id is not None
    assert third.id is not None
    assert event_count == 1
    assert link_count == 2
    assert event.representative_image_id == second.id
    assert event.last_detected_at_utc == third.captured_at_utc
    assert event.alert_status == "sent"
    assert event.telegram_message_id == 1


async def test_pending_motion_alert_is_sent_when_sender_becomes_available(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=1),
        image=static_image(),
        create_analysis=False,
    )
    current = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )

    async with session_scope() as session:
        await process_due_motion_analyses_once(session=session, now=NOW, sender=None)

    session_factory = get_session_factory()

    async with session_factory() as session:
        event = await session.scalar(select(MotionEvent))

    assert event.alert_status == "pending"

    await add_telegram_recipient()
    sender = FakeMotionAlertSender()

    async with session_scope() as session:
        processed_count = await process_due_motion_analyses_once(
            session=session,
            now=NOW + timedelta(minutes=1),
            sender=sender,
        )

    assert processed_count == 1
    assert len(sender.photos) == 1
    assert sender.photos[0][0] == 456
    assert sender.photos[0][1] == Path(current.storage_path)

    async with session_factory() as session:
        event = await session.scalar(select(MotionEvent))

    assert event.alert_status == "sent"
    assert event.telegram_message_id == 1


async def test_motion_detection_after_window_creates_new_event(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    await set_capture_interval_seconds(
        camera_id=camera_id,
        capture_interval_seconds=120,
    )
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=1),
        image=static_image(),
        create_analysis=False,
    )
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW + timedelta(minutes=6),
        image=static_image(),
        create_analysis=True,
    )

    async with session_scope() as session:
        processed_count = await process_due_motion_analyses_once(
            session=session,
            now=NOW + timedelta(minutes=6),
        )

    assert processed_count == 2

    session_factory = get_session_factory()

    async with session_factory() as session:
        event_count = await session.scalar(select(func.count()).select_from(MotionEvent))

    assert event_count == 2


async def test_motion_analysis_failure_keeps_image_row(
    create_camera,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=1),
        image=static_image(),
        create_analysis=False,
    )
    current = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )

    def fail_detection(*args: object, **kwargs: object) -> None:
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(
        "timelapse.services.motion_worker.detect_motion",
        fail_detection,
    )

    async with session_scope() as session:
        processed_count = await process_due_motion_analyses_once(session=session, now=NOW)

    assert processed_count == 1

    session_factory = get_session_factory()

    async with session_factory() as session:
        analysis = await session.get(MotionAnalysis, current.id)
        image = await session.get(Image, current.id)

    assert analysis.status == AnalysisStatus.FAILED
    assert analysis.error_message == "RuntimeError"
    assert image.deleted_at is None
    assert image.storage_state == ImageStorageState.STORED


async def test_motion_alert_failure_does_not_fail_analysis_or_delete_image(
    create_camera,
    tmp_path: Path,
) -> None:
    camera = await create_camera(slug="front-door")
    camera_id = await get_camera_id(camera.slug)
    await add_telegram_recipient()
    sender = FakeMotionAlertSender(fail=True)
    await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW - timedelta(minutes=1),
        image=static_image(),
        create_analysis=False,
    )
    current = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
        image=motion_image(),
        create_analysis=True,
    )

    async with session_scope() as session:
        processed_count = await process_due_motion_analyses_once(
            session=session,
            now=NOW,
            sender=sender,
        )

    assert processed_count == 1

    session_factory = get_session_factory()

    async with session_factory() as session:
        analysis = await session.get(MotionAnalysis, current.id)
        image = await session.get(Image, current.id)
        event = await session.scalar(select(MotionEvent))

    assert analysis.status == AnalysisStatus.COMPLETED
    assert image.deleted_at is None
    assert image.motion_detected is True
    assert event.alert_status == "failed"
