from __future__ import annotations

from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.database import get_session
from timelapse.models.enums import CameraCommandStatus
from timelapse.schemas.commands import (
    CameraCommandResponse,
    CameraCommandResultRequest,
    CameraCommandResultResponse,
)
from timelapse.services.camera_authentication import (
    AuthenticatedCamera,
    authenticate_camera,
)
from timelapse.services.camera_commands import (
    CameraCommandError,
    claim_next_camera_command,
    load_camera_command_media,
    record_camera_command_result,
)

router = APIRouter(
    prefix="/api/v1/cameras",
    tags=["camera-commands"],
)


@router.get(
    "/{camera_slug}/commands/next",
    response_model=CameraCommandResponse,
)
async def claim_next_command(
    authenticated_camera: Annotated[
        AuthenticatedCamera,
        Depends(authenticate_camera),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CameraCommandResponse | Response:
    command = await claim_next_camera_command(
        session=session,
        camera_id=authenticated_camera.camera_id,
    )

    if command is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return CameraCommandResponse(
        id=command.id,
        command_type=command.command_type,
        media_size_bytes=cast(int, command.media_size_bytes),
        media_sha256=cast(str, command.media_sha256),
        media_mime_type=cast(str, command.media_mime_type),
        duration_seconds=int(command.payload.get("duration_seconds", 0)),
        expires_at_utc=command.expires_at,
    )


@router.get(
    "/{camera_slug}/commands/{command_id}/media",
    response_class=FileResponse,
)
async def download_command_media(
    command_id: UUID,
    authenticated_camera: Annotated[
        AuthenticatedCamera,
        Depends(authenticate_camera),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    try:
        command = await load_camera_command_media(
            session=session,
            camera_id=authenticated_camera.camera_id,
            command_id=command_id,
        )
    except CameraCommandError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": error.code},
        ) from None

    return FileResponse(
        path=Path(cast(str, command.media_storage_path)),
        media_type=cast(str, command.media_mime_type),
        filename=f"{command.id}.mp3",
    )


@router.post(
    "/{camera_slug}/commands/{command_id}/result",
    response_model=CameraCommandResultResponse,
)
async def report_command_result(
    command_id: UUID,
    payload: CameraCommandResultRequest,
    authenticated_camera: Annotated[
        AuthenticatedCamera,
        Depends(authenticate_camera),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CameraCommandResultResponse:
    try:
        command = await record_camera_command_result(
            session=session,
            camera_id=authenticated_camera.camera_id,
            command_id=command_id,
            status=CameraCommandStatus(payload.status),
            error_code=payload.error_code,
        )
    except CameraCommandError as error:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if error.code == "command_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": error.code},
        ) from None

    return CameraCommandResultResponse(id=command.id, status=command.status)
