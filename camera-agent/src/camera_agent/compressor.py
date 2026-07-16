from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidCaptureError(ValueError):
    """Raised when a captured file is missing, empty, or not decodable."""


@dataclass(frozen=True)
class JpegMetadata:
    width_pixels: int
    height_pixels: int
    file_size_bytes: int
    sha256: str


def normalize_jpeg(
    source_path: Path,
    destination_path: Path,
    *,
    maximum_width: int,
    maximum_height: int,
    quality: int,
) -> JpegMetadata:
    """Validate, orient, resize, and atomically write a normalized JPEG."""
    _validate_source_file(source_path)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination_path.with_name(f".{destination_path.name}.{uuid4().hex}.tmp")

    try:
        with Image.open(source_path) as source_image:
            source_image.load()
            oriented_image = ImageOps.exif_transpose(source_image)
            rgb_image = oriented_image.convert("RGB")
            rgb_image.thumbnail(
                (maximum_width, maximum_height),
                Image.Resampling.LANCZOS,
            )

            with temporary_path.open("wb") as output_file:
                rgb_image.save(
                    output_file,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                )
                output_file.flush()
                os.fsync(output_file.fileno())

        width_pixels, height_pixels = _verify_output_jpeg(
            temporary_path,
            maximum_width=maximum_width,
            maximum_height=maximum_height,
        )
        file_size_bytes = temporary_path.stat().st_size
        sha256 = _sha256_file(temporary_path)

        os.replace(temporary_path, destination_path)
        _fsync_directory(destination_path.parent)

        return JpegMetadata(
            width_pixels=width_pixels,
            height_pixels=height_pixels,
            file_size_bytes=file_size_bytes,
            sha256=sha256,
        )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        temporary_path.unlink(missing_ok=True)
        if isinstance(exc, InvalidCaptureError):
            raise
        raise InvalidCaptureError(f"cannot normalize captured image: {exc}") from exc


def _validate_source_file(source_path: Path) -> None:
    if not source_path.is_file():
        raise InvalidCaptureError(f"captured file does not exist: {source_path}")

    try:
        size = source_path.stat().st_size
    except OSError as exc:
        raise InvalidCaptureError(f"cannot inspect captured file: {exc}") from exc

    if size <= 0:
        raise InvalidCaptureError(f"captured file is empty: {source_path}")


def _verify_output_jpeg(
    path: Path,
    *,
    maximum_width: int,
    maximum_height: int,
) -> tuple[int, int]:
    with Image.open(path) as image:
        image.load()
        if image.format != "JPEG":
            raise InvalidCaptureError("normalized output is not JPEG")
        width_pixels, height_pixels = image.size

    if width_pixels > maximum_width or height_pixels > maximum_height:
        raise InvalidCaptureError("normalized output exceeds configured maximum dimensions")
    if width_pixels <= 0 or height_pixels <= 0:
        raise InvalidCaptureError("normalized output has invalid dimensions")

    return width_pixels, height_pixels


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
