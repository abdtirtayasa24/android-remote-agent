from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from sqlalchemy import select
from timelapse.bot.authorization import (
    TelegramAuthorizationRequest,
    authorize_telegram_user,
)
from timelapse.bot.commands import (
    handle_help_command,
    handle_latest_command,
    handle_status_command,
)
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import Camera, Image, TelegramPrincipal
from timelapse.models.enums import CameraHealthState, CaptureSource, ImageStorageState

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@dataclass
class FakeTelegramSender:
    messages: list[tuple[int, str]] = field(default_factory=list)
    photos: list[tuple[int, Path, str | None]] = field(default_factory=list)

    async def send_message(self, *, chat_id: int, text: str) -> int:
        self.messages.append((chat_id, text))
        return len(self.messages)

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str | None = None,
    ) -> int:
        self.photos.append((chat_id, photo_path, caption))
        return len(self.photos)


def write_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full((180, 320, 3), 40, dtype=np.uint8)
    assert cv2.imwrite(str(path), image)


async def add_stored_image(*, camera_id, storage_root: Path, captured_at: datetime) -> Image:
    capture_id = uuid4()
    image_path = storage_root / f"{capture_id}.jpg"
    write_jpeg(image_path)
    image_bytes = image_path.read_bytes()

    async with session_scope() as session:
        stored_image = Image(
            capture_id=capture_id,
            camera_id=camera_id,
            captured_at_utc=captured_at,
            received_at_utc=captured_at,
            capture_source=CaptureSource.SCHEDULED,
            storage_state=ImageStorageState.STORED,
            storage_path=str(image_path),
            mime_type="image/jpeg",
            file_size_bytes=len(image_bytes),
            width_pixels=320,
            height_pixels=180,
            sha256=hashlib.sha256(image_bytes).hexdigest(),
        )
        session.add(stored_image)
        await session.flush()
        return stored_image


async def test_admin_user_id_bootstraps_principal_without_chat_id_env() -> None:
    async with session_scope() as session:
        authorized = await authorize_telegram_user(
            session=session,
            request=TelegramAuthorizationRequest(
                telegram_user_id=999,
                telegram_chat_id=222,
                display_name="Admin",
            ),
            admin_user_id=999,
        )

    assert authorized is not None
    assert authorized.role == "administrator"

    session_factory = get_session_factory()

    async with session_factory() as session:
        principal = await session.scalar(select(TelegramPrincipal))

    assert principal.telegram_user_id == 999
    assert principal.telegram_chat_id == 222
    assert principal.role == "administrator"


async def test_help_lists_supported_authorized_commands() -> None:
    text = handle_help_command(role="administrator")

    assert "/status" in text
    assert "/latest" in text
    assert "/images" in text
    assert "/exports" in text
    assert "/cancel" in text


async def test_status_returns_camera_health_without_storage_paths(create_camera) -> None:
    camera_fixture = await create_camera(slug="front-door")

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera.health_state = CameraHealthState.ONLINE
        camera.last_capture_at = NOW
        camera.last_upload_at = NOW

    async with session_scope() as session:
        text = await handle_status_command(session=session, args=[camera_fixture.slug])

    assert "Front Door" in text
    assert "online" in text
    assert "/srv/" not in text


async def test_latest_sends_latest_stored_image(create_camera, tmp_path: Path) -> None:
    camera_fixture = await create_camera(slug="front-door")
    sender = FakeTelegramSender()

    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera_id = camera.id

    older = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW.replace(hour=10),
    )
    latest = await add_stored_image(
        camera_id=camera_id,
        storage_root=tmp_path,
        captured_at=NOW,
    )

    async with session_scope() as session:
        await handle_latest_command(
            session=session,
            args=[camera_fixture.slug],
            chat_id=456,
            sender=sender,
        )

    assert older.id is not None
    assert sender.photos == [(456, Path(latest.storage_path), sender.photos[0][2])]
    assert "/srv/" not in (sender.photos[0][2] or "")
