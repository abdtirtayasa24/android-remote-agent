"""Add health alert state and heartbeat summaries.

Revision ID: 20260716_0002
Revises: 20260714_0001
Create Date: 2026-07-16 12:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0002"
down_revision: str | None = "20260714_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "heartbeat_daily_summaries",
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
        sa.Column("summary_date_utc", sa.Date(), nullable=False),
        sa.Column("heartbeat_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minimum_battery_percent", sa.SmallInteger()),
        sa.Column("maximum_temperature_c", sa.Numeric(5, 2)),
        sa.Column("maximum_pending_image_count", sa.Integer()),
        sa.Column("maximum_pending_image_bytes", sa.BigInteger()),
        sa.Column("offline_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "camera_id",
            "summary_date_utc",
            name="uq_heartbeat_daily_summaries_camera_date",
        ),
    )
    op.create_index(
        "idx_heartbeat_daily_summaries_camera_date",
        "heartbeat_daily_summaries",
        ["camera_id", "summary_date_utc"],
    )

    op.create_table(
        "alert_states",
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
        sa.Column("alert_type", sa.Text(), nullable=False),
        sa.Column("condition_code", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_observed_at", sa.DateTime(timezone=True)),
        sa.Column("last_sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_resolved_at", sa.DateTime(timezone=True)),
        sa.Column("last_telegram_message_id", sa.BigInteger()),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "camera_id",
            "alert_type",
            "condition_code",
            name="uq_alert_states_camera_condition",
        ),
    )
    op.create_index(
        "idx_alert_states_active",
        "alert_states",
        ["camera_id", "alert_type"],
        postgresql_where=sa.text("is_active IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("idx_alert_states_active", table_name="alert_states")
    op.drop_table("alert_states")
    op.drop_index(
        "idx_heartbeat_daily_summaries_camera_date",
        table_name="heartbeat_daily_summaries",
    )
    op.drop_table("heartbeat_daily_summaries")
