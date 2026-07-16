from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import (
    AlertState,
    AuditEvent,
    Camera,
    CameraHeartbeat,
)
from timelapse.models.enums import CameraHealthState
from timelapse.services.telegram_messages import (
    format_health_alert_message,
    format_health_recovery_message,
)
from timelapse.services.telegram_recipients import load_telegram_recipient_chat_ids

ONLINE_WINDOW = timedelta(minutes=10)
OFFLINE_AFTER = timedelta(minutes=15)
UPLOAD_DELAY_AFTER = timedelta(minutes=30)
LOW_BATTERY_PERCENT = 20
HIGH_TEMPERATURE_C = Decimal("45.0")
LOW_STORAGE_BYTES = 1024 * 1024 * 1024
LARGE_QUEUE_BYTES = 1024 * 1024 * 1024
CAPTURE_FAILURE_THRESHOLD = 3
HEARTBEAT_FAILURE_THRESHOLD = 3
LOGGER = logging.getLogger(__name__)
_HEARTBEAT_RECOVERY_PATTERN = re.compile(
    r"^heartbeat_recovered_after_(?P<count>\d+)_failures$"
)


@dataclass(frozen=True)
class LatestHeartbeatSnapshot:
    battery_percent: int | None
    battery_status: str | None
    battery_temperature_c: Decimal | None
    available_storage_bytes: int | None
    pending_image_count: int | None
    pending_image_bytes: int | None
    oldest_pending_at: datetime | None
    consecutive_capture_failures: int
    last_error_code: str | None


@dataclass(frozen=True)
class HealthEvaluationInput:
    enabled: bool
    last_seen_at: datetime | None
    latest_heartbeat: LatestHeartbeatSnapshot | None
    now: datetime


@dataclass(frozen=True)
class HealthEvaluation:
    state: CameraHealthState
    condition_codes: frozenset[str]
    evaluated_at: datetime


@dataclass(frozen=True)
class AlertDeliveryResult:
    delivered: bool
    message_id: int | None
    outcome: str


class TelegramMessageSender(Protocol):
    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
    ) -> int | None: ...


def evaluate_camera_health(payload: HealthEvaluationInput) -> HealthEvaluation:
    now = payload.now.astimezone(UTC)

    if not payload.enabled:
        return HealthEvaluation(
            state=CameraHealthState.DISABLED,
            condition_codes=frozenset(),
            evaluated_at=now,
        )

    last_seen_at = _normalize_datetime(payload.last_seen_at)

    if last_seen_at is None or now - last_seen_at >= OFFLINE_AFTER:
        return HealthEvaluation(
            state=CameraHealthState.OFFLINE,
            condition_codes=frozenset({"offline"}),
            evaluated_at=now,
        )

    condition_codes = set(_degraded_condition_codes(payload.latest_heartbeat, now=now))

    if now - last_seen_at > ONLINE_WINDOW:
        condition_codes.add("last_seen_stale")

    if condition_codes:
        state = CameraHealthState.DEGRADED
    else:
        state = CameraHealthState.ONLINE

    return HealthEvaluation(
        state=state,
        condition_codes=frozenset(condition_codes),
        evaluated_at=now,
    )


async def evaluate_all_cameras_once(
    *,
    session: AsyncSession,
    sender: TelegramMessageSender | None = None,
    now: datetime | None = None,
    admin_user_id: int | None = None,
) -> int:
    evaluated_count = 0
    current_time = (now or datetime.now(UTC)).astimezone(UTC)

    cameras = (await session.scalars(select(Camera).order_by(Camera.slug))).all()

    for camera in cameras:
        latest_heartbeat = await load_latest_heartbeat_snapshot(
            session=session,
            camera_id=camera.id,
        )
        evaluation = evaluate_camera_health(
            HealthEvaluationInput(
                enabled=camera.enabled,
                last_seen_at=camera.last_seen_at,
                latest_heartbeat=latest_heartbeat,
                now=current_time,
            )
        )

        if camera.health_state != evaluation.state:
            session.add(
                AuditEvent(
                    event_type="camera_health_state_changed",
                    camera_id=camera.id,
                    outcome="recorded",
                    details={
                        "from": camera.health_state.value,
                        "to": evaluation.state.value,
                        "conditions": sorted(evaluation.condition_codes),
                    },
                )
            )
            camera.health_state = evaluation.state

        await sync_health_alerts(
            session=session,
            camera=camera,
            evaluation=evaluation,
            sender=sender,
            admin_user_id=admin_user_id,
        )
        evaluated_count += 1

    return evaluated_count


