from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from timelapse.services.heartbeat_aggregation import (
    HeartbeatAggregateInput,
    summarize_heartbeats,
)

DAY = datetime(2026, 7, 16, tzinfo=UTC).date()


def test_summarize_heartbeats_calculates_daily_extremes_and_offline_gap() -> None:
    first = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
    second = first + timedelta(minutes=5)
    third = first + timedelta(minutes=30)

    summary = summarize_heartbeats(
        summary_date=DAY,
        heartbeats=(
            HeartbeatAggregateInput(
                received_at=first,
                battery_percent=80,
                battery_temperature_c=Decimal("33.5"),
                pending_image_count=1,
                pending_image_bytes=100,
            ),
            HeartbeatAggregateInput(
                received_at=second,
                battery_percent=50,
                battery_temperature_c=Decimal("40.0"),
                pending_image_count=3,
                pending_image_bytes=500,
            ),
            HeartbeatAggregateInput(
                received_at=third,
                battery_percent=None,
                battery_temperature_c=None,
                pending_image_count=None,
                pending_image_bytes=None,
            ),
        ),
    )

    assert summary.heartbeat_count == 3
    assert summary.minimum_battery_percent == 50
    assert summary.maximum_temperature_c == Decimal("40.0")
    assert summary.maximum_pending_image_count == 3
    assert summary.maximum_pending_image_bytes == 500
    assert summary.offline_seconds == 10 * 60


def test_summarize_heartbeats_handles_empty_day() -> None:
    summary = summarize_heartbeats(summary_date=DAY, heartbeats=())

    assert summary.heartbeat_count == 0
    assert summary.minimum_battery_percent is None
    assert summary.maximum_temperature_c is None
    assert summary.offline_seconds == 0
