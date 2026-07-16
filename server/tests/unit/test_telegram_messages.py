from __future__ import annotations

from datetime import UTC, datetime

from timelapse.models.enums import CameraHealthState
from timelapse.services.health import HealthEvaluation
from timelapse.services.telegram_messages import (
    format_health_alert_message,
    format_health_recovery_message,
    format_motion_alert_caption,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_health_alert_message_is_english_and_contains_no_storage_paths() -> None:
    message = format_health_alert_message(
        camera_slug="front-door",
        camera_display_name="Front Door",
        evaluation=HealthEvaluation(
            state=CameraHealthState.DEGRADED,
            condition_codes=frozenset({"battery_low", "storage_low"}),
            evaluated_at=NOW,
        ),
    )

    assert "Camera health warning" in message
    assert "Front Door" in message
    assert "battery low" in message.lower()
    assert "/srv/" not in message


def test_health_recovery_message_is_english() -> None:
    message = format_health_recovery_message(
        camera_slug="front-door",
        camera_display_name="Front Door",
        resolved_condition="offline",
        resolved_at=NOW,
    )

    assert "Camera recovered" in message
    assert "Front Door" in message
    assert "offline" in message


def test_motion_alert_caption_is_english_and_contains_no_storage_paths() -> None:
    message = format_motion_alert_caption(
        camera_slug="front-door",
        camera_display_name="Front Door",
        detected_at=NOW,
    )

    assert "Motion detected" in message
    assert "Front Door" in message
    assert "/srv/" not in message