async def load_latest_heartbeat_snapshot(
    *,
    session: AsyncSession,
    camera_id: UUID,
) -> LatestHeartbeatSnapshot | None:
    heartbeat = await session.scalar(
        select(CameraHeartbeat)
        .where(CameraHeartbeat.camera_id == camera_id)
        .order_by(CameraHeartbeat.received_at.desc())
        .limit(1)
    )

    if heartbeat is None:
        return None

    return LatestHeartbeatSnapshot(
        battery_percent=heartbeat.battery_percent,
        battery_status=heartbeat.battery_status,
        battery_temperature_c=heartbeat.battery_temperature_c,
        available_storage_bytes=heartbeat.available_storage_bytes,
        pending_image_count=heartbeat.pending_image_count,
        pending_image_bytes=heartbeat.pending_image_bytes,
        oldest_pending_at=heartbeat.oldest_pending_at,
        consecutive_capture_failures=heartbeat.consecutive_capture_failures,
        last_error_code=heartbeat.last_error_code,
    )


async def sync_health_alerts(
    *,
    session: AsyncSession,
    camera: Camera,
    evaluation: HealthEvaluation,
    sender: TelegramMessageSender | None,
    admin_user_id: int | None = None,
) -> None:
    desired_conditions = _alert_conditions(evaluation)
    existing_states = {
        (state.alert_type, state.condition_code): state
        for state in (
            await session.scalars(
                select(AlertState)
                .where(AlertState.camera_id == camera.id)
                .where(AlertState.alert_type == "health")
            )
        ).all()
    }

    for condition_code in sorted(desired_conditions):
        key = ("health", condition_code)
        alert_state = existing_states.get(key)

        if alert_state is None:
            alert_state = AlertState(
                camera_id=camera.id,
                alert_type="health",
                condition_code=condition_code,
            )
            session.add(alert_state)

        alert_state.last_observed_at = evaluation.evaluated_at
        should_send_alert = not alert_state.is_active or alert_state.last_sent_at is None
        alert_state.is_active = True

        if not should_send_alert:
            continue

        message = format_health_alert_message(
            camera_slug=camera.slug,
            camera_display_name=camera.display_name,
            evaluation=evaluation,
        )
        delivery = await _send_to_alert_recipients(
            session=session,
            sender=sender,
            text=message,
            admin_user_id=admin_user_id,
            camera_id=camera.id,
            condition_code=condition_code,
        )

        if delivery.delivered:
            alert_state.last_sent_at = evaluation.evaluated_at
            alert_state.last_telegram_message_id = delivery.message_id

        _record_alert_audit(
            session=session,
            camera_id=camera.id,
            condition_code=condition_code,
            outcome=delivery.outcome,
        )

    for key, alert_state in existing_states.items():
        _, condition_code = key

        if not alert_state.is_active or condition_code in desired_conditions:
            continue

        alert_state.is_active = False
        alert_state.last_resolved_at = evaluation.evaluated_at
        message = format_health_recovery_message(
            camera_slug=camera.slug,
            camera_display_name=camera.display_name,
            resolved_condition=condition_code,
            resolved_at=evaluation.evaluated_at,
        )
        if alert_state.last_sent_at is None:
            _record_alert_audit(
                session=session,
                camera_id=camera.id,
                condition_code=condition_code,
                outcome="resolved_without_prior_delivery",
            )
            continue

        delivery = await _send_to_alert_recipients(
            session=session,
            sender=sender,
            text=message,
            admin_user_id=admin_user_id,
            camera_id=camera.id,
            condition_code=condition_code,
        )

        if delivery.delivered:
            alert_state.last_telegram_message_id = delivery.message_id

        _record_alert_audit(
            session=session,
            camera_id=camera.id,
            condition_code=condition_code,
            outcome=("resolved" if delivery.delivered else f"resolved_{delivery.outcome}"),
        )


