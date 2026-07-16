from __future__ import annotations

import ssl
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal[
        "development",
        "test",
        "production",
    ] = "production"

    log_level: str = "INFO"

    # Use Neon's pooled URL for normal application traffic.
    database_url: SecretStr

    # Use Neon's direct, non-pooled URL for Alembic.
    database_migration_url: SecretStr

    database_pool_size: int = Field(
        default=3,
        ge=1,
        le=20,
    )
    database_max_overflow: int = Field(
        default=2,
        ge=0,
        le=20,
    )
    database_connect_timeout_seconds: int = Field(
        default=15,
        ge=1,
        le=60,
    )

    camera_token_pepper: SecretStr
    require_https: bool = True

    storage_root: Path = Path("/srv/timelapse")
    storage_hard_min_free_bytes: int = Field(default=512 * 1024 * 1024, ge=0)
    storage_severe_min_free_bytes: int = Field(default=1024 * 1024 * 1024, ge=0)
    public_domain: str | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_admin_user_id: int | None = None
    health_worker_interval_seconds: int = Field(default=60, ge=10, le=3600)
    heartbeat_aggregation_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    motion_worker_interval_seconds: int = Field(default=30, ge=5, le=3600)
    daily_timelapse_enabled: bool = True
    daily_timelapse_worker_interval_seconds: int = Field(default=60, ge=10, le=3600)
    daily_timelapse_send_hour_jakarta: int = Field(default=0, ge=0, le=23)
    daily_timelapse_send_minute_jakarta: int = Field(default=10, ge=0, le=59)
    daily_timelapse_frame_rate: int = Field(default=24, ge=1, le=60)
    retention_worker_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    reconciliation_worker_interval_seconds: int = Field(default=3600, ge=60, le=86400)

    @field_validator(
        "database_url",
        "database_migration_url",
    )
    @classmethod
    def validate_database_url(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        parsed = make_url(value.get_secret_value())

        if parsed.get_backend_name() != "postgresql":
            raise ValueError("database URL must use PostgreSQL")

        if not parsed.host:
            raise ValueError("database URL must include a host")

        if not parsed.database:
            raise ValueError("database URL must include a database name")

        return value

    @field_validator("camera_token_pepper")
    @classmethod
    def validate_camera_token_pepper(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("CAMERA_TOKEN_PEPPER must contain at least 32 characters")

        return value

    @property
    def runtime_database_url(self) -> URL:
        return _build_asyncpg_url(self.database_url.get_secret_value())

    @property
    def migration_database_url(self) -> URL:
        return _build_asyncpg_url(self.database_migration_url.get_secret_value())

    @property
    def database_connect_args(self) -> dict[str, object]:
        return {
            "ssl": ssl.create_default_context(),
            "timeout": self.database_connect_timeout_seconds,
        }

    @property
    def images_directory(self) -> Path:
        return self.storage_root / "images"

    @property
    def exports_directory(self) -> Path:
        return self.storage_root / "exports"

    @property
    def timelapses_directory(self) -> Path:
        return self.storage_root / "timelapses"

    @property
    def quarantine_directory(self) -> Path:
        return self.storage_root / "quarantine"

    @property
    def temporary_directory(self) -> Path:
        return self.storage_root / "tmp"


def _build_asyncpg_url(value: str) -> URL:
    parsed = make_url(value)
    parsed = parsed.set(drivername="postgresql+asyncpg")

    # TLS is configured through asyncpg connect_args below.
    #
    # channel_binding is supported by some libpq-based drivers,
    # but is not an asyncpg connection argument. Removing these
    # URL options avoids them being interpreted as PostgreSQL
    # session settings.
    return parsed.difference_update_query(
        {
            "sslmode",
            "channel_binding",
        }
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
