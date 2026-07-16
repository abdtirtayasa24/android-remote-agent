from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

FRAME_DIFF_V1 = "frame-diff-v1"
LIGHTING_CHANGE = "lighting_change"


class MotionDetectionStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class MotionDetectionConfig:
    pixel_threshold: int = 25
    changed_ratio_threshold: float = 0.02
    region_ratio_threshold: float = 0.005
    lighting_changed_ratio_threshold: float = 0.50
    lighting_brightness_delta_threshold: float = 20.0
    resize_width: int = 320
    resize_height: int = 180


@dataclass(frozen=True)
class MotionDetectionResult:
    status: MotionDetectionStatus
    algorithm_version: str
    motion_detected: bool | None
    changed_pixel_ratio: float | None
    largest_region_ratio: float | None
    brightness_delta: float | None
    suppression_reason: str | None = None
    skip_reason: str | None = None
    error_message: str | None = None


def detect_motion(
    *,
    previous_image_path: Path,
    current_image_path: Path,
    config: MotionDetectionConfig,
) -> MotionDetectionResult:
    previous_image = _read_image(previous_image_path, label="previous")

    if isinstance(previous_image, MotionDetectionResult):
        return previous_image

    current_image = _read_image(current_image_path, label="current")

    if isinstance(current_image, MotionDetectionResult):
        return current_image

    previous_gray = _normalize_image(previous_image, config=config)
    current_gray = _normalize_image(current_image, config=config)
    previous_blurred = cv2.GaussianBlur(previous_gray, (5, 5), 0)
    current_blurred = cv2.GaussianBlur(current_gray, (5, 5), 0)
    difference = cv2.absdiff(previous_blurred, current_blurred)
    _, thresholded = cv2.threshold(
        difference,
        config.pixel_threshold,
        255,
        cv2.THRESH_BINARY,
    )
    kernel = np.ones((3, 3), dtype=np.uint8)
    opened = cv2.morphologyEx(thresholded, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)

    total_pixels = config.resize_width * config.resize_height
    changed_pixel_count = int(cv2.countNonZero(mask))
    changed_pixel_ratio = changed_pixel_count / total_pixels
    largest_region_ratio = _largest_region_ratio(mask, total_pixels=total_pixels)
    brightness_delta = abs(float(current_gray.mean()) - float(previous_gray.mean()))
    suppression_reason = _suppression_reason(
        changed_pixel_ratio=changed_pixel_ratio,
        brightness_delta=brightness_delta,
        config=config,
    )
    motion_detected = (
        suppression_reason is None
        and changed_pixel_ratio >= config.changed_ratio_threshold
        and largest_region_ratio >= config.region_ratio_threshold
    )

    return MotionDetectionResult(
        status=MotionDetectionStatus.COMPLETED,
        algorithm_version=FRAME_DIFF_V1,
        motion_detected=motion_detected,
        changed_pixel_ratio=changed_pixel_ratio,
        largest_region_ratio=largest_region_ratio,
        brightness_delta=brightness_delta,
        suppression_reason=suppression_reason,
    )


def _read_image(
    path: Path,
    *,
    label: str,
) -> npt.NDArray[np.uint8] | MotionDetectionResult:
    if not path.exists():
        return _skipped(f"{label}_image_missing")

    image = cv2.imread(str(path), cv2.IMREAD_COLOR)

    if image is None:
        return _skipped(f"{label}_image_decode_failed")

    return image


def _normalize_image(
    image: npt.NDArray[np.uint8],
    *,
    config: MotionDetectionConfig,
) -> npt.NDArray[np.uint8]:
    resized = cv2.resize(
        image,
        (config.resize_width, config.resize_height),
        interpolation=cv2.INTER_AREA,
    )
    return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)


def _largest_region_ratio(
    mask: npt.NDArray[np.uint8],
    *,
    total_pixels: int,
) -> float:
    component_count, _, stats, _ = cv2.connectedComponentsWithStats(
        mask,
        connectivity=8,
    )

    if component_count <= 1:
        return 0.0

    largest_region_pixels = int(stats[1:, cv2.CC_STAT_AREA].max())
    return largest_region_pixels / total_pixels


def _suppression_reason(
    *,
    changed_pixel_ratio: float,
    brightness_delta: float,
    config: MotionDetectionConfig,
) -> str | None:
    if (
        changed_pixel_ratio > config.lighting_changed_ratio_threshold
        and brightness_delta >= config.lighting_brightness_delta_threshold
    ):
        return LIGHTING_CHANGE

    return None


def _skipped(reason: str) -> MotionDetectionResult:
    return MotionDetectionResult(
        status=MotionDetectionStatus.SKIPPED,
        algorithm_version=FRAME_DIFF_V1,
        motion_detected=None,
        changed_pixel_ratio=None,
        largest_region_ratio=None,
        brightness_delta=None,
        skip_reason=reason,
    )