def _alert_conditions(evaluation: HealthEvaluation) -> frozenset[str]:
    if evaluation.state == CameraHealthState.OFFLINE:
        return frozenset({"offline"})

    if evaluation.state == CameraHealthState.DEGRADED:
        return evaluation.condition_codes

    return frozenset()


async def _send_to_alert_recipients(
    *,
    session: AsyncSession,
    sender: TelegramMessageSender | None,
    text: str,
    admin_user_id: int | None,
    camera_id: UUID,
    condition_code: str,
) -> AlertDeliveryResult:
    if sender is None:
        return AlertDeliveryResult(
            delivered=False,
            message_id=None,
            outcome="skipped_no_sender",
        )

    recipient_chat_ids = await load_telegram_recipient_chat_ids(
        session=session,
        admin_user_id=admin_user_id,
    )

    if not recipient_chat_ids:
        return AlertDeliveryResult(
            delivered=False,
            message_id=None,
            outcome="skipped_no_recipient",
        )

    message_id: int | None = None

    try:
        for chat_id in recipient_chat_ids:
            message_id = await sender.send_message(
                chat_id=chat_id,
                text=text,
            )
    except Exception as error:
        LOGGER.warning(
            "health_alert_delivery_failed camera_id=%s condition_code=%s error_type=%s",
            camera_id,
            condition_code,
            type(error).__name__,
        )
        return AlertDeliveryResult(
            delivered=False,
            message_id=None,
            outcome="failed",
        )

    return AlertDeliveryResult(delivered=True, message_id=message_id, outcome="sent")


def _record_alert_audit(
    *,
    session: AsyncSession,
    camera_id: UUID,
    condition_code: str,
    outcome: str,
) -> None:
    session.add(
        AuditEvent(
            event_type="health_alert",
            camera_id=camera_id,
            outcome=outcome,
            details={"condition_code": condition_code},
        )
    )


def _degraded_condition_codes(
    latest_heartbeat: LatestHeartbeatSnapshot | None,
    *,
    now: datetime,
) -> set[str]:
    if latest_heartbeat is None:
        return set()

    condition_codes: set[str] = set()

    if (
        latest_heartbeat.battery_percent is not None
        and latest_heartbeat.battery_percent < LOW_BATTERY_PERCENT
        and not _is_charging(latest_heartbeat.battery_status)
    ):
        condition_codes.add("battery_low")

    if (
        latest_heartbeat.battery_temperature_c is not None
        and latest_heartbeat.battery_temperature_c >= HIGH_TEMPERATURE_C
    ):
        condition_codes.add("temperature_high")

    if (
        latest_heartbeat.available_storage_bytes is not None
        and latest_heartbeat.available_storage_bytes < LOW_STORAGE_BYTES
    ):
        condition_codes.add("storage_low")

    if (
        latest_heartbeat.pending_image_bytes is not None
        and latest_heartbeat.pending_image_bytes > LARGE_QUEUE_BYTES
    ):
        condition_codes.add("queue_large")

    oldest_pending_at = _normalize_datetime(latest_heartbeat.oldest_pending_at)

    if oldest_pending_at is not None and now - oldest_pending_at > UPLOAD_DELAY_AFTER:
        condition_codes.add("upload_delayed")

    if latest_heartbeat.consecutive_capture_failures >= CAPTURE_FAILURE_THRESHOLD:
        condition_codes.add("capture_failures")

    if (
        _recovered_heartbeat_failures(latest_heartbeat.last_error_code)
        >= HEARTBEAT_FAILURE_THRESHOLD
    ):
        condition_codes.add("heartbeat_failures")

    return condition_codes


def _is_charging(status: str | None) -> bool:
    return status is not None and status.upper() in {"CHARGING", "FULL"}


def _recovered_heartbeat_failures(error_code: str | None) -> int:
    if error_code is None:
        return 0

    match = _HEARTBEAT_RECOVERY_PATTERN.fullmatch(error_code)

    if match is None:
        return 0

    return int(match.group("count"))


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)
