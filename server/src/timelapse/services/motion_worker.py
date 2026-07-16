from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import Camera, Image, MotionAnalysis
from timelapse.models.enums import AnalysisStatus, CaptureSource, ImageStorageState
from timelapse.services.motion_detection import (
    MotionDetectionConfig,
    MotionDetectionStatus,
    detect_motion,
)
from timelapse.services.motion_events import (
    MotionAlertSender,
    deliver_pending_motion_alerts_once,
    record_motion_detection,
    send_motion_event_alert,
)

DEFAULT_STALE_AFTER = timedelta(minutes=5)
DEFAULT_BATCH_SIZE = 10
RATIO_QUANTUM = Decimal("0.000001")
BRIGHTNESS_QUANTUM = Decimal("0.001")


async def process_due_motion_analyses_once(
    *,
    session: AsyncSession,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sender: MotionAlertSender | None = None,
    admin_user_id: int | None = None,
) -> int:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    analyses = await claim_due_motion_analyses(
        session=session,
        now=current_time,
        stale_after=stale_after,
        batch_size=batch_size,
    )

    processed_count = 0

    for analysis in analyses:
        await process_claimed_motion_analysis(
            session=session,
            analysis=analysis,
            now=current_time,
            sender=sender,
            admin_user_id=admin_user_id,
        )
        processed_count += 1

    delivered_alert_count = await deliver_pending_motion_alerts_once(
        session=session,
        sender=sender,
        admin_user_id=admin_user_id,
    )

    return processed_count + delivered_alert_count


async def claim_due_motion_analyses(
    *,
    session: AsyncSession,
    now: datetime,
    stale_after: timedelta,
    batch_size: int,
) -> tuple[MotionAnalysis, ...]:
    stale_before = now - stale_after
    eligible_status = or_(
        MotionAnalysis.status == AnalysisStatus.PENDING,
        and_(
            MotionAnalysis.status == AnalysisStatus.PROCESSING,
            or_(
                MotionAnalysis.claimed_at.is_(None),
                MotionAnalysis.claimed_at < stale_before,
            ),
        ),
    )
    analyses = (
        await session.scalars(
            select(MotionAnalysis)
            .join(Image, Image.id == MotionAnalysis.image_id)
            .where(eligible_status)
            .order_by(Image.captured_at_utc, MotionAnalysis.image_id)
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=MotionAnalysis)
        )
    ).all()

    for analysis in analyses:
        analysis.status = AnalysisStatus.PROCESSING
        analysis.claimed_at = now
        analysis.error_message = None

    await session.flush()

    return tuple(analyses)


async def process_claimed_motion_analysis(
    *,
    session: AsyncSession,
    analysis: MotionAnalysis,
    now: datetime,
    sender: MotionAlertSender | None,
    admin_user_id: int | None,
) -> None:
    await _process_claimed_motion_analysis(
        session=session,
        analysis=analysis,
        now=now,
        sender=sender,
        admin_user_id=admin_user_id,
    )


async def _process_claimed_motion_analysis(
    *,
    session: AsyncSession,
    analysis: MotionAnalysis,
    now: datetime,
    sender: MotionAlertSender | None,
    admin_user_id: int | None,
) -> None:
    image = await session.get(Image, analysis.image_id)

    if image is None:
        _mark_skipped(analysis, reason="image_missing", now=now)
        return

    camera = await session.get(Camera, image.camera_id)

    if camera is None:
        _mark_skipped(analysis, reason="camera_missing", now=now)
        return

    if not camera.motion_enabled:
        _mark_skipped(analysis, reason="motion_disabled", now=now)
        return

    if image.storage_state != ImageStorageState.STORED or image.deleted_at is not None:
        _mark_skipped(analysis, reason="image_not_stored", now=now)
        return

    previous_image = await _load_previous_image(session=session, image=image)

    if previous_image is None:
        _mark_skipped(analysis, reason="no_previous_image", now=now)
        return

    analysis.previous_image_id = previous_image.id

    maximum_previous_age = timedelta(seconds=camera.capture_interval_seconds * 3)

    if image.captured_at_utc - previous_image.captured_at_utc > maximum_previous_age:
        _mark_skipped(analysis, reason="previous_image_too_old", now=now)
        return

    try:
        result = await asyncio.to_thread(
            detect_motion,
            previous_image_path=Path(previous_image.storage_path),
            current_image_path=Path(image.storage_path),
            config=MotionDetectionConfig(
                pixel_threshold=camera.motion_pixel_threshold,
                changed_ratio_threshold=float(camera.motion_changed_ratio),
                region_ratio_threshold=float(camera.motion_region_ratio),
            ),
        )
    except Exception as error:
        analysis.status = AnalysisStatus.FAILED
        analysis.analyzed_at = now
        analysis.error_message = type(error).__name__
        return

    analysis.changed_pixel_ratio = _decimal_or_none(
        result.changed_pixel_ratio,
        quantum=RATIO_QUANTUM,
    )
    analysis.largest_region_ratio = _decimal_or_none(
        result.largest_region_ratio,
        quantum=RATIO_QUANTUM,
    )
    analysis.brightness_delta = _decimal_or_none(
        result.brightness_delta,
        quantum=BRIGHTNESS_QUANTUM,
    )
    analysis.motion_detected = result.motion_detected
    analysis.suppression_reason = result.suppression_reason or result.skip_reason
    analysis.algorithm_version = result.algorithm_version
    analysis.analyzed_at = now

    if result.status == MotionDetectionStatus.SKIPPED:
        analysis.status = AnalysisStatus.SKIPPED
        return

    if result.status == MotionDetectionStatus.FAILED:
        analysis.status = AnalysisStatus.FAILED
        analysis.error_message = result.error_message
        return

    analysis.status = AnalysisStatus.COMPLETED
    image.motion_detected = result.motion_detected

    if not result.motion_detected:
        return

    event_result = await record_motion_detection(
        session=session,
        image=image,
        analysis=analysis,
        cooldown_seconds=camera.motion_cooldown_seconds,
    )

    if event_result.created:
        await send_motion_event_alert(
            session=session,
            event=event_result.event,
            image=image,
            camera_display_name=camera.display_name,
            camera_slug=camera.slug,
            sender=sender,
            admin_user_id=admin_user_id,
        )


async def _load_previous_image(
    *,
    session: AsyncSession,
    image: Image,
) -> Image | None:
    return await session.scalar(
        select(Image)
        .where(Image.camera_id == image.camera_id)
        .where(Image.id != image.id)
        .where(Image.capture_source == CaptureSource.SCHEDULED)
        .where(Image.storage_state == ImageStorageState.STORED)
        .where(Image.deleted_at.is_(None))
        .where(Image.captured_at_utc < image.captured_at_utc)
        .order_by(Image.captured_at_utc.desc())
        .limit(1)
    )


def _mark_skipped(
    analysis: MotionAnalysis,
    *,
    reason: str,
    now: datetime,
) -> None:
    analysis.status = AnalysisStatus.SKIPPED
    analysis.motion_detected = None
    analysis.suppression_reason = reason
    analysis.analyzed_at = now


def _decimal_or_none(
    value: float | None,
    *,
    quantum: Decimal,
) -> Decimal | None:
    if value is None:
        return None

    return Decimal(str(value)).quantize(quantum)
