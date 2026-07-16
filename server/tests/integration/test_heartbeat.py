from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from timelapse.database import (
    get_session_factory,
)
from timelapse.models.entities import (
    Camera,
    CameraHeartbeat,
)


async def test_authenticated_heartbeat_is_stored(
    client: httpx.AsyncClient,
    create_camera,
) -> None:
    camera_fixture = await create_camera(slug="front-door")

    response = await client.post(
        ("/api/v1/cameras/front-door/heartbeats"),
        headers={"Authorization": (f"Bearer {camera_fixture.credential}")},
        json={
            "agent_version": "0.4.0",
            "sent_at_utc": ("2026-07-15T01:00:00Z"),
            "uptime_seconds": 3600,
            "battery_percent": 80,
            "battery_status": "CHARGING",
            "battery_temperature_c": 34.5,
            "available_storage_bytes": (10 * 1024 * 1024 * 1024),
            "pending_image_count": 2,
            "pending_image_bytes": 300000,
            "oldest_pending_at_utc": ("2026-07-15T00:58:00Z"),
            "last_capture_at_utc": ("2026-07-15T00:59:00Z"),
            "last_upload_at_utc": ("2026-07-15T00:59:10Z"),
            "dropped_image_count": 1,
            "consecutive_capture_failures": 0,
            "last_error_code": None,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["camera_enabled"] is True
    assert body["configuration_version"] == 1
    assert body["server_time_utc"].endswith("Z")

    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == "front-door"))
        heartbeat = await session.scalar(
            select(CameraHeartbeat).where(CameraHeartbeat.camera_id == camera.id)
        )

    assert camera is not None
    assert camera.last_seen_at is not None
    assert camera.last_capture_at == datetime(
        2026,
        7,
        15,
        0,
        59,
        tzinfo=UTC,
    )

    assert heartbeat is not None
    assert heartbeat.agent_version == "0.4.0"
    assert heartbeat.pending_image_count == 2
    assert heartbeat.dropped_image_count == 1


async def test_heartbeat_requires_timestamp_offset(
    client: httpx.AsyncClient,
    create_camera,
) -> None:
    camera_fixture = await create_camera(slug="front-door")

    response = await client.post(
        ("/api/v1/cameras/front-door/heartbeats"),
        headers={"Authorization": (f"Bearer {camera_fixture.credential}")},
        json={
            "agent_version": "0.4.0",
            "sent_at_utc": ("2026-07-15T01:00:00"),
        },
    )

    assert response.status_code == 422
