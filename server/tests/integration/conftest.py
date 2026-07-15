from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from timelapse.configuration import get_settings
from timelapse.database import (
    close_database,
    get_engine,
    get_session_factory,
    session_scope,
)
from timelapse.models import Base
from timelapse.models.entities import Camera, CameraCredential
from timelapse.services.camera_credentials import (
    digest_camera_secret,
    generate_camera_credential,
)


@dataclass(frozen=True)
class CameraFixture:
    slug: str
    credential: str
    token_id: str


CameraFactory = Callable[..., Awaitable[CameraFixture]]


def reset_test_schema(connection: Connection) -> None:
    """
    Create missing model tables and remove data left by previous tests.

    This operates only against the explicitly configured test database.
    """
    Base.metadata.create_all(connection)

    identifier_preparer = connection.dialect.identifier_preparer

    table_names = [
        identifier_preparer.format_table(table)
        for table in reversed(Base.metadata.sorted_tables)
    ]

    if not table_names:
        return

    connection.execute(
        text("TRUNCATE TABLE " + ", ".join(table_names) + " RESTART IDENTITY CASCADE")
    )


def validate_test_database_url(
    *,
    variable_name: str,
    value: str,
) -> None:
    parsed_url = make_url(value)

    if parsed_url.get_backend_name() != "postgresql":
        raise RuntimeError(f"{variable_name} must use PostgreSQL")

    if not parsed_url.host:
        raise RuntimeError(f"{variable_name} must include a database host")

    if not parsed_url.database:
        raise RuntimeError(f"{variable_name} must include a database name")

    database_name = parsed_url.database
    destructive_access_allowed = os.getenv(
        "TEST_DATABASE_ALLOW_DESTRUCTIVE",
        "",
    ).lower() in {"1", "true", "yes"}

    if not database_name.endswith("_test") and not destructive_access_allowed:
        raise RuntimeError(
            f"{variable_name} points to database "
            f"{database_name!r}, which does not end in '_test'. "
            "For a dedicated Neon test branch whose database is "
            "named 'neondb', set "
            "TEST_DATABASE_ALLOW_DESTRUCTIVE=true explicitly."
        )


@pytest_asyncio.fixture(autouse=True)
async def integration_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[None]:
    runtime_database_url = os.getenv("TEST_DATABASE_URL")
    migration_database_url = os.getenv("TEST_DATABASE_MIGRATION_URL")

    if not runtime_database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    if not migration_database_url:
        migration_database_url = runtime_database_url

    validate_test_database_url(
        variable_name="TEST_DATABASE_URL",
        value=runtime_database_url,
    )
    validate_test_database_url(
        variable_name="TEST_DATABASE_MIGRATION_URL",
        value=migration_database_url,
    )

    # Close any engine cached by an earlier test configuration before
    # changing environment variables.
    await close_database()

    monkeypatch.setenv(
        "DATABASE_URL",
        runtime_database_url,
    )
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        migration_database_url,
    )
    monkeypatch.setenv(
        "DATABASE_POOL_SIZE",
        "2",
    )
    monkeypatch.setenv(
        "DATABASE_MAX_OVERFLOW",
        "0",
    )
    monkeypatch.setenv(
        "DATABASE_CONNECT_TIMEOUT_SECONDS",
        "15",
    )
    monkeypatch.setenv(
        "CAMERA_TOKEN_PEPPER",
        "integration-test-pepper-" * 3,
    )
    monkeypatch.setenv(
        "STORAGE_ROOT",
        str(tmp_path / "storage"),
    )
    monkeypatch.setenv(
        "REQUIRE_HTTPS",
        "true",
    )
    monkeypatch.setenv(
        "ENVIRONMENT",
        "test",
    )

    get_settings.cache_clear()
    get_session_factory.cache_clear()
    get_engine.cache_clear()

    settings = get_settings()

    # Schema operations use the direct database URL rather than Neon's
    # pooled endpoint.
    migration_engine = create_async_engine(
        settings.migration_database_url,
        connect_args=settings.database_connect_args,
        poolclass=NullPool,
    )

    try:
        async with migration_engine.begin() as connection:
            await connection.run_sync(reset_test_schema)

        yield
    finally:
        await migration_engine.dispose()
        await close_database()

        get_settings.cache_clear()
        get_session_factory.cache_clear()
        get_engine.cache_clear()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # Import after the environment fixture configures Settings.
    from timelapse.api.main import app

    transport = httpx.ASGITransport(
        app=app,
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def create_camera() -> CameraFactory:
    async def factory(
        *,
        slug: str,
        revoked: bool = False,
    ) -> CameraFixture:
        settings = get_settings()
        generated = generate_camera_credential()

        async with session_scope() as session:
            camera = Camera(
                slug=slug,
                display_name=slug.replace(
                    "-",
                    " ",
                ).title(),
            )
            session.add(camera)
            await session.flush()

            credential = CameraCredential(
                camera_id=camera.id,
                token_id=generated.token_id,
                secret_digest=digest_camera_secret(
                    secret=generated.secret,
                    pepper=(settings.camera_token_pepper.get_secret_value()),
                ),
            )

            if revoked:
                credential.revoked_at = datetime.now(timezone.utc)

            session.add(credential)

        return CameraFixture(
            slug=slug,
            credential=generated.plaintext,
            token_id=generated.token_id,
        )

    return factory
