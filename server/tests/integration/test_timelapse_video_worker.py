from __future__ import annotations

import asyncio
import hashlib
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import (
    Camera,
    Image,
    TimelapseVideoDelivery,
    TimelapseVideoJob,
)
from timelapse.models.enums import CaptureSource, ImageStorageState, JobStatus
from timelapse.services.storage_pressure import StoragePressureState
from timelapse.services.timelapse_video_generator import GeneratedTimelapseVideo
from timelapse.services.timelapse_video_requests import create_due_video_jobs_once
from timelapse.services.timelapse_video_worker import process_due_video_jobs_once

DUE_AT = datetime(2026, 7, 16, 17, 10, tzinfo=UTC)
CAPTURED_AT = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class RecordingVideoSender:
    def __init__(self) -> None:
        self.sent_paths: list[Path] = []

    async def send_video(
        self,
        *,
        chat_id: int,
        video_path: Path,
        caption: str | None = None,
    ) -> int:
        assert chat_id == 123
        assert caption is not None
        assert await asyncio.to_thread(video_path.is_file)
        self.sent_paths.append(video_path)
        return 456


class FailingOnceVideoSender(RecordingVideoSender):
    def __init__(self) -> None:
        super().__init__()
        self.attempt_count = 0

    async def send_video(
        self,
        *,
        chat_id: int,
        video_path: Path,
        caption: str | None = None,
    ) -> int:
        self.attempt_count += 1
        if self.attempt_count == 1:
            raise RuntimeError("telegram unavailable")
        return await super().send_video(
            chat_id=chat_id,
            video_path=video_path,
            caption=caption,
        )


class PartialFailureVideoSender:
    def __init__(self) -> None:
        self.attempted_chat_ids: list[int] = []
        self.failed_once = False

    async def send_video(
        self,
        *,
        chat_id: int,
        video_path: Path,
        caption: str | None = None,
    ) -> int:
        assert await asyncio.to_thread(video_path.is_file)
        assert caption is not None
        self.attempted_chat_ids.append(chat_id)

        if chat_id == 456 and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("telegram unavailable")

        return chat_id


async def create_pending_job(
    create_camera,
    tmp_path: Path,
    *,
    recipient_chat_ids: tuple[int, ...] = (123,),
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"jpeg")
    capture_id = uuid4()

    async with session_scope() as session:
        session.add(
            Image(
                capture_id=capture_id,
                camera_id=camera_id,
                captured_at_utc=CAPTURED_AT,
                received_at_utc=CAPTURED_AT,
                capture_source=CaptureSource.SCHEDULED,
                storage_state=ImageStorageState.STORED,
                storage_path=str(image_path),
                mime_type="image/jpeg",
                file_size_bytes=image_path.stat().st_size,
                width_pixels=320,
                height_pixels=180,
                sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
            )
        )

    async with session_scope() as session:
        await create_due_video_jobs_once(
            session=session,
            now=DUE_AT,
            recipient_chat_ids=recipient_chat_ids,
        )


