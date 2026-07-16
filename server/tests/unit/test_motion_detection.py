from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from timelapse.services.motion_detection import (
    FRAME_DIFF_V1,
    MotionDetectionConfig,
    MotionDetectionStatus,
    detect_motion,
)

CONFIG = MotionDetectionConfig(
    pixel_threshold=25,
    changed_ratio_threshold=0.02,
    region_ratio_threshold=0.005,
)


def write_image(path: Path, image: np.ndarray) -> None:
    assert cv2.imwrite(str(path), image)


def test_frame_diff_v1_static_scene_returns_no_motion(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.jpg"
    current_path = tmp_path / "current.jpg"
    image = np.full((180, 320, 3), 40, dtype=np.uint8)
    write_image(previous_path, image)
    write_image(current_path, image)

    result = detect_motion(
        previous_image_path=previous_path,
        current_image_path=current_path,
        config=CONFIG,
    )

    assert result.status == MotionDetectionStatus.COMPLETED
    assert result.algorithm_version == FRAME_DIFF_V1
    assert result.motion_detected is False
    assert result.suppression_reason is None
    assert result.changed_pixel_ratio == 0
    assert result.largest_region_ratio == 0


def test_frame_diff_v1_controlled_movement_returns_motion(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.jpg"
    current_path = tmp_path / "current.jpg"
    previous = np.zeros((180, 320, 3), dtype=np.uint8)
    current = previous.copy()
    current[50:110, 120:180] = 255
    write_image(previous_path, previous)
    write_image(current_path, current)

    result = detect_motion(
        previous_image_path=previous_path,
        current_image_path=current_path,
        config=CONFIG,
    )

    assert result.status == MotionDetectionStatus.COMPLETED
    assert result.motion_detected is True
    assert result.changed_pixel_ratio >= CONFIG.changed_ratio_threshold
    assert result.largest_region_ratio >= CONFIG.region_ratio_threshold
    assert result.suppression_reason is None


def test_frame_diff_v1_suppresses_large_brightness_shift(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.jpg"
    current_path = tmp_path / "current.jpg"
    previous = np.full((180, 320, 3), 40, dtype=np.uint8)
    current = np.full((180, 320, 3), 90, dtype=np.uint8)
    write_image(previous_path, previous)
    write_image(current_path, current)

    result = detect_motion(
        previous_image_path=previous_path,
        current_image_path=current_path,
        config=CONFIG,
    )

    assert result.status == MotionDetectionStatus.COMPLETED
    assert result.motion_detected is False
    assert result.suppression_reason == "lighting_change"
    assert result.changed_pixel_ratio > 0.50
    assert result.brightness_delta >= 20


def test_frame_diff_v1_missing_input_returns_structured_skip(tmp_path: Path) -> None:
    current_path = tmp_path / "current.jpg"
    write_image(current_path, np.zeros((180, 320, 3), dtype=np.uint8))

    result = detect_motion(
        previous_image_path=tmp_path / "missing.jpg",
        current_image_path=current_path,
        config=CONFIG,
    )

    assert result.status == MotionDetectionStatus.SKIPPED
    assert result.motion_detected is None
    assert result.skip_reason == "previous_image_missing"
    assert result.changed_pixel_ratio is None


def test_frame_diff_v1_invalid_input_returns_structured_skip(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.jpg"
    current_path = tmp_path / "current.jpg"
    previous_path.write_text("not an image", encoding="utf-8")
    write_image(current_path, np.zeros((180, 320, 3), dtype=np.uint8))

    result = detect_motion(
        previous_image_path=previous_path,
        current_image_path=current_path,
        config=CONFIG,
    )

    assert result.status == MotionDetectionStatus.SKIPPED
    assert result.motion_detected is None
    assert result.skip_reason == "previous_image_decode_failed"
