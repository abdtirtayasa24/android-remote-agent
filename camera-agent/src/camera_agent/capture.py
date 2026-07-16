from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from camera_agent.configuration import AgentConfig


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapturedImage:
    capture_id: str
    file_path: Path
    captured_at_utc: str
    file_size_bytes: int
    sha256: str


def capture_and_prepare(
    config: AgentConfig,
) -> CapturedImage:
    return _capture_and_prepare_in_directory(
        config,
        output_directory=config.pending_directory,
    )


def capture_validation_image(
    config: AgentConfig,
) -> CapturedImage:
    return _capture_and_prepare_in_directory(
        config,
        output_directory=config.validation_captures_directory,
    )


def _capture_and_prepare_in_directory(
    config: AgentConfig,
    *,
    output_directory: Path,
) -> CapturedImage:
    captured_at = datetime.now(UTC)
    capture_id = str(uuid4())

    date_directory = output_directory / config.camera_slug / captured_at.strftime("%Y-%m-%d")
    date_directory.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    config.temporary_directory.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    filename = f"{captured_at.strftime('%Y%m%dT%H%M%SZ')}_{capture_id}.jpg"

    final_path = date_directory / filename
    output_temporary_path = date_directory / f".{filename}.part"
    raw_path = config.temporary_directory / f"capture-{capture_id}.jpg"

    try:
        _invoke_termux_camera(
            camera_id=config.camera_id,
            destination=raw_path,
            timeout_seconds=(config.capture_timeout_seconds),
        )

        _validate_raw_capture(raw_path)

        _compress_jpeg(
            source=raw_path,
            destination=output_temporary_path,
            maximum_width=config.maximum_width,
            maximum_height=config.maximum_height,
            quality=config.jpeg_quality,
        )

        _validate_prepared_jpeg(
            path=output_temporary_path,
            maximum_width=config.maximum_width,
            maximum_height=config.maximum_height,
        )

        os.replace(
            output_temporary_path,
            final_path,
        )
        _fsync_directory(date_directory)

        file_size = final_path.stat().st_size
        sha256 = _calculate_sha256(final_path)

        return CapturedImage(
            capture_id=capture_id,
            file_path=final_path,
            captured_at_utc=(captured_at.isoformat().replace("+00:00", "Z")),
            file_size_bytes=file_size,
            sha256=sha256,
        )
    except (
        CaptureError,
        OSError,
        subprocess.SubprocessError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        final_path.unlink(missing_ok=True)

        if isinstance(exc, CaptureError):
            raise

        raise CaptureError(type(exc).__name__) from exc
    finally:
        raw_path.unlink(missing_ok=True)
        output_temporary_path.unlink(missing_ok=True)


def _invoke_termux_camera(
    *,
    camera_id: int,
    destination: Path,
    timeout_seconds: int,
) -> None:
    try:
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "termux-camera-photo",
                "-c",
                str(camera_id),
                str(destination),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise CaptureError("termux_camera_photo_missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise CaptureError("camera_capture_timeout") from exc

    if result.returncode != 0:
        raise CaptureError("camera_capture_command_failed")


def _validate_raw_capture(path: Path) -> None:
    if not path.is_file():
        raise CaptureError("camera_capture_missing")

    if path.stat().st_size <= 0:
        raise CaptureError("camera_capture_empty")

    try:
        with Image.open(path) as image:
            image.verify()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise CaptureError("camera_capture_invalid") from exc


def _compress_jpeg(
    *,
    source: Path,
    destination: Path,
    maximum_width: int,
    maximum_height: int,
    quality: int,
) -> None:
    with Image.open(source) as source_image:
        source_image.load()

        image = ImageOps.exif_transpose(source_image)

        if image.mode != "RGB":
            image = image.convert("RGB")
        else:
            image = image.copy()

    image.thumbnail(
        (
            maximum_width,
            maximum_height,
        ),
        Image.Resampling.LANCZOS,
    )

    with destination.open("wb") as output:
        image.save(
            output,
            format="JPEG",
            quality=quality,
            optimize=True,
        )
        output.flush()
        os.fsync(output.fileno())

    image.close()


def _validate_prepared_jpeg(
    *,
    path: Path,
    maximum_width: int,
    maximum_height: int,
) -> None:
    if not path.is_file():
        raise CaptureError("prepared_image_missing")

    if path.stat().st_size <= 0:
        raise CaptureError("prepared_image_empty")

    try:
        with Image.open(path) as image:
            if image.format != "JPEG":
                raise CaptureError("prepared_image_not_jpeg")

            width, height = image.size

            if width <= 0 or height <= 0 or width > maximum_width or height > maximum_height:
                raise CaptureError("prepared_image_dimensions_invalid")

            image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise CaptureError("prepared_image_invalid") from exc


def _calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as image_file:
        for chunk in iter(
            lambda: image_file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY,
    )

    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
