from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile


class ExportZipPartTooLargeError(ValueError):
    def __init__(self, *, part_number: int, maximum_part_bytes: int) -> None:
        super().__init__("export_zip_part_too_large")
        self.part_number = part_number
        self.maximum_part_bytes = maximum_part_bytes


@dataclass(frozen=True)
class ExportZipImage:
    ordinal: int
    filename: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class ExportZipPart:
    part_number: int
    path: Path
    file_size_bytes: int
    sha256: str


def build_export_zip_parts(
    *,
    output_dir: Path,
    job_id: str,
    images: tuple[ExportZipImage, ...],
    maximum_part_bytes: int,
) -> tuple[ExportZipPart, ...]:
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    parts: list[ExportZipPart] = []
    current_images: list[ExportZipImage] = []

    for image in images:
        candidate = [*current_images, image]
        candidate_path = _write_part(
            output_dir=output_dir,
            job_id=job_id,
            part_number=len(parts) + 1,
            images=tuple(candidate),
        )

        if candidate_path.stat().st_size <= maximum_part_bytes:
            _unlink_existing_part(parts, len(parts) + 1)
            current_images = candidate
            continue

        if not current_images:
            candidate_path.unlink(missing_ok=True)
            raise ExportZipPartTooLargeError(
                part_number=len(parts) + 1,
                maximum_part_bytes=maximum_part_bytes,
            )

        candidate_path.unlink(missing_ok=True)
        parts.append(
            _finalize_part(
                output_dir=output_dir,
                job_id=job_id,
                part_number=len(parts) + 1,
                images=tuple(current_images),
            )
        )
        current_images = [image]

    if current_images or not parts:
        parts.append(
            _finalize_part(
                output_dir=output_dir,
                job_id=job_id,
                part_number=len(parts) + 1,
                images=tuple(current_images),
            )
        )

    return tuple(parts)


def _unlink_existing_part(parts: list[ExportZipPart], part_number: int) -> None:
    if part_number <= len(parts):
        parts[part_number - 1].path.unlink(missing_ok=True)


def _finalize_part(
    *,
    output_dir: Path,
    job_id: str,
    part_number: int,
    images: tuple[ExportZipImage, ...],
) -> ExportZipPart:
    path = _write_part(
        output_dir=output_dir,
        job_id=job_id,
        part_number=part_number,
        images=images,
    )
    data = path.read_bytes()
    return ExportZipPart(
        part_number=part_number,
        path=path,
        file_size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _write_part(
    *,
    output_dir: Path,
    job_id: str,
    part_number: int,
    images: tuple[ExportZipImage, ...],
) -> Path:
    path = output_dir / f"{job_id}-part-{part_number:03d}.zip"

    with TemporaryDirectory(dir=output_dir) as temporary_directory:
        manifest_path = Path(temporary_directory) / "manifest.csv"

        with manifest_path.open("w", newline="", encoding="utf-8") as manifest_file:
            writer = csv.writer(manifest_file)
            writer.writerow(["ordinal", "filename", "file_size_bytes", "sha256"])

            for image in images:
                writer.writerow(
                    [
                        image.ordinal,
                        image.filename,
                        image.path.stat().st_size,
                        image.sha256,
                    ]
                )

        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            archive.write(manifest_path, "manifest.csv")

            for image in images:
                archive.write(image.path, image.filename)

    return path
