from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_serializer


class ImageUploadResponse(BaseModel):
    capture_id: UUID
    status: Literal["stored", "already_stored"]
    received_at_utc: datetime

    @field_serializer("received_at_utc")
    def serialize_received_at_utc(
        self,
        value: datetime,
    ) -> str:
        return value.isoformat().replace("+00:00", "Z")
