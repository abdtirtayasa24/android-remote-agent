from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

JAKARTA_TIME_ZONE = ZoneInfo("Asia/Jakarta")
JAKARTA_TIME_LABEL = "Asia/Jakarta"


def format_jakarta_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)

    return f"{value.astimezone(JAKARTA_TIME_ZONE):%Y-%m-%d %H:%M:%S} {JAKARTA_TIME_LABEL}"
