from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import (
    Camera,
    CameraHeartbeat,
    HeartbeatDailySummary,
)

OFFLINE_AFTER = timedelta(minutes=15)
DETAILED_HEARTBEAT_RETENTION_DAYS = 7


@dataclass(frozen=True)
class HeartbeatAggregateInput:
    received_at: datetime
    battery_percent: int | None
    battery_temperature_c: Decimal | None
    pending_image_count: int | None
    pending_image_bytes: int | None


@dataclass(frozen=True)
class HeartbeatDailyAggregate:
    summary_date: date
    heartbeat_count: int
    minimum_battery_percent: int | None
    maximum_temperature_c: Decimal | None
    maximum_pending_image_count: int | None
    maximum_pending_image_bytes: int | None
    offline_seconds: int


async def aggregate_due_heartbeats_once(
    *,
    session: AsyncSession,
    now: datetime | None = None,
) -> int:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff_date = current_time.date() - timedelta(days=DETAILED_HEARTBEAT_RETENTION_DAYS)
    cutoff_at = datetime.combine(cutoff_date, time.min, tzinfo=UTC)
    processed_days = 0

    camera_ids = (await session.scalars(select(Camera.id).order_by(Camera.slug))).all()

    for camera_id in camera_ids:
        heartbeat_dates = (
            await session.scalars(
                select(func.date(CameraHeartbeat.received_at))
                .where(CameraHeartbeat.camera_id == camera_id)
                .where(CameraHeartbeat.received_at < cutoff_at)
                .distinct()
                .order_by(func.date(CameraHeartbeat.received_at))
            )
        ).all()

        for raw_summary_date in heartbeat_dates:
            summary_date = _coerce_date(raw_summary_date)
            day_start = datetime.combine(summary_date, time.min, tzinfo=UTC)
            day_end = day_start + timedelta(days=1)
            heartbeats = (
                await session.scalars(
                    select(CameraHeartbeat)
                    .where(CameraHeartbeat.camera_id == camera_id)
                    .where(CameraHeartbeat.received_at >= day_start)
                    .where(CameraHeartbeat.received_at < day_end)
                    .order_by(CameraHeartbeat.received_at)
                )
            ).all()

            aggregate = summarize_heartbeats(
                summary_date=summary_date,
                heartbeats=tuple(
                    HeartbeatAggregateInput(
                        received_at=heartbeat.received_at,
                        battery_percent=heartbeat.battery_percent,
                        battery_temperature_c=heartbeat.battery_temperature_c,
                        pending_image_count=heartbeat.pending_image_count,
                        pending_image_bytes=heartbeat.pending_image_bytes,
                    )
                    for heartbeat in heartbeats
                ),
            )
            await upsert_daily_summary(
                session=session,
                camera_id=camera_id,
                aggregate=aggregate,
            )
            await session.execute(
                delete(CameraHeartbeat)
                .where(CameraHeartbeat.camera_id == camera_id)
                .where(CameraHeartbeat.received_at >= day_start)
                .where(CameraHeartbeat.received_at < day_end)
            )
            processed_days += 1

    return processed_days


def summarize_heartbeats(
    *,
    summary_date: date,
    heartbeats: tuple[HeartbeatAggregateInput, ...],
) -> HeartbeatDailyAggregate:
    ordered = sorted(heartbeats, key=lambda heartbeat: heartbeat.received_at)
    battery_values = [
        heartbeat.battery_percent
        for heartbeat in ordered
        if heartbeat.battery_percent is not None
    ]
    temperature_values = [
        heartbeat.battery_temperature_c
        for heartbeat in ordered
        if heartbeat.battery_temperature_c is not None
    ]
    pending_count_values = [
        heartbeat.pending_image_count
        for heartbeat in ordered
        if heartbeat.pending_image_count is not None
    ]
    pending_byte_values = [
        heartbeat.pending_image_bytes
        for heartbeat in ordered
        if heartbeat.pending_image_bytes is not None
    ]

    return HeartbeatDailyAggregate(
        summary_date=summary_date,
        heartbeat_count=len(ordered),
        minimum_battery_percent=(min(battery_values) if battery_values else None),
        maximum_temperature_c=(max(temperature_values) if temperature_values else None),
        maximum_pending_image_count=(
            max(pending_count_values) if pending_count_values else None
        ),
        maximum_pending_image_bytes=(
            max(pending_byte_values) if pending_byte_values else None
        ),
        offline_seconds=_offline_seconds(ordered),
    )


async def upsert_daily_summary(
    *,
    session: AsyncSession,
    camera_id: UUID,
    aggregate: HeartbeatDailyAggregate,
) -> None:
    existing = await session.scalar(
        select(HeartbeatDailySummary)
        .where(HeartbeatDailySummary.camera_id == camera_id)
        .where(HeartbeatDailySummary.summary_date_utc == aggregate.summary_date)
        .with_for_update()
    )

    if existing is None:
        session.add(
            HeartbeatDailySummary(
                camera_id=camera_id,
                summary_date_utc=aggregate.summary_date,
                heartbeat_count=aggregate.heartbeat_count,
                minimum_battery_percent=aggregate.minimum_battery_percent,
                maximum_temperature_c=aggregate.maximum_temperature_c,
                maximum_pending_image_count=aggregate.maximum_pending_image_count,
                maximum_pending_image_bytes=aggregate.maximum_pending_image_bytes,
                offline_seconds=aggregate.offline_seconds,
            )
        )
        return

    existing.heartbeat_count = aggregate.heartbeat_count
    existing.minimum_battery_percent = aggregate.minimum_battery_percent
    existing.maximum_temperature_c = aggregate.maximum_temperature_c
    existing.maximum_pending_image_count = aggregate.maximum_pending_image_count
    existing.maximum_pending_image_bytes = aggregate.maximum_pending_image_bytes
    existing.offline_seconds = aggregate.offline_seconds


def _offline_seconds(heartbeats: list[HeartbeatAggregateInput]) -> int:
    offline_seconds = 0

    for previous, current in zip(heartbeats, heartbeats[1:], strict=False):
        previous_at = _normalize_datetime(previous.received_at)
        current_at = _normalize_datetime(current.received_at)
        gap = current_at - previous_at

        if gap > OFFLINE_AFTER:
            offline_seconds += int((gap - OFFLINE_AFTER).total_seconds())

    return offline_seconds


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _coerce_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, str):
        return date.fromisoformat(value)

    raise TypeError(f"unsupported date value: {value!r}")
