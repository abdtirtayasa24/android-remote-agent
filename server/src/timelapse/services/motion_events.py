from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import (
    Camera,
    Image,
    MotionAnalysis,
    MotionEvent,
    MotionEventImage,
)
from timelapse.services.telegram_messages import format_motion_alert_caption
from timelapse.services.telegram_recipients import load_telegram_recipient_chat_ids

LOGGER = logging.getLogger(__name__)


class MotionAlertSender(Protocol):
    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str | None = None,
    ) -> int | None: ...


@dataclass(frozen=True)
class MotionEventResult:
    event: MotionEvent
    created: bool


async def record_motion_detection(
    *,
    session: AsyncSession,
    image: Image,
    analysis: MotionAnalysis,
    cooldown_seconds: int,
) -> MotionEventResult:
    detected_at = image.captured_at_utc
    cooldown_window_start = detected_at - timedelta(seconds=cooldown_seconds)

    event = await session.scalar(
        select(MotionEvent)
        .where(MotionEvent.camera_id == image.camera_id)
        .where(MotionEvent.last_detected_at_utc >= cooldown_window_start)
        .order_by(MotionEvent.last_detected_at_utc.desc())
        .limit(1)
        .with_for_update()
    )

    peak_change_ratio = analysis.changed_pixel_ratio or Decimal("0")

    if event is None:
        event = MotionEvent(
            camera_id=image.camera_id,
            started_at_utc=detected_at,
            last_detected_at_utc=detected_at,
            peak_change_ratio=peak_change_ratio,
            representative_image_id=image.id,
            alert_status="pending",
        )
        session.add(event)
        await session.flush()
        created = True
    else:
        event.last_detected_at_utc = max(event.last_detected_at_utc, detected_at)
        event.peak_change_ratio = max(event.peak_change_ratio, peak_change_ratio)
        created = False

    session.add(
        MotionEventImage(
            event_id=event.id,
            image_id=image.id,
            detected_at=detected_at,
        )
    )

    return MotionEventResult(event=event, created=created)


async def send_motion_event_alert(
    *,
    session: AsyncSession,
    event: MotionEvent,
    image: Image,
    camera_display_name: str,
    camera_slug: str,
    sender: MotionAlertSender | None,
    admin_user_id: int | None = None,
) -> bool:
    if sender is None:
        return False

    recipient_chat_ids = await load_telegram_recipient_chat_ids(
        session=session,
        admin_user_id=admin_user_id,
    )

    if not recipient_chat_ids:
        return False

    caption = format_motion_alert_caption(
        camera_slug=camera_slug,
        camera_display_name=camera_display_name,
        detected_at=image.captured_at_utc,
    )
    message_id: int | None = None

    try:
        for chat_id in recipient_chat_ids:
            message_id = await sender.send_photo(
                chat_id=chat_id,
                photo_path=Path(image.storage_path),
                caption=caption,
            )
    except Exception as error:
        LOGGER.warning(
            "motion_alert_delivery_failed event_id=%s camera_id=%s error_type=%s",
            event.id,
            event.camera_id,
            type(error).__name__,
        )
        event.alert_status = "failed"
        return False

    event.alert_status = "sent"
    event.telegram_message_id = message_id
    return True


async def deliver_pending_motion_alerts_once(
    *,
    session: AsyncSession,
    sender: MotionAlertSender | None,
    admin_user_id: int | None = None,
    batch_size: int = 10,
) -> int:
    if sender is None:
        return 0

    events = (
        await session.scalars(
            select(MotionEvent)
            .where(MotionEvent.alert_status == "pending")
            .order_by(MotionEvent.created_at, MotionEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    delivered_count = 0

    for event in events:
        image = await session.get(Image, event.representative_image_id)
        camera = await session.get(Camera, event.camera_id)

        if image is None or camera is None:
            event.alert_status = "failed"
            continue

        delivered = await send_motion_event_alert(
            session=session,
            event=event,
            image=image,
            camera_display_name=camera.display_name,
            camera_slug=camera.slug,
            sender=sender,
            admin_user_id=admin_user_id,
        )

        if delivered:
            delivered_count += 1

    return delivered_count
