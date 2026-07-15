from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select

from timelapse.configuration import Settings
from timelapse.database import session_scope
from timelapse.models.entities import (
    Camera,
    Image,
    MotionAnalysis,
)
from timelapse.models.enums import (
    AnalysisStatus,
    CaptureSource,
    ImageStorageState,
)
from timelapse.services.camera_authentication import AuthenticatedCamera
from timelapse.services.image_files import (
    FileInstallResult,
    UploadRejectedError,
    ValidatedUpload,
    build_image_storage_path,
    install_validated_file,
)


@dataclass(frozen=True)
class ImageUploadCommand:
    capture_id: UUID
    captured_at_utc: datetime
    capture_source: CaptureSource
    upload: ValidatedUpload


@dataclass(frozen=True)
class ImageUploadResult:
    capture_id: UUID
    status: str
    received_at_utc: datetime


def parse_captured_at_utc(
    value: str,
    *,
    now: datetime | None = None,
) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise UploadRejectedError(
            status_code=422,
            code="invalid_captured_at_utc",
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UploadRejectedError(
            status_code=422,
            code="captured_at_offset_required",
        )

    captured_at_utc = parsed.astimezone(UTC)
    current_time = now or datetime.now(UTC)

    if captured_at_utc > current_time + timedelta(hours=24):
        raise UploadRejectedError(
            status_code=422,
            code="captured_at_too_far_in_future",
        )

    return captured_at_utc


async def store_image_upload(
    *,
    authenticated_camera: AuthenticatedCamera,
    command: ImageUploadCommand,
    settings: Settings,
) -> ImageUploadResult:
    final_path = build_image_storage_path(
        settings=settings,
        camera_slug=authenticated_camera.slug,
        captured_at_utc=command.captured_at_utc,
        capture_id=command.capture_id,
    )

    await stage_image_row(
        authenticated_camera=authenticated_camera,
        command=command,
        final_path=final_path,
    )

    return await finalize_image_storage(
        authenticated_camera=authenticated_camera,
        command=command,
        final_path=final_path,
        settings=settings,
    )


async def stage_image_row(
    *,
    authenticated_camera: AuthenticatedCamera,
    command: ImageUploadCommand,
    final_path: Path,
) -> None:
    async with session_scope() as session:
        await acquire_capture_lock(
            session=session,
            capture_id=command.capture_id,
        )

        existing_image = await session.scalar(
            select(Image).where(Image.capture_id == command.capture_id)
        )

        if existing_image is not None:
            assert_same_capture(
                image=existing_image,
                authenticated_camera=authenticated_camera,
                command=command,
                expected_storage_path=final_path,
            )

            if existing_image.storage_state in {
                ImageStorageState.STAGING,
                ImageStorageState.STORED,
            }:
                return

            raise UploadRejectedError(
                status_code=409,
                code="capture_id_conflict",
            )

        received_at_utc = datetime.now(UTC)

        session.add(
            Image(
                capture_id=command.capture_id,
                camera_id=authenticated_camera.camera_id,
                captured_at_utc=command.captured_at_utc,
                received_at_utc=received_at_utc,
                capture_source=command.capture_source,
                storage_state=ImageStorageState.STAGING,
                storage_path=str(final_path),
                mime_type="image/jpeg",
                file_size_bytes=command.upload.file_size_bytes,
                width_pixels=command.upload.width_pixels,
                height_pixels=command.upload.height_pixels,
                sha256=command.upload.sha256,
            )
        )


async def finalize_image_storage(
    *,
    authenticated_camera: AuthenticatedCamera,
    command: ImageUploadCommand,
    final_path: Path,
    settings: Settings,
) -> ImageUploadResult:
    async with session_scope() as session:
        await acquire_capture_lock(
            session=session,
            capture_id=command.capture_id,
        )

        image = await session.scalar(
            select(Image).where(Image.capture_id == command.capture_id).with_for_update()
        )

        if image is None:
            raise RuntimeError("staging image row disappeared before finalization")

        assert_same_capture(
            image=image,
            authenticated_camera=authenticated_camera,
            command=command,
            expected_storage_path=final_path,
        )

        if image.storage_state not in {
            ImageStorageState.STAGING,
            ImageStorageState.STORED,
        }:
            raise UploadRejectedError(
                status_code=409,
                code="capture_id_conflict",
            )

        was_already_stored = image.storage_state == ImageStorageState.STORED

        try:
            install_result = await asyncio.to_thread(
                install_validated_file,
                upload=command.upload,
                final_path=final_path,
                settings=settings,
            )
        except OSError as exc:
            raise UploadRejectedError(
                status_code=500,
                code="image_storage_failed",
            ) from exc

        image.storage_state = ImageStorageState.STORED

        if command.capture_source == CaptureSource.SCHEDULED:
            existing_analysis = await session.get(
                MotionAnalysis,
                image.id,
            )

            if existing_analysis is None:
                session.add(
                    MotionAnalysis(
                        image_id=image.id,
                        status=AnalysisStatus.PENDING,
                        algorithm_version="frame-diff-v1",
                    )
                )

        camera = await session.scalar(
            select(Camera).where(Camera.id == authenticated_camera.camera_id).with_for_update()
        )

        if camera is None:
            raise RuntimeError("authenticated camera disappeared during upload")

        current_time = datetime.now(UTC)

        camera.last_seen_at = current_time
        camera.last_upload_at = current_time

        if camera.last_capture_at is None or command.captured_at_utc > camera.last_capture_at:
            camera.last_capture_at = command.captured_at_utc

        response_status = "stored"

        if was_already_stored and install_result == FileInstallResult.REUSED_EXISTING:
            response_status = "already_stored"
        elif was_already_stored:
            # The logical image already existed, but this retry repaired
            # a missing or conflicting filesystem copy.
            response_status = "already_stored"

        return ImageUploadResult(
            capture_id=image.capture_id,
            status=response_status,
            received_at_utc=image.received_at_utc,
        )


async def acquire_capture_lock(
    *,
    session: sa.ext.asyncio.AsyncSession,
    capture_id: UUID,
) -> None:
    await session.execute(
        sa.text(
            """
            SELECT pg_advisory_xact_lock(
                hashtextextended(:capture_id, 0)
            )
            """
        ),
        {"capture_id": str(capture_id)},
    )


def assert_same_capture(
    *,
    image: Image,
    authenticated_camera: AuthenticatedCamera,
    command: ImageUploadCommand,
    expected_storage_path: Path,
) -> None:
    if image.deleted_at is not None:
        raise UploadRejectedError(
            status_code=409,
            code="capture_id_conflict",
        )

    fields_match = all(
        (
            image.camera_id == authenticated_camera.camera_id,
            image.captured_at_utc == command.captured_at_utc,
            image.capture_source == command.capture_source,
            image.sha256 == command.upload.sha256,
            image.file_size_bytes == command.upload.file_size_bytes,
            image.width_pixels == command.upload.width_pixels,
            image.height_pixels == command.upload.height_pixels,
            image.storage_path == str(expected_storage_path),
        )
    )

    if not fields_match:
        raise UploadRejectedError(
            status_code=409,
            code="capture_id_conflict",
        )
