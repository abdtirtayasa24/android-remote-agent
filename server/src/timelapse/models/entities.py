from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from timelapse.models.base import Base
from timelapse.models.enums import (
    ANALYSIS_STATUS_DB,
    CAMERA_HEALTH_STATE_DB,
    CAPTURE_SOURCE_DB,
    IMAGE_STORAGE_STATE_DB,
    JOB_STATUS_DB,
    AnalysisStatus,
    CameraHealthState,
    CaptureSource,
    ImageStorageState,
    JobStatus,
)


def uuid_primary_key() -> sa.Column[UUID]:
    return mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        sa.CheckConstraint(
            "capture_interval_seconds >= 30",
            name="ck_cameras_capture_interval",
        ),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 365",
            name="ck_cameras_retention_days",
        ),
        sa.CheckConstraint(
            "jpeg_quality BETWEEN 1 AND 100",
            name="ck_cameras_jpeg_quality",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.true(),
    )

    capture_interval_seconds: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="60",
    )
    retention_days: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="7",
    )
    jpeg_quality: Mapped[int] = mapped_column(
        sa.SmallInteger,
        nullable=False,
        server_default="72",
    )
    maximum_width: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="1280",
    )
    maximum_height: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="720",
    )

    motion_enabled: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.true(),
    )
    motion_pixel_threshold: Mapped[int] = mapped_column(
        sa.SmallInteger,
        nullable=False,
        server_default="25",
    )
    motion_changed_ratio: Mapped[Decimal] = mapped_column(
        sa.Numeric(7, 6),
        nullable=False,
        server_default="0.020000",
    )
    motion_region_ratio: Mapped[Decimal] = mapped_column(
        sa.Numeric(7, 6),
        nullable=False,
        server_default="0.005000",
    )
    motion_cooldown_seconds: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="300",
    )

    health_state: Mapped[CameraHealthState] = mapped_column(
        CAMERA_HEALTH_STATE_DB,
        nullable=False,
        server_default=CameraHealthState.OFFLINE.value,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_capture_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_upload_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    configuration_version: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="1",
    )

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class CameraCredential(Base):
    __tablename__ = "camera_credentials"

    id: Mapped[UUID] = uuid_primary_key()
    camera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_id: Mapped[str] = mapped_column(sa.String(16), nullable=False, unique=True)
    secret_digest: Mapped[bytes] = mapped_column(sa.LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Image(Base):
    __tablename__ = "images"
    __table_args__ = (
        sa.CheckConstraint(
            "file_size_bytes > 0",
            name="ck_images_file_size",
        ),
        sa.CheckConstraint(
            "width_pixels > 0",
            name="ck_images_width",
        ),
        sa.CheckConstraint(
            "height_pixels > 0",
            name="ck_images_height",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    capture_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        unique=True,
    )
    camera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("cameras.id", ondelete="RESTRICT"),
        nullable=False,
    )
    captured_at_utc: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    received_at_utc: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    capture_source: Mapped[CaptureSource] = mapped_column(
        CAPTURE_SOURCE_DB,
        nullable=False,
    )
    storage_state: Mapped[ImageStorageState] = mapped_column(
        IMAGE_STORAGE_STATE_DB,
        nullable=False,
        server_default=ImageStorageState.STAGING.value,
    )
    storage_path: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default="image/jpeg",
    )
    file_size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    width_pixels: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    height_pixels: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    motion_detected: Mapped[bool | None] = mapped_column(sa.Boolean)
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class MotionAnalysis(Base):
    __tablename__ = "motion_analyses"

    image_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("images.id", ondelete="CASCADE"),
        primary_key=True,
    )
    previous_image_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("images.id", ondelete="SET NULL"),
    )
    status: Mapped[AnalysisStatus] = mapped_column(
        ANALYSIS_STATUS_DB,
        nullable=False,
        server_default=AnalysisStatus.PENDING.value,
    )
    changed_pixel_ratio: Mapped[Decimal | None] = mapped_column(sa.Numeric(7, 6))
    largest_region_ratio: Mapped[Decimal | None] = mapped_column(sa.Numeric(7, 6))
    brightness_delta: Mapped[Decimal | None] = mapped_column(sa.Numeric(8, 3))
    motion_detected: Mapped[bool | None] = mapped_column(sa.Boolean)
    suppression_reason: Mapped[str | None] = mapped_column(sa.Text)
    algorithm_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    analyzed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(sa.Text)


