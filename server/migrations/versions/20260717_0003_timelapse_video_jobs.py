"""Add daily time-lapse video jobs.

Revision ID: 20260717_0003
Revises: 20260716_0002
Create Date: 2026-07-17 12:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260717_0003"
down_revision: str | None = "20260716_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timelapse_video_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("local_date_jakarta", sa.Date(), nullable=False),
        sa.Column("start_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "processing",
                "uploading",
                "completed",
                "failed",
                "cancelled",
                name="job_status",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("image_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.Text()),
        sa.Column("file_size_bytes", sa.BigInteger()),
        sa.Column("sha256", sa.CHAR(64)),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("file_deleted_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.Text()),
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
    op.create_index(
        "idx_timelapse_video_jobs_pending",
        "timelapse_video_jobs",
        ["status", "created_at"],
        postgresql_where=sa.text(
            "status IN ('pending', 'processing', 'uploading')"
        ),
    )

    op.create_table(
        "timelapse_video_deliveries",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("timelapse_video_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("telegram_chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.Text()),
        sa.CheckConstraint(
            "status IN ('pending', 'sent')",
            name="ck_timelapse_video_deliveries_status",
        ),
    )
    op.create_index(
        "idx_timelapse_video_deliveries_pending",
        "timelapse_video_deliveries",
        ["status", "job_id"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "timelapse_video_job_images",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("timelapse_video_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "image_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "job_id",
            "ordinal",
            name="uq_timelapse_video_job_images_ordinal",
        ),
    )
    op.create_index(
        "idx_timelapse_video_job_images_image",
        "timelapse_video_job_images",
        ["image_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_timelapse_video_job_images_image",
        table_name="timelapse_video_job_images",
    )
    op.drop_table("timelapse_video_job_images")
    op.drop_index(
        "idx_timelapse_video_deliveries_pending",
        table_name="timelapse_video_deliveries",
    )
    op.drop_table("timelapse_video_deliveries")
    op.drop_index(
        "idx_timelapse_video_jobs_pending",
        table_name="timelapse_video_jobs",
    )
    op.drop_table("timelapse_video_jobs")
