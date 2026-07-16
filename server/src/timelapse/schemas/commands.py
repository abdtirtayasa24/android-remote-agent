from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from timelapse.models.enums import CameraCommandStatus, CameraCommandType


class CameraCommandResponse(BaseModel):
    id: UUID
    command_type: CameraCommandType
    media_size_bytes: int
    media_sha256: str
    media_mime_type: str
    duration_seconds: int = Field(ge=0, le=300)
    expires_at_utc: datetime


class CameraCommandResultRequest(BaseModel):
    status: Literal[
        CameraCommandStatus.STARTED,
        CameraCommandStatus.COMPLETED,
        CameraCommandStatus.FAILED,
    ]
    error_code: str | None = Field(default=None, max_length=128, pattern=r"^[a-z0-9_]+$")

    @model_validator(mode="after")
    def validate_error_code(self) -> CameraCommandResultRequest:
        if self.status == CameraCommandStatus.FAILED and self.error_code is None:
            raise ValueError("error_code is required for failed commands")

        if self.status != CameraCommandStatus.FAILED and self.error_code is not None:
            raise ValueError("error_code is only valid for failed commands")

        return self


class CameraCommandResultResponse(BaseModel):
    id: UUID
    status: CameraCommandStatus
