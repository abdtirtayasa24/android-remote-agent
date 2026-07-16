from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from timelapse.services.health import HealthEvaluation

_CONDITION_LABELS = {
    "battery_low": "battery low",
    "temperature_high": "temperature high",
    "storage_low": "phone storage low",
    "queue_large": "local queue large",
    "upload_delayed": "upload delayed",
    "capture_failures": "capture failures",
    "heartbeat_failures": "heartbeat send failures",
    "last_seen_stale": "last seen stale",
    "offline": "offline",
}


def format_health_alert_message(
    *,
    camera_slug: str,
    camera_display_name: str,
    evaluation: HealthEvaluation,
) -> str:
    conditions = ", ".join(
        _CONDITION_LABELS.get(code, code.replace("_", " "))
        for code in sorted(evaluation.condition_codes)
    )

    if not conditions:
        conditions = evaluation.state.value

    return "\n".join(
        (
            f"Camera health warning — {camera_display_name}",
            f"Camera: {camera_slug}",
            f"State: {evaluation.state.value}",
            f"Conditions: {conditions}",
            f"Time: {_format_utc(evaluation.evaluated_at)} UTC",
        )
    )


def format_health_recovery_message(
    *,
    camera_slug: str,
    camera_display_name: str,
    resolved_condition: str,
    resolved_at: datetime,
) -> str:
    condition = _CONDITION_LABELS.get(
        resolved_condition,
        resolved_condition.replace("_", " "),
    )

    return "\n".join(
        (
            f"Camera recovered — {camera_display_name}",
            f"Camera: {camera_slug}",
            f"Resolved condition: {condition}",
            f"Time: {_format_utc(resolved_at)} UTC",
        )
    )


def format_motion_alert_caption(
    *,
    camera_slug: str,
    camera_display_name: str,
    detected_at: datetime,
) -> str:
    return "\n".join(
        (
            f"Motion detected — {camera_display_name}",
            f"Camera: {camera_slug}",
            f"Time: {_format_utc(detected_at)} UTC",
        )
    )


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
