"""Create initial time-lapse schema.

Revision ID: 20260714_0001
Revises:
Create Date: 2026-07-14 16:03:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL_DIRECTORY = Path(__file__).resolve().parents[1] / "sql"


def execute_sql_file(filename: str) -> None:
    sql = (SQL_DIRECTORY / filename).read_text(encoding="utf-8")

    # These migration files contain only controlled DDL statements and no
    # procedural function bodies. Executing one statement at a time remains
    # compatible with asyncpg, which rejects multi-command prepared statements.
    for statement in sql.split(";\n"):
        stripped_statement = statement.strip()
        if stripped_statement:
            op.execute(sa.text(stripped_statement))


def upgrade() -> None:
    execute_sql_file("20260714_0001_initial_up.sql")


def downgrade() -> None:
    execute_sql_file("20260714_0001_initial_down.sql")
