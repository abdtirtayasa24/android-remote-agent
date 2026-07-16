from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

import sqlalchemy as sa

EnumType = TypeVar("EnumType", bound=StrEnum)


class CameraHealthState(StrEnum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DISABLED = "disabled"


class CaptureSource(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    MOTION = "motion"


class ImageStorageState(StrEnum):
    STAGING = "staging"
    STORED = "stored"
    MISSING = "missing"
    DELETING = "deleting"


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CameraCommandStatus(StrEnum):
    PREPARING = "preparing"
    PENDING = "pending"
    CLAIMED = "claimed"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class CameraCommandType(StrEnum):
    PLAY_AUDIO = "play_audio"


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


def enum_values(enum_class: type[EnumType]) -> list[str]:
    return [member.value for member in enum_class]


CAMERA_HEALTH_STATE_DB = sa.Enum(
    CameraHealthState,
    name="camera_health_state",
    values_callable=enum_values,
)

CAPTURE_SOURCE_DB = sa.Enum(
    CaptureSource,
    name="capture_source",
    values_callable=enum_values,
)

IMAGE_STORAGE_STATE_DB = sa.Enum(
    ImageStorageState,
    name="image_storage_state",
    values_callable=enum_values,
)

JOB_STATUS_DB = sa.Enum(
    JobStatus,
    name="job_status",
    values_callable=enum_values,
)

CAMERA_COMMAND_STATUS_DB = sa.Enum(
    CameraCommandStatus,
    name="camera_command_status",
    values_callable=enum_values,
)

CAMERA_COMMAND_TYPE_DB = sa.Enum(
    CameraCommandType,
    name="camera_command_type",
    values_callable=enum_values,
)

ANALYSIS_STATUS_DB = sa.Enum(
    AnalysisStatus,
    name="analysis_status",
    values_callable=enum_values,
)
