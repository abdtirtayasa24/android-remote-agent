from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import Camera, CameraHeartbeat, HeartbeatDailySummary
from timelapse.services.heartbeat_aggregation import aggregate_due_heartbeats_once

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
OLD_DAY = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


async def test_heartbeat_aggregation_is_idempotent_and_expires_detail_rows(
    create_camera,
) -> None:
    camera_fixture = await create_camera(slug="front-door")

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        session.add_all(
            [
                CameraHeartbeat(
                    camera_id=camera.id,
                    received_at=OLD_DAY,
                    agent_version="0.4.0",
                    battery_percent=80,
                    battery_temperature_c=Decimal("34.0"),
                    pending_image_count=1,
                    pending_image_bytes=100,
                    dropped_image_count=0,
                    consecutive_capture_failures=0,
                ),
                CameraHeartbeat(
                    camera_id=camera.id,
                    received_at=OLD_DAY + timedelta(minutes=30),
                    agent_version="0.4.0",
                    battery_percent=60,
                    battery_temperature_c=Decimal("38.0"),
                    pending_image_count=5,
                    pending_image_bytes=500,
                    dropped_image_count=0,
                    consecutive_capture_failures=0,
                ),
            ]
        )

    async with session_scope() as session:
        first_processed = await aggregate_due_heartbeats_once(session=session, now=NOW)

    async with session_scope() as session:
        second_processed = await aggregate_due_heartbeats_once(session=session, now=NOW)

    session_factory = get_session_factory()

    async with session_factory() as session:
        summaries = (await session.scalars(select(HeartbeatDailySummary))).all()
        detail_rows = (await session.scalars(select(CameraHeartbeat))).all()

    assert first_processed == 1
    assert second_processed == 0
    assert len(summaries) == 1
    assert summaries[0].heartbeat_count == 2
    assert summaries[0].minimum_battery_percent == 60
    assert summaries[0].maximum_temperature_c == Decimal("38.00")
    assert summaries[0].maximum_pending_image_count == 5
    assert summaries[0].maximum_pending_image_bytes == 500
    assert summaries[0].offline_seconds == 15 * 60
    assert detail_rows == []
