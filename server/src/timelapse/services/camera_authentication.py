from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.configuration import Settings, get_settings
from timelapse.database import get_session, session_scope
from timelapse.models.entities import Camera, CameraCredential
from timelapse.services.camera_credentials import (
    camera_secret_matches,
    parse_camera_credential,
)


@dataclass(frozen=True)
class AuthenticatedCamera:
    camera_id: UUID
    credential_id: UUID
    slug: str
    maximum_width: int
    maximum_height: int


def authentication_failure() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "invalid_camera_credential"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def record_credential_use(credential_id: UUID) -> None:
    async with session_scope() as session:
        await session.execute(
            update(CameraCredential)
            .where(CameraCredential.id == credential_id)
            .values(last_used_at=datetime.now(UTC))
        )


async def authenticate_camera(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedCamera:
    if settings.require_https and request.url.scheme != "https":
        raise HTTPException(
            status_code=400,
            detail={"code": "https_required"},
        )

    authorization = request.headers.get("authorization", "")
    scheme, separator, plaintext = authorization.partition(" ")

    if not separator or scheme.lower() != "bearer" or not plaintext:
        raise authentication_failure()

    parsed = parse_camera_credential(plaintext.strip())

    if parsed is None:
        raise authentication_failure()

    result = await session.execute(
        select(CameraCredential, Camera)
        .join(
            Camera,
            Camera.id == CameraCredential.camera_id,
        )
        .where(CameraCredential.token_id == parsed.token_id)
    )

    credential_and_camera = result.one_or_none()

    if credential_and_camera is None:
        raise authentication_failure()

    credential, camera = credential_and_camera
    now = datetime.now(UTC)

    stored_digest = bytes(credential.secret_digest)

    if not camera_secret_matches(
        secret=parsed.secret,
        expected_digest=stored_digest,
        pepper=settings.camera_token_pepper.get_secret_value(),
    ):
        raise authentication_failure()

    if credential.revoked_at is not None:
        raise authentication_failure()

    if credential.expires_at is not None and credential.expires_at <= now:
        raise authentication_failure()

    requested_camera_slug = request.path_params.get("camera_slug")

    if camera.slug != requested_camera_slug:
        raise authentication_failure()

    if not camera.enabled:
        raise authentication_failure()

    background_tasks.add_task(
        record_credential_use,
        credential.id,
    )

    return AuthenticatedCamera(
        camera_id=camera.id,
        credential_id=credential.id,
        slug=camera.slug,
        maximum_width=camera.maximum_width,
        maximum_height=camera.maximum_height,
    )
