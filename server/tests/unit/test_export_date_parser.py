from __future__ import annotations

from datetime import UTC, datetime

import pytest
from timelapse.bot.date_parser import ExportDateParseError, parse_images_command_args


def test_cross_midnight_jakarta_range_converts_to_utc() -> None:
    parsed = parse_images_command_args(
        ["2026-07-16", "23:30", "2026-07-17", "00:30", "front-door"]
    )

    assert parsed.start_at_utc == datetime(2026, 7, 16, 16, 30, tzinfo=UTC)
    assert parsed.end_at_utc == datetime(2026, 7, 16, 17, 30, tzinfo=UTC)
    assert parsed.camera_slug == "front-door"


def test_exact_24_hour_range_is_accepted() -> None:
    parsed = parse_images_command_args(["2026-07-16", "00:00", "2026-07-17", "00:00"])

    assert (parsed.end_at_utc - parsed.start_at_utc).total_seconds() == 24 * 60 * 60
    assert parsed.camera_slug is None


def test_over_24_hour_range_is_rejected_with_usage() -> None:
    with pytest.raises(ExportDateParseError) as error:
        parse_images_command_args(["2026-07-16", "00:00", "2026-07-17", "00:01"])

    assert error.value.code == "range_too_large"
    assert "/images YYYY-MM-DD HH:mm YYYY-MM-DD HH:mm [camera]" in error.value.usage


def test_invalid_format_is_rejected_with_usage() -> None:
    with pytest.raises(ExportDateParseError) as error:
        parse_images_command_args(["2026/07/16", "00:00", "2026-07-17", "00:00"])

    assert error.value.code == "invalid_format"
    assert "/images" in error.value.usage
