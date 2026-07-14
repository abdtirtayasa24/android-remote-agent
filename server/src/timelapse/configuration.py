from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "production"
    log_level: str = "INFO"

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "timelapse"
    postgres_user: str = "timelapse"
    postgres_password: SecretStr

    storage_root: Path = Path("/srv/timelapse")
    public_domain: str | None = None

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def images_directory(self) -> Path:
        return self.storage_root / "images"

    @property
    def exports_directory(self) -> Path:
        return self.storage_root / "exports"

    @property
    def quarantine_directory(self) -> Path:
        return self.storage_root / "quarantine"

    @property
    def temporary_directory(self) -> Path:
        return self.storage_root / "tmp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
