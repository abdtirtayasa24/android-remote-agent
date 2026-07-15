from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from timelapse.configuration import Settings
from timelapse.services.image_files import (
    UploadRejectedError,
    build_image_storage_path,
    inspect_jpeg,
)
from timelapse.services.image_upload import (
    parse_captured_at_utc,
)


def create_jpeg(
    *,
    width: int,
    height: int,
) -> bytes:
    output = BytesIO()

    Image.new(
        "RGB",
        (width, height),
        "white",
    ).save(
        output,
        format="JPEG",
        quality=72,
    )

    return output.getvalue()


def test_valid_jpeg_is_decoded(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(
        create_jpeg(
            width=1280,
            height=720,
        )
    )

    dimensions = inspect_jpeg(
        image_path,
        1280,
        720,
    )

    assert dimensions == (1280, 720)


def test_non_jpeg_is_rejected(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_text(
        "not an image",
        encoding="utf-8",
    )

    with pytest.raises(
        UploadRejectedError,
        match="invalid_jpeg",
    ):
        inspect_jpeg(
            image_path,
            1280,
            720,
        )


def test_excessive_dimensions_are_rejected(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(
        create_jpeg(
            width=1920,
            height=1080,
        )
    )

    with pytest.raises(
        UploadRejectedError,
        match="image_dimensions_exceeded",
    ):
        inspect_jpeg(
            image_path,
            1280,
            720,
        )


def test_timestamp_requires_timezone_offset() -> None:
    with pytest.raises(
        UploadRejectedError,
        match="captured_at_offset_required",
    ):
        parse_captured_at_utc("2026-07-14T01:00:00")


def test_timestamp_is_converted_to_utc() -> None:
    parsed = parse_captured_at_utc(
        "2026-07-14T08:00:00+07:00",
        now=datetime(
            2026,
            7,
            14,
            2,
            0,
            tzinfo=UTC,
        ),
    )

    assert parsed == datetime(
        2026,
        7,
        14,
        1,
        0,
        tzinfo=UTC,
    )


def test_storage_path_ignores_client_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        ("postgresql://timelapse:test-password@localhost:5432/timelapse_test"),
    )
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        ("postgresql://timelapse:test-password@localhost:5432/timelapse_test"),
    )
    monkeypatch.setenv(
        "CAMERA_TOKEN_PEPPER",
        "x" * 64,
    )
    monkeypatch.setenv(
        "STORAGE_ROOT",
        str(tmp_path),
    )

    settings = Settings()
    capture_id = uuid4()

    path = build_image_storage_path(
        settings=settings,
        camera_slug="front-door",
        captured_at_utc=datetime(
            2026,
            7,
            14,
            1,
            2,
            3,
            tzinfo=timezone.utc,
        ),
        capture_id=capture_id,
    )

    assert path == (
        tmp_path
        / "images"
        / "front-door"
        / "2026"
        / "07"
        / "14"
        / f"20260714T010203Z_{capture_id}.jpg"
    )

    assert "client-controlled-name" not in str(path)
