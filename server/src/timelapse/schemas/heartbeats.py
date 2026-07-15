from __future__ import annotations

from datetime import UTC, datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)

UTC = UTC


class CameraHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_version: str = Field(
        min_length=1,
        max_length=64,
    )
    sent_at_utc: datetime
    uptime_seconds: int | None = Field(
        default=None,
        ge=0,
    )
    battery_percent: int | None = Field(
        default=None,
        ge=0,
        le=100,
    )
    battery_status: str | None = Field(
        default=None,
        max_length=32,
    )
    battery_temperature_c: float | None = Field(
        default=None,
        ge=-30,
        le=100,
    )
    available_storage_bytes: int | None = Field(
        default=None,
        ge=0,
    )
    pending_image_count: int | None = Field(
        default=None,
        ge=0,
    )
    pending_image_bytes: int | None = Field(
        default=None,
        ge=0,
    )
    oldest_pending_at_utc: datetime | None = None
    last_capture_at_utc: datetime | None = None
    last_upload_at_utc: datetime | None = None
    dropped_image_count: int = Field(
        default=0,
        ge=0,
    )
    consecutive_capture_failures: int = Field(
        default=0,
        ge=0,
    )
    last_error_code: str | None = Field(
        default=None,
        max_length=128,
    )

    @field_validator(
        "sent_at_utc",
        "oldest_pending_at_utc",
        "last_capture_at_utc",
        "last_upload_at_utc",
    )
    @classmethod
    def normalize_timestamp(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a UTC offset")

        return value.astimezone(UTC)


class CameraHeartbeatResponse(BaseModel):
    server_time_utc: datetime
    camera_enabled: bool
    configuration_version: int

    @field_serializer("server_time_utc")
    def serialize_server_time(
        self,
        value: datetime,
    ) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