def fake_video_builder(**kwargs) -> GeneratedTimelapseVideo:
    output_directory = kwargs["output_directory"]
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"{kwargs['job_id']}.mp4"
    path.write_bytes(b"generated-video")
    return GeneratedTimelapseVideo(
        path=path,
        file_size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


async def test_video_worker_sends_then_deletes_generated_file(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_pending_job(create_camera, tmp_path)
    sender = RecordingVideoSender()

    async with session_scope() as session:
        processed_count = await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=sender,
            admin_user_id=123,
            now=DUE_AT,
            video_builder=fake_video_builder,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await session.scalar(select(TimelapseVideoJob))

    assert processed_count == 1
    assert len(sender.sent_paths) == 1
    assert not sender.sent_paths[0].exists()
    assert job.status == JobStatus.COMPLETED
    assert job.telegram_message_id == 456
    assert job.storage_path is None
    assert job.file_deleted_at == DUE_AT
    assert job.file_size_bytes == len(b"generated-video")
    assert job.sha256 == hashlib.sha256(b"generated-video").hexdigest()


async def test_recently_claimed_video_job_is_not_processed_concurrently(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_pending_job(create_camera, tmp_path)
    generation_started = threading.Event()
    release_generation = threading.Event()

    def blocking_builder(**kwargs) -> GeneratedTimelapseVideo:
        generation_started.set()
        assert release_generation.wait(timeout=5)
        return fake_video_builder(**kwargs)

    async def run_first_worker() -> int:
        async with session_scope() as session:
            return await process_due_video_jobs_once(
                session=session,
                output_directory=tmp_path / "videos",
                sender=RecordingVideoSender(),
                admin_user_id=123,
                now=DUE_AT,
                video_builder=blocking_builder,
            )

    first_worker = asyncio.create_task(run_first_worker())
    assert await asyncio.to_thread(generation_started.wait, 5)

    try:
        async with session_scope() as session:
            second_count = await process_due_video_jobs_once(
                session=session,
                output_directory=tmp_path / "videos",
                sender=RecordingVideoSender(),
                admin_user_id=123,
                now=DUE_AT + timedelta(minutes=1),
                video_builder=fake_video_builder,
            )
    finally:
        release_generation.set()

    first_count = await first_worker

    assert first_count == 1
    assert second_count == 0


async def test_video_worker_reuses_generated_file_after_delivery_failure(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_pending_job(create_camera, tmp_path)
    sender = FailingOnceVideoSender()
    generation_count = 0

    def counting_builder(**kwargs) -> GeneratedTimelapseVideo:
        nonlocal generation_count
        generation_count += 1
        return fake_video_builder(**kwargs)

    async with session_scope() as session:
        await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=sender,
            admin_user_id=123,
            now=DUE_AT,
            video_builder=counting_builder,
        )

    async with session_scope() as session:
        job = await session.scalar(select(TimelapseVideoJob))
        assert job.status == JobStatus.UPLOADING
        assert job.storage_path is not None
        assert await asyncio.to_thread(Path(job.storage_path).is_file)

    async with session_scope() as session:
        await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=sender,
            admin_user_id=123,
            now=DUE_AT,
            video_builder=counting_builder,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await session.scalar(select(TimelapseVideoJob))

    assert generation_count == 1
    assert sender.attempt_count == 2
    assert job.status == JobStatus.COMPLETED
    assert job.storage_path is None


async def test_video_worker_retries_only_unsent_recipients(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_pending_job(
        create_camera,
        tmp_path,
        recipient_chat_ids=(123, 456),
    )
    sender = PartialFailureVideoSender()

    async with session_scope() as session:
        await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=sender,
            admin_user_id=123,
            now=DUE_AT,
            video_builder=fake_video_builder,
        )

    async with session_scope() as session:
        deliveries = (
            await session.scalars(
                select(TimelapseVideoDelivery).order_by(
                    TimelapseVideoDelivery.telegram_chat_id
                )
            )
        ).all()
        assert [delivery.status for delivery in deliveries] == ["sent", "pending"]

    async with session_scope() as session:
        await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=sender,
            admin_user_id=123,
            now=DUE_AT,
            video_builder=fake_video_builder,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await session.scalar(select(TimelapseVideoJob))
        deliveries = (
            await session.scalars(
                select(TimelapseVideoDelivery).order_by(
                    TimelapseVideoDelivery.telegram_chat_id
                )
            )
        ).all()

    assert sender.attempted_chat_ids == [123, 456, 456]
    assert [delivery.status for delivery in deliveries] == ["sent", "sent"]
    assert job.status == JobStatus.COMPLETED


async def test_video_worker_defers_generation_under_storage_pressure(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_pending_job(create_camera, tmp_path)

    def unexpected_builder(**kwargs) -> GeneratedTimelapseVideo:
        raise AssertionError("ffmpeg must not run under storage pressure")

    async with session_scope() as session:
        processed_count = await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=RecordingVideoSender(),
            admin_user_id=123,
            now=DUE_AT,
            storage_pressure_state=StoragePressureState.SEVERE,
            video_builder=unexpected_builder,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await session.scalar(select(TimelapseVideoJob))

    assert processed_count == 1
    assert job.status == JobStatus.PENDING
    assert job.claimed_at is None
    assert job.error_code == "storage_pressure_severe"
    assert job.storage_path is None


async def test_storage_pressure_deletes_retained_retry_video(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_pending_job(create_camera, tmp_path)
    sender = FailingOnceVideoSender()

    async with session_scope() as session:
        await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=sender,
            admin_user_id=123,
            now=DUE_AT,
            video_builder=fake_video_builder,
        )

    async with session_scope() as session:
        job = await session.scalar(select(TimelapseVideoJob))
        retained_path = Path(job.storage_path)
        assert await asyncio.to_thread(retained_path.is_file)

    async with session_scope() as session:
        await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=None,
            admin_user_id=123,
            now=DUE_AT,
            storage_pressure_state=StoragePressureState.SEVERE,
            video_builder=fake_video_builder,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await session.scalar(select(TimelapseVideoJob))

    assert sender.attempt_count == 1
    assert not await asyncio.to_thread(retained_path.exists)
    assert job.status == JobStatus.PENDING
    assert job.storage_path is None
    assert job.error_code == "storage_pressure_severe"


async def test_video_worker_deletes_oversized_output_and_retains_metadata(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_pending_job(create_camera, tmp_path)
    generated_path: Path | None = None

    def oversized_builder(**kwargs) -> GeneratedTimelapseVideo:
        nonlocal generated_path
        output_directory = kwargs["output_directory"]
        output_directory.mkdir(parents=True, exist_ok=True)
        generated_path = output_directory / f"{kwargs['job_id']}.mp4"
        generated_path.write_bytes(b"oversized")
        return GeneratedTimelapseVideo(
            path=generated_path,
            file_size_bytes=101,
            sha256=hashlib.sha256(b"oversized").hexdigest(),
        )

    async with session_scope() as session:
        await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=RecordingVideoSender(),
            admin_user_id=123,
            now=DUE_AT,
            maximum_video_bytes=100,
            video_builder=oversized_builder,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await session.scalar(select(TimelapseVideoJob))

    assert generated_path is not None
    assert not await asyncio.to_thread(generated_path.exists)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "video_too_large"
    assert job.storage_path is None
    assert job.file_deleted_at == DUE_AT
    assert job.file_size_bytes == 101
    assert job.sha256 == hashlib.sha256(b"oversized").hexdigest()


async def test_video_worker_cleans_completed_file_without_sender(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_pending_job(create_camera, tmp_path)
    video_path = tmp_path / "videos" / "existing.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"already-sent")

    async with session_scope() as session:
        job = await session.scalar(select(TimelapseVideoJob))
        job.status = JobStatus.COMPLETED
        job.storage_path = str(video_path)
        job.telegram_message_id = 789
        job.completed_at = DUE_AT

    async with session_scope() as session:
        processed_count = await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=None,
            admin_user_id=123,
            now=DUE_AT,
            video_builder=fake_video_builder,
        )

    assert processed_count == 1
    assert not await asyncio.to_thread(video_path.exists)


async def test_video_worker_cleans_completed_file_without_resending(
    create_camera,
    tmp_path: Path,
) -> None:
    await create_pending_job(create_camera, tmp_path)
    video_path = tmp_path / "videos" / "existing.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"already-sent")

    async with session_scope() as session:
        job = await session.scalar(select(TimelapseVideoJob))
        job.status = JobStatus.COMPLETED
        job.storage_path = str(video_path)
        job.telegram_message_id = 789
        job.completed_at = DUE_AT

    sender = RecordingVideoSender()
    async with session_scope() as session:
        processed_count = await process_due_video_jobs_once(
            session=session,
            output_directory=tmp_path / "videos",
            sender=sender,
            admin_user_id=123,
            now=DUE_AT,
            video_builder=fake_video_builder,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await session.scalar(select(TimelapseVideoJob))

    assert processed_count == 1
    assert sender.sent_paths == []
    assert not video_path.exists()
    assert job.status == JobStatus.COMPLETED
    assert job.storage_path is None
    assert job.file_deleted_at == DUE_AT
