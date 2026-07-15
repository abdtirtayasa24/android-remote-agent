from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.database import get_session
from timelapse.models.entities import (
    Camera,
    CameraHeartbeat,
)
from timelapse.schemas.heartbeats import (
    CameraHeartbeatRequest,
    CameraHeartbeatResponse,
)
from timelapse.services.camera_authentication import (
    AuthenticatedCamera,
    authenticate_camera,
)

UTC = UTC

router = APIRouter(
    prefix="/api/v1/cameras",
    tags=["camera-heartbeats"],
)


@router.post(
    "/{camera_slug}/heartbeats",
    response_model=CameraHeartbeatResponse,
    status_code=200,
)
async def receive_heartbeat(
    payload: CameraHeartbeatRequest,
    authenticated_camera: Annotated[
        AuthenticatedCamera,
        Depends(authenticate_camera),
    ],
    session: Annotated[
        AsyncSession,
        Depends(get_session),
    ],
) -> CameraHeartbeatResponse:
    received_at = datetime.now(UTC)

    try:
        camera = await session.scalar(
            select(Camera).where(Camera.id == authenticated_camera.camera_id).with_for_update()
        )

        if camera is None:
            raise RuntimeError("Authenticated camera disappeared")

        session.add(
            CameraHeartbeat(
                camera_id=camera.id,
                received_at=received_at,
                device_sent_at=(payload.sent_at_utc),
                agent_version=(payload.agent_version),
                uptime_seconds=(payload.uptime_seconds),
                battery_percent=(payload.battery_percent),
                battery_status=(payload.battery_status),
                battery_temperature_c=(payload.battery_temperature_c),
                available_storage_bytes=(payload.available_storage_bytes),
                pending_image_count=(payload.pending_image_count),
                pending_image_bytes=(payload.pending_image_bytes),
                oldest_pending_at=(payload.oldest_pending_at_utc),
                last_capture_at=(payload.last_capture_at_utc),
                last_upload_at=(payload.last_upload_at_utc),
                dropped_image_count=(payload.dropped_image_count),
                consecutive_capture_failures=(payload.consecutive_capture_failures),
                last_error_code=(payload.last_error_code),
            )
        )

        camera.last_seen_at = received_at

        if payload.last_capture_at_utc is not None and (
            camera.last_capture_at is None
            or payload.last_capture_at_utc > camera.last_capture_at
        ):
            camera.last_capture_at = payload.last_capture_at_utc

        if payload.last_upload_at_utc is not None and (
            camera.last_upload_at is None or payload.last_upload_at_utc > camera.last_upload_at
        ):
            camera.last_upload_at = payload.last_upload_at_utc

        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return CameraHeartbeatResponse(
        server_time_utc=received_at,
        camera_enabled=camera.enabled,
        configuration_version=(camera.configuration_version),
    )
