from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from timelapse.models.enums import CameraHealthState
from timelapse.services.health import (
    HealthEvaluationInput,
    LatestHeartbeatSnapshot,
    evaluate_camera_health,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_disabled_camera_is_disabled_regardless_of_last_seen() -> None:
    evaluation = evaluate_camera_health(
        HealthEvaluationInput(
            enabled=False,
            last_seen_at=NOW,
            latest_heartbeat=None,
            now=NOW,
        )
    )

    assert evaluation.state == CameraHealthState.DISABLED
    assert evaluation.condition_codes == frozenset()


def test_camera_without_recent_contact_is_offline() -> None:
    evaluation = evaluate_camera_health(
        HealthEvaluationInput(
            enabled=True,
            last_seen_at=NOW - timedelta(minutes=16),
            latest_heartbeat=None,
            now=NOW,
        )
    )

    assert evaluation.state == CameraHealthState.OFFLINE
    assert evaluation.condition_codes == frozenset({"offline"})


def test_recent_camera_without_warning_conditions_is_online() -> None:
    evaluation = evaluate_camera_health(
        HealthEvaluationInput(
            enabled=True,
            last_seen_at=NOW - timedelta(minutes=4),
            latest_heartbeat=LatestHeartbeatSnapshot(
                battery_percent=80,
                battery_status="CHARGING",
                battery_temperature_c=Decimal("35.0"),
                available_storage_bytes=8 * 1024 * 1024 * 1024,
                pending_image_count=2,
                pending_image_bytes=200_000,
                oldest_pending_at=None,
                consecutive_capture_failures=0,
                last_error_code=None,
            ),
            now=NOW,
        )
    )

    assert evaluation.state == CameraHealthState.ONLINE
    assert evaluation.condition_codes == frozenset()


def test_warning_conditions_make_recent_camera_degraded() -> None:
    evaluation = evaluate_camera_health(
        HealthEvaluationInput(
            enabled=True,
            last_seen_at=NOW - timedelta(minutes=2),
            latest_heartbeat=LatestHeartbeatSnapshot(
                battery_percent=10,
                battery_status="DISCHARGING",
                battery_temperature_c=Decimal("46.0"),
                available_storage_bytes=400 * 1024 * 1024,
                pending_image_count=20,
                pending_image_bytes=2 * 1024 * 1024 * 1024,
                oldest_pending_at=NOW - timedelta(minutes=40),
                consecutive_capture_failures=3,
                last_error_code="heartbeat_recovered_after_3_failures",
            ),
            now=NOW,
        )
    )

    assert evaluation.state == CameraHealthState.DEGRADED
    assert evaluation.condition_codes == frozenset(
        {
            "battery_low",
            "temperature_high",
            "storage_low",
            "queue_large",
            "upload_delayed",
            "capture_failures",
            "heartbeat_failures",
        }
    )


def test_seen_between_online_and_offline_windows_is_degraded() -> None:
    evaluation = evaluate_camera_health(
        HealthEvaluationInput(
            enabled=True,
            last_seen_at=NOW - timedelta(minutes=12),
            latest_heartbeat=None,
            now=NOW,
        )
    )

    assert evaluation.state == CameraHealthState.DEGRADED
    assert evaluation.condition_codes == frozenset({"last_seen_stale"})
