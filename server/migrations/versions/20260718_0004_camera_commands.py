"""Add camera command queue and voice playback preference.

Revision ID: 20260718_0004
Revises: 20260717_0003
Create Date: 2026-07-18 12:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0004"
down_revision: str | None = "20260717_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

command_status = postgresql.ENUM(
    "preparing",
    "pending",
    "claimed",
    "started",
    "completed",
    "failed",
    "expired",
    name="camera_command_status",
)
command_type = postgresql.ENUM(
    "play_audio",
    name="camera_command_type",
)


def upgrade() -> None:
    command_status.create(op.get_bind(), checkfirst=True)
    command_type.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "telegram_principals",
        sa.Column("voice_playback_camera_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_telegram_principals_voice_playback_camera",
        "telegram_principals",
        "cameras",
        ["voice_playback_camera_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "camera_commands",
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
        sa.Column(
            "command_type",
            postgresql.ENUM(
                "play_audio",
                name="camera_command_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "preparing",
                "pending",
                "claimed",
                "started",
                "completed",
                "failed",
                "expired",
                name="camera_command_status",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("media_storage_path", sa.Text()),
        sa.Column("media_mime_type", sa.Text()),
        sa.Column("media_size_bytes", sa.BigInteger()),
        sa.Column("media_sha256", sa.CHAR(64)),
        sa.Column("requested_by_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_in_telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_camera_commands_pending",
        "camera_commands",
        ["camera_id", "status", "created_at"],
        postgresql_where=sa.text("status IN ('pending', 'claimed', 'started')"),
    )


def downgrade() -> None:
    op.drop_index("idx_camera_commands_pending", table_name="camera_commands")
    op.drop_table("camera_commands")
    op.drop_constraint(
        "fk_telegram_principals_voice_playback_camera",
        "telegram_principals",
        type_="foreignkey",
    )
    op.drop_column("telegram_principals", "voice_playback_camera_id")
    command_type.drop(op.get_bind(), checkfirst=True)
    command_status.drop(op.get_bind(), checkfirst=True)