class MotionEvent(Base):
    __tablename__ = "motion_events"
    __table_args__ = (
        sa.CheckConstraint(
            "alert_status IN ('pending', 'sent', 'failed', 'suppressed')",
            name="ck_motion_events_alert_status",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    camera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at_utc: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    last_detected_at_utc: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    ended_at_utc: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    peak_change_ratio: Mapped[Decimal] = mapped_column(sa.Numeric(7, 6), nullable=False)
    representative_image_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("images.id", ondelete="RESTRICT"),
        nullable=False,
    )
    alert_status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default="pending",
    )
    telegram_message_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class MotionEventImage(Base):
    __tablename__ = "motion_event_images"

    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("motion_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    image_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("images.id", ondelete="CASCADE"),
        primary_key=True,
    )
    detected_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )


class CameraHeartbeat(Base):
    __tablename__ = "camera_heartbeats"
    __table_args__ = (
        sa.CheckConstraint(
            "battery_percent BETWEEN 0 AND 100",
            name="ck_camera_heartbeats_battery_percent",
        ),
    )

    id: Mapped[int] = mapped_column(
        sa.BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    camera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    received_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    device_sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    agent_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    uptime_seconds: Mapped[int | None] = mapped_column(sa.BigInteger)
    battery_percent: Mapped[int | None] = mapped_column(sa.SmallInteger)
    battery_status: Mapped[str | None] = mapped_column(sa.Text)
    battery_temperature_c: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    available_storage_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    pending_image_count: Mapped[int | None] = mapped_column(sa.Integer)
    pending_image_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    oldest_pending_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_capture_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_upload_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    dropped_image_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="0",
    )
    consecutive_capture_failures: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="0",
    )
    last_error_code: Mapped[str | None] = mapped_column(sa.Text)


