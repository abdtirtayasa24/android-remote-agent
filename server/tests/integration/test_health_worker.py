from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from timelapse.database import get_session_factory, session_scope
from timelapse.models.entities import AlertState, Camera, CameraHeartbeat, TelegramPrincipal
from timelapse.models.enums import CameraHealthState
from timelapse.services.health import evaluate_all_cameras_once

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@dataclass
class FakeTelegramSender:
    messages: list[tuple[int, str]] = field(default_factory=list)
    fail: bool = False

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
    ) -> int:
        if self.fail:
            raise RuntimeError("telegram unavailable")

        self.messages.append((chat_id, text))
        return len(self.messages)


async def add_telegram_recipient() -> None:
    async with session_scope() as session:
        session.add(
            TelegramPrincipal(
                telegram_user_id=123,
                telegram_chat_id=456,
                display_name="Operator",
                role="administrator",
            )
        )


async def test_health_worker_marks_camera_offline_within_sixteen_minutes(
    create_camera,
) -> None:
    camera_fixture = await create_camera(slug="front-door")

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera.last_seen_at = NOW - timedelta(minutes=16)
        camera.health_state = CameraHealthState.ONLINE

    async with session_scope() as session:
        processed_count = await evaluate_all_cameras_once(session=session, now=NOW)

    assert processed_count == 1

    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))

    assert camera.health_state == CameraHealthState.OFFLINE


async def test_health_alerts_are_not_duplicated_for_unchanged_state(
    create_camera,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    await add_telegram_recipient()
    sender = FakeTelegramSender()

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera.last_seen_at = NOW - timedelta(minutes=16)
        camera.health_state = CameraHealthState.ONLINE

    async with session_scope() as session:
        await evaluate_all_cameras_once(session=session, sender=sender, now=NOW)

    async with session_scope() as session:
        await evaluate_all_cameras_once(
            session=session,
            sender=sender,
            now=NOW + timedelta(minutes=1),
        )

    assert len(sender.messages) == 1
    assert "Camera health warning" in sender.messages[0][1]

    session_factory = get_session_factory()

    async with session_factory() as session:
        alert_states = (await session.scalars(select(AlertState))).all()

    assert len(alert_states) == 1
    assert alert_states[0].is_active is True
    assert alert_states[0].condition_code == "offline"


async def test_pending_health_alert_is_sent_when_recipient_becomes_available(
    create_camera,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    sender = FakeTelegramSender()

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera.last_seen_at = NOW - timedelta(minutes=16)
        camera.health_state = CameraHealthState.ONLINE

    async with session_scope() as session:
        await evaluate_all_cameras_once(session=session, sender=None, now=NOW)

    async with session_scope() as session:
        alert_state = await session.scalar(select(AlertState))
        assert alert_state.is_active is True
        assert alert_state.last_sent_at is None

    await add_telegram_recipient()

    async with session_scope() as session:
        await evaluate_all_cameras_once(
            session=session,
            sender=sender,
            now=NOW + timedelta(minutes=1),
        )

    assert len(sender.messages) == 1

    async with session_scope() as session:
        alert_state = await session.scalar(select(AlertState))
        assert alert_state.last_sent_at == NOW + timedelta(minutes=1)


async def test_health_alert_uses_admin_user_id_when_no_recipient_exists(
    create_camera,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    sender = FakeTelegramSender()

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera.last_seen_at = NOW - timedelta(minutes=16)
        camera.health_state = CameraHealthState.ONLINE

    async with session_scope() as session:
        await evaluate_all_cameras_once(
            session=session,
            sender=sender,
            now=NOW,
            admin_user_id=123,
        )

    assert sender.messages[0][0] == 123


async def test_telegram_failure_does_not_rollback_health_state(
    create_camera,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    await add_telegram_recipient()
    sender = FakeTelegramSender(fail=True)

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera.last_seen_at = NOW - timedelta(minutes=16)
        camera.health_state = CameraHealthState.ONLINE

    async with session_scope() as session:
        processed_count = await evaluate_all_cameras_once(
            session=session, sender=sender, now=NOW
        )

    assert processed_count == 1

    session_factory = get_session_factory()

    async with session_factory() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        alert_state = await session.scalar(select(AlertState))

    assert camera.health_state == CameraHealthState.OFFLINE
    assert alert_state.is_active is True
    assert alert_state.last_sent_at is None


async def test_health_recovery_alert_is_sent_once(
    create_camera,
) -> None:
    camera_fixture = await create_camera(slug="front-door")
    await add_telegram_recipient()
    sender = FakeTelegramSender()

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera.last_seen_at = NOW - timedelta(minutes=16)
        camera.health_state = CameraHealthState.ONLINE

    async with session_scope() as session:
        await evaluate_all_cameras_once(session=session, sender=sender, now=NOW)

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_fixture.slug))
        camera.last_seen_at = NOW
        session.add(
            CameraHeartbeat(
                camera_id=camera.id,
                received_at=NOW,
                agent_version="0.4.0",
                dropped_image_count=0,
                consecutive_capture_failures=0,
            )
        )

    async with session_scope() as session:
        await evaluate_all_cameras_once(
            session=session,
            sender=sender,
            now=NOW + timedelta(minutes=1),
        )

    async with session_scope() as session:
        await evaluate_all_cameras_once(
            session=session,
            sender=sender,
            now=NOW + timedelta(minutes=2),
        )

    assert len(sender.messages) == 2
    assert "Camera recovered" in sender.messages[1][1]
