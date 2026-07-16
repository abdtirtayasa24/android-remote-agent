from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import warnings
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from anyio import Path as AsyncPath
from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from timelapse.configuration import Settings

MAXIMUM_IMAGE_BYTES = 5 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


class UploadRejectedError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class ValidatedUpload:
    temporary_path: Path
    file_size_bytes: int
    sha256: str
    width_pixels: int
    height_pixels: int


class FileInstallResult(StrEnum):
    INSTALLED = "installed"
    REUSED_EXISTING = "reused_existing"
    REPLACED_CONFLICT = "replaced_conflict"


def ensure_storage_layout(settings: Settings) -> None:
    for directory in (
        settings.images_directory,
        settings.exports_directory,
        settings.timelapses_directory,
        settings.quarantine_directory,
        settings.temporary_directory,
        settings.temporary_directory / "uploads",
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o750,
        )


async def receive_and_validate_upload(
    *,
    upload_file: UploadFile,
    expected_sha256: str,
    maximum_width: int,
    maximum_height: int,
    settings: Settings,
) -> ValidatedUpload:
    upload_directory = settings.temporary_directory / "uploads"

    await asyncio.to_thread(
        upload_directory.mkdir,
        parents=True,
        exist_ok=True,
        mode=0o750,
    )

    temporary_path = upload_directory / f"{uuid4()}.part"

    try:
        file_size_bytes, actual_sha256 = await _stream_upload(
            upload_file=upload_file,
            destination=temporary_path,
        )

        if not hmac.compare_digest(
            actual_sha256,
            expected_sha256.lower(),
        ):
            raise UploadRejectedError(
                status_code=422,
                code="checksum_mismatch",
            )

        width_pixels, height_pixels = await asyncio.to_thread(
            inspect_jpeg,
            temporary_path,
            maximum_width,
            maximum_height,
        )

        return ValidatedUpload(
            temporary_path=temporary_path,
            file_size_bytes=file_size_bytes,
            sha256=actual_sha256,
            width_pixels=width_pixels,
            height_pixels=height_pixels,
        )
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


async def _stream_upload(
    *,
    upload_file: UploadFile,
    destination: Path,
) -> tuple[int, str]:
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )

    digest = hashlib.sha256()
    file_size_bytes = 0

    destination_async = AsyncPath(destination)

    try:
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = await upload_file.read(UPLOAD_CHUNK_BYTES)

                if not chunk:
                    break

                file_size_bytes += len(chunk)

                if file_size_bytes > MAXIMUM_IMAGE_BYTES:
                    raise UploadRejectedError(
                        status_code=413,
                        code="image_too_large",
                    )

                digest.update(chunk)
                output.write(chunk)

            output.flush()
            os.fsync(output.fileno())
    except Exception:
        await destination_async.unlink(missing_ok=True)
        raise

    if file_size_bytes == 0:
        await destination_async.unlink(missing_ok=True)

        raise UploadRejectedError(
            status_code=422,
            code="empty_image",
        )

    return file_size_bytes, digest.hexdigest()


def inspect_jpeg(
    path: Path,
    maximum_width: int,
    maximum_height: int,
) -> tuple[int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter(
                "error",
                Image.DecompressionBombWarning,
            )

            with Image.open(path) as image:
                if image.format != "JPEG":
                    raise UploadRejectedError(
                        status_code=422,
                        code="invalid_jpeg",
                    )

                width_pixels, height_pixels = image.size

                if width_pixels <= 0 or height_pixels <= 0:
                    raise UploadRejectedError(
                        status_code=422,
                        code="invalid_image_dimensions",
                    )

                if width_pixels > maximum_width or height_pixels > maximum_height:
                    raise UploadRejectedError(
                        status_code=422,
                        code="image_dimensions_exceeded",
                    )

                image.verify()

            with Image.open(path) as image:
                image.load()

                if image.format != "JPEG":
                    raise UploadRejectedError(
                        status_code=422,
                        code="invalid_jpeg",
                    )

                if image.size != (width_pixels, height_pixels):
                    raise UploadRejectedError(
                        status_code=422,
                        code="invalid_image_dimensions",
                    )

    except UploadRejectedError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise UploadRejectedError(
            status_code=422,
            code="invalid_jpeg",
        ) from exc

    return width_pixels, height_pixels


def build_image_storage_path(
    *,
    settings: Settings,
    camera_slug: str,
    captured_at_utc: datetime,
    capture_id: UUID,
) -> Path:
    return (
        settings.images_directory
        / camera_slug
        / captured_at_utc.strftime("%Y")
        / captured_at_utc.strftime("%m")
        / captured_at_utc.strftime("%d")
        / (f"{captured_at_utc.strftime('%Y%m%dT%H%M%SZ')}_{capture_id}.jpg")
    )


def install_validated_file(
    *,
    upload: ValidatedUpload,
    final_path: Path,
    settings: Settings,
) -> FileInstallResult:
    final_path.parent.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o750,
    )

    if final_path.exists():
        if file_matches_upload(final_path, upload):
            upload.temporary_path.unlink(missing_ok=True)
            return FileInstallResult.REUSED_EXISTING

        quarantine_directory = settings.quarantine_directory / "upload-conflicts"
        quarantine_directory.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o750,
        )

        quarantine_path = quarantine_directory / f"{uuid4()}_{final_path.name}"

        os.replace(final_path, quarantine_path)
        fsync_directory(quarantine_directory)

        os.replace(upload.temporary_path, final_path)
        fsync_directory(final_path.parent)

        return FileInstallResult.REPLACED_CONFLICT

    os.replace(upload.temporary_path, final_path)
    fsync_directory(final_path.parent)

    return FileInstallResult.INSTALLED


def file_matches_upload(
    path: Path,
    upload: ValidatedUpload,
) -> bool:
    try:
        if path.stat().st_size != upload.file_size_bytes:
            return False
    except OSError:
        return False

    digest = hashlib.sha256()

    try:
        with path.open("rb") as image_file:
            for chunk in iter(
                lambda: image_file.read(UPLOAD_CHUNK_BYTES),
                b"",
            ):
                digest.update(chunk)
    except OSError:
        return False

    return hmac.compare_digest(
        digest.hexdigest(),
        upload.sha256,
    )


def fsync_directory(directory: Path) -> None:
    descriptor = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY,
    )

    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