class HeartbeatDailySummary(Base):
    __tablename__ = "heartbeat_daily_summaries"
    __table_args__ = (
        sa.UniqueConstraint(
            "camera_id",
            "summary_date_utc",
            name="uq_heartbeat_daily_summaries_camera_date",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    camera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    summary_date_utc: Mapped[date] = mapped_column(sa.Date, nullable=False)
    heartbeat_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="0",
    )
    minimum_battery_percent: Mapped[int | None] = mapped_column(sa.SmallInteger)
    maximum_temperature_c: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 2))
    maximum_pending_image_count: Mapped[int | None] = mapped_column(sa.Integer)
    maximum_pending_image_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    offline_seconds: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class AlertState(Base):
    __tablename__ = "alert_states"
    __table_args__ = (
        sa.UniqueConstraint(
            "camera_id",
            "alert_type",
            "condition_code",
            name="uq_alert_states_camera_condition",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    camera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    alert_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    condition_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.false(),
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_telegram_message_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class TelegramPrincipal(Base):
    __tablename__ = "telegram_principals"
    __table_args__ = (
        sa.UniqueConstraint(
            "telegram_user_id",
            "telegram_chat_id",
            name="uq_telegram_principals_user_chat",
        ),
        sa.CheckConstraint(
            "role IN ('viewer', 'administrator')",
            name="ck_telegram_principals_role",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    telegram_user_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    role: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default="viewer",
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        server_default=sa.true(),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class TimelapseVideoJob(Base):
    __tablename__ = "timelapse_video_jobs"
    __table_args__ = (
        sa.UniqueConstraint(
            "camera_id",
            "local_date_jakarta",
            name="uq_timelapse_video_jobs_camera_date",
        ),
        sa.CheckConstraint(
            "end_at_utc > start_at_utc",
            name="ck_timelapse_video_jobs_order",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    camera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    local_date_jakarta: Mapped[date] = mapped_column(sa.Date, nullable=False)
    start_at_utc: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    end_at_utc: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        JOB_STATUS_DB,
        nullable=False,
        server_default=JobStatus.PENDING.value,
    )
    image_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="0",
    )
    storage_path: Mapped[str | None] = mapped_column(sa.Text)
    file_size_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    sha256: Mapped[str | None] = mapped_column(sa.CHAR(64))
    telegram_message_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    claimed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    file_deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(sa.Text)


class TimelapseVideoDelivery(Base):
    __tablename__ = "timelapse_video_deliveries"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending', 'sent')",
            name="ck_timelapse_video_deliveries_status",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("timelapse_video_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    telegram_chat_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default="pending",
    )
    telegram_message_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(sa.Text)


class TimelapseVideoJobImage(Base):
    __tablename__ = "timelapse_video_job_images"
    __table_args__ = (
        sa.UniqueConstraint(
            "job_id",
            "ordinal",
            name="uq_timelapse_video_job_images_ordinal",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("timelapse_video_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    image_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("images.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(sa.Integer, nullable=False)


class ExportJob(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (
        sa.CheckConstraint(
            "end_at_utc > start_at_utc",
            name="ck_export_jobs_order",
        ),
        sa.CheckConstraint(
            "end_at_utc <= start_at_utc + INTERVAL '24 hours'",
            name="ck_export_jobs_maximum_range",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    requested_by_user_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    destination_chat_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    camera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("cameras.id"),
        nullable=False,
    )
    start_at_utc: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    end_at_utc: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        JOB_STATUS_DB,
        nullable=False,
        server_default=JobStatus.PENDING.value,
    )
    matching_image_count: Mapped[int | None] = mapped_column(sa.Integer)
    completed_part_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default="0",
    )
    claimed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now() + INTERVAL '6 hours'"),
    )


class ExportJobImage(Base):
    __tablename__ = "export_job_images"
    __table_args__ = (
        sa.UniqueConstraint(
            "export_job_id",
            "ordinal",
            name="uq_export_job_images_ordinal",
        ),
    )

    export_job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("export_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    image_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("images.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(sa.Integer, nullable=False)


class ExportPart(Base):
    __tablename__ = "export_parts"
    __table_args__ = (
        sa.UniqueConstraint(
            "export_job_id",
            "part_number",
            name="uq_export_parts_job_part",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'uploading', 'sent', 'failed', 'deleted')",
            name="ck_export_parts_status",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    export_job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("export_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    part_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default="created",
    )
    telegram_message_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(
        sa.BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    camera_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("cameras.id", ondelete="SET NULL"),
    )
    telegram_user_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    telegram_chat_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    remote_ip: Mapped[str | None] = mapped_column(INET)
    outcome: Mapped[str] = mapped_column(sa.Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )


sa.Index(
    "idx_camera_credentials_active",
    CameraCredential.token_id,
    postgresql_where=CameraCredential.revoked_at.is_(None),
)

sa.Index(
    "idx_images_camera_capture_time",
    Image.camera_id,
    Image.captured_at_utc.desc(),
    postgresql_where=sa.text("deleted_at IS NULL AND storage_state = 'stored'"),
)

sa.Index(
    "idx_images_retention",
    Image.captured_at_utc,
    postgresql_where=sa.text("deleted_at IS NULL AND storage_state = 'stored'"),
)

sa.Index(
    "idx_motion_analyses_pending",
    MotionAnalysis.status,
    MotionAnalysis.image_id,
    postgresql_where=sa.text("status IN ('pending', 'processing')"),
)

sa.Index(
    "idx_camera_heartbeats_camera_received",
    CameraHeartbeat.camera_id,
    CameraHeartbeat.received_at.desc(),
)

sa.Index(
    "idx_heartbeat_daily_summaries_camera_date",
    HeartbeatDailySummary.camera_id,
    HeartbeatDailySummary.summary_date_utc.desc(),
)

sa.Index(
    "idx_alert_states_active",
    AlertState.camera_id,
    AlertState.alert_type,
    postgresql_where=AlertState.is_active.is_(True),
)

sa.Index(
    "idx_timelapse_video_deliveries_pending",
    TimelapseVideoDelivery.status,
    TimelapseVideoDelivery.job_id,
    postgresql_where=sa.text("status = 'pending'"),
)

sa.Index(
    "idx_timelapse_video_job_images_image",
    TimelapseVideoJobImage.image_id,
)

sa.Index(
    "idx_timelapse_video_jobs_pending",
    TimelapseVideoJob.status,
    TimelapseVideoJob.created_at,
    postgresql_where=sa.text("status IN ('pending', 'processing', 'uploading')"),
)
