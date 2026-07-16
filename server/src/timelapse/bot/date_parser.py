from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

JAKARTA = ZoneInfo("Asia/Jakarta")
IMAGES_USAGE = "/images YYYY-MM-DD HH:mm YYYY-MM-DD HH:mm [camera] (Asia/Jakarta)"
MAX_EXPORT_RANGE = timedelta(hours=24)


class ExportDateParseError(ValueError):
    def __init__(self, *, code: str, usage: str = IMAGES_USAGE) -> None:
        super().__init__(code)
        self.code = code
        self.usage = usage


@dataclass(frozen=True)
class ParsedImagesCommand:
    start_at_utc: datetime
    end_at_utc: datetime
    camera_slug: str | None


def parse_images_command_args(args: list[str]) -> ParsedImagesCommand:
    if len(args) not in {4, 5}:
        raise ExportDateParseError(code="invalid_format")

    try:
        start_local = datetime.strptime(
            f"{args[0]} {args[1]}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=JAKARTA)
        end_local = datetime.strptime(
            f"{args[2]} {args[3]}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=JAKARTA)
    except ValueError as error:
        raise ExportDateParseError(code="invalid_format") from error

    start_at_utc = start_local.astimezone(UTC)
    end_at_utc = end_local.astimezone(UTC)

    if end_at_utc <= start_at_utc:
        raise ExportDateParseError(code="invalid_range")

    if end_at_utc - start_at_utc > MAX_EXPORT_RANGE:
        raise ExportDateParseError(code="range_too_large")

    return ParsedImagesCommand(
        start_at_utc=start_at_utc,
        end_at_utc=end_at_utc,
        camera_slug=args[4] if len(args) == 5 else None,
    )
