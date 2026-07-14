from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from camera_agent.configuration import AgentConfig, ConfigurationError

_FILENAME_PATTERN = re.compile(
    r"^(?P<timestamp>\d{8}T\d{6}Z)_[0-9a-fA-F-]{36}\.jpg$"
)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Milestone 1 capture evidence")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / "timelapse" / "config.json",
    )
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--minimum-count", type=int, default=1)
    parser.add_argument("--since-utc")
    parser.add_argument("--maximum-gap-seconds", type=float)
    return parser.parse_args(argv)


def parse_utc(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp must include Z or an explicit offset")
    return parsed.astimezone(timezone.utc)


def capture_time_from_name(path: Path) -> datetime:
    match = _FILENAME_PATTERN.fullmatch(path.name)
    if match is None:
        raise ValueError("filename does not match the capture naming contract")
    return datetime.strptime(
        match.group("timestamp"),
        "%Y%m%dT%H%M%SZ",
    ).replace(tzinfo=timezone.utc)


def validate_capture(
    path: Path,
    *,
    maximum_width: int,
    maximum_height: int,
) -> dict[str, object]:
    if path.stat().st_size <= 0:
        raise ValueError("file is empty")

    try:
        with Image.open(path) as image:
            image.load()
            image_format = image.format
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"cannot decode image: {exc}") from exc

    if image_format != "JPEG":
        raise ValueError(f"expected JPEG, got {image_format}")
    if width > maximum_width or height > maximum_height:
        raise ValueError(
            f"dimensions {width}x{height} exceed {maximum_width}x{maximum_height}"
        )

    return {
        "path": str(path),
        "captured_at_utc": capture_time_from_name(path).isoformat().replace("+00:00", "Z"),
        "width": width,
        "height": height,
        "size_bytes": path.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    if args.minimum_count < 1:
        print("--minimum-count must be at least 1", file=sys.stderr)
        return 2
    if args.maximum_gap_seconds is not None and args.maximum_gap_seconds <= 0:
        print("--maximum-gap-seconds must be greater than zero", file=sys.stderr)
        return 2

    try:
        config = AgentConfig.from_file(args.config)
        since_utc = parse_utc(args.since_utc) if args.since_utc else None
    except (ConfigurationError, ValueError) as exc:
        print(f"validation configuration error: {exc}", file=sys.stderr)
        return 2

    directory = args.directory or (config.output_directory / config.camera_slug)
    candidates = sorted(directory.rglob("*.jpg")) if directory.exists() else []

    valid: list[dict[str, object]] = []
    invalid: list[dict[str, str]] = []

    for path in candidates:
        try:
            capture_time = capture_time_from_name(path)
            if since_utc is not None and capture_time < since_utc:
                continue
            valid.append(
                validate_capture(
                    path,
                    maximum_width=config.maximum_width,
                    maximum_height=config.maximum_height,
                )
            )
        except (OSError, ValueError) as exc:
            invalid.append({"path": str(path), "error": str(exc)})

    capture_times = [
        parse_utc(str(item["captured_at_utc"]))
        for item in valid
    ]
    capture_times.sort()
    gaps = [
        (current - previous).total_seconds()
        for previous, current in zip(capture_times, capture_times[1:], strict=False)
    ]
    maximum_gap = max(gaps, default=0.0)

    summary = {
        "directory": str(directory),
        "valid_capture_count": len(valid),
        "invalid_capture_count": len(invalid),
        "first_capture_at_utc": (
            capture_times[0].isoformat().replace("+00:00", "Z")
            if capture_times
            else None
        ),
        "last_capture_at_utc": (
            capture_times[-1].isoformat().replace("+00:00", "Z")
            if capture_times
            else None
        ),
        "maximum_gap_seconds": maximum_gap,
        "invalid_files": invalid,
    }
    print(json.dumps(summary, indent=2))

    count_failed = len(valid) < args.minimum_count
    gap_failed = (
        args.maximum_gap_seconds is not None
        and maximum_gap > args.maximum_gap_seconds
    )
    return 1 if invalid or count_failed or gap_failed else 0


if __name__ == "__main__":
    sys.exit(main())
