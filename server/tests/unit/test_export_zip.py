from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest
from timelapse.services.export_zip import (
    ExportZipImage,
    ExportZipPartTooLargeError,
    build_export_zip_parts,
)


def test_export_zip_manifest_matches_snapshot_images(tmp_path: Path) -> None:
    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    outside_path = tmp_path / "outside.jpg"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    outside_path.write_bytes(b"outside")

    parts = build_export_zip_parts(
        output_dir=tmp_path / "exports",
        job_id="job-1",
        images=(
            ExportZipImage(ordinal=1, filename="first.jpg", path=first_path, sha256="a" * 64),
            ExportZipImage(
                ordinal=2, filename="second.jpg", path=second_path, sha256="b" * 64
            ),
        ),
        maximum_part_bytes=1024 * 1024,
    )

    assert len(parts) == 1

    with ZipFile(parts[0].path) as archive:
        names = set(archive.namelist())
        manifest = archive.read("manifest.csv").decode()

    assert "first.jpg" in names
    assert "second.jpg" in names
    assert "outside.jpg" not in names
    assert "1,first.jpg" in manifest
    assert "2,second.jpg" in manifest
    assert "outside" not in manifest
    assert parts[0].path.stat().st_size <= 1024 * 1024


def test_export_zip_rejects_single_part_larger_than_limit(tmp_path: Path) -> None:
    image_path = tmp_path / "large.jpg"
    image_path.write_bytes(b"x" * 2048)

    with pytest.raises(ExportZipPartTooLargeError) as error:
        build_export_zip_parts(
            output_dir=tmp_path / "exports",
            job_id="job-1",
            images=(
                ExportZipImage(
                    ordinal=1,
                    filename="large.jpg",
                    path=image_path,
                    sha256="a" * 64,
                ),
            ),
            maximum_part_bytes=1,
        )

    assert error.value.part_number == 1
