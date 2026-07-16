from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection
from timelapse.database import get_engine

MIGRATION_DIRECTORY = Path(__file__).resolve().parents[2] / "migrations" / "versions"
MIGRATION_PATHS = (
    MIGRATION_DIRECTORY / "20260714_0001_initial_schema.py",
    MIGRATION_DIRECTORY / "20260716_0002_health_alerts.py",
    MIGRATION_DIRECTORY / "20260717_0003_timelapse_video_jobs.py",
    MIGRATION_DIRECTORY / "20260718_0004_camera_commands.py",
)


def load_migration(path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(path.stem, path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def run_migration_chain(connection: Connection, schema_name: str) -> None:
    connection.execute(sa.text(f'SET LOCAL search_path TO "{schema_name}"'))
    context = MigrationContext.configure(connection)
    migrations = [load_migration(path) for path in MIGRATION_PATHS]

    with Operations.context(context):
        for migration in migrations:
            migration.upgrade()

        tables = set(sa.inspect(connection).get_table_names())
        assert "timelapse_video_jobs" in tables
        assert "timelapse_video_deliveries" in tables
        assert "timelapse_video_job_images" in tables
        assert "camera_commands" in tables

        for migration in reversed(migrations):
            migration.downgrade()

        assert sa.inspect(connection).get_table_names() == []


async def test_complete_migration_chain_upgrades_and_downgrades() -> None:
    schema_name = f"migration_test_{uuid4().hex}"
    engine = get_engine()

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(sa.text(f'CREATE SCHEMA "{schema_name}"'))
            await connection.run_sync(run_migration_chain, schema_name)
        finally:
            await transaction.rollback()
