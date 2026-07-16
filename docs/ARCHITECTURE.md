# Architecture

This document explains how the Android time-lapse security camera system is structured at runtime. It intentionally avoids repository layout and setup steps; those are covered in `README.md`.

## Technology Stack

| Layer | Technology |
|---|---|
| Camera device | Android 9+, Termux, Termux:API, Termux:Boot |
| Camera agent | Python, Pillow, HTTPX, SQLite |
| API server | FastAPI, Uvicorn, Pydantic Settings |
| Persistence | Neon PostgreSQL, SQLAlchemy asyncio, asyncpg, Alembic |
| Image storage | VPS filesystem under `/srv/timelapse` |
| Background processing | Native Python worker process managed by systemd |
| Telegram interface | `python-telegram-bot` long polling |
| Edge and TLS | Host-installed Nginx and Certbot |
| Process management | Native systemd services |
| Quality tooling | Pytest, pytest-asyncio, Ruff |

## System Context

```text
Android phone
  └─ Termux camera agent
       ├─ Captures and compresses JPEGs
       ├─ Stores pending uploads in SQLite
       ├─ Uploads images over HTTPS
       └─ Sends health heartbeats

Internet / HTTPS
  └─ Nginx on Ubuntu VPS
       └─ Proxies public API routes to local FastAPI

Ubuntu VPS
  ├─ FastAPI API service
  │    ├─ Authenticates camera credentials
  │    ├─ Validates uploads and heartbeats
  │    ├─ Stores image metadata in PostgreSQL
  │    └─ Stores image files on disk
  ├─ Worker service
  │    ├─ Evaluates camera health
  │    ├─ Runs motion analysis
  │    ├─ Builds exports
  │    ├─ Enforces retention
  │    └─ Reconciles database/filesystem state
  ├─ Telegram bot service
  │    ├─ Authorizes Telegram users
  │    ├─ Handles status/retrieval commands
  │    └─ Delivers alerts and export files
  └─ Neon PostgreSQL
       └─ Stores cameras, credentials, images, heartbeats, motion events, exports, alerts, and audit events
```

## Core Flows

### 1. Scheduled Capture and Upload

1. The Android agent schedules captures from a monotonic clock.
2. It invokes `termux-camera-photo` for the configured camera ID.
3. The captured file is decoded, EXIF-orientated, resized, and compressed as JPEG.
4. The final local file is written atomically into the pending directory.
5. A SQLite queue row is created with `capture_id`, timestamp, size, checksum, and file path.
6. The upload loop claims due queue rows independently from the capture loop.
7. The agent uploads multipart form data to `/api/v1/cameras/{camera_slug}/images`.
8. The server authenticates the camera credential, validates checksum/JPEG/dimensions, stores metadata, and atomically installs the file.
9. Duplicate retries with the same `capture_id` return `already_stored` and do not duplicate metadata.
10. The Android agent deletes the local file only after a `stored` or `already_stored` confirmation.

### 2. Heartbeats and Camera Health

1. The Android agent sends a heartbeat every configured interval.
2. The heartbeat includes runtime, queue, battery, storage, and recent capture/upload state.
3. The API persists each heartbeat and updates camera `last_seen_at` fields.
4. The worker classifies cameras as online, degraded, offline, or disabled.
5. Alert state is persisted so unchanged conditions do not spam Telegram.
6. Daily heartbeat summaries are created before detailed heartbeat rows expire.

### 3. Motion Processing

1. Each accepted scheduled image creates a pending motion-analysis record.
2. The worker claims pending analysis rows safely.
3. `frame-diff-v1` compares the image with the previous valid scheduled image from the same camera.
4. Metrics are saved with the analysis result.
5. Positive detections are grouped into five-minute motion events.
6. Only the first image in a motion event triggers a Telegram alert.

### 4. Telegram Retrieval and Export

1. Telegram updates are received through long polling.
2. Authorization checks the Telegram user ID before command handling.
3. `/status` and `/latest` read current camera state and latest stored image metadata.
4. `/images` parses an Asia/Jakarta date range and converts it to UTC.
5. The server snapshots selected image IDs into export job rows using a half-open `[start, end)` interval.
6. The export worker builds ZIP parts from the snapshot, writes a manifest, sends parts to Telegram, and removes completed artifacts.
7. Retention excludes active export snapshots so files are not deleted while an export is being built or sent.

### 5. Retention and Reconciliation

1. Retention calculates expiry from original capture time and camera retention settings.
2. Eligible image rows are claimed in batches.
3. Files are deleted before metadata is removed or tombstoned.
4. Disk-pressure handling prioritizes stopping exports before deleting stored images.
5. Reconciliation detects missing files, orphaned files, checksum mismatches, stale staging rows, and stale temporary/export files.
6. Orphaned files are quarantined before final deletion.

## Trust Boundaries

- Android camera credentials are bearer secrets and must only be stored on the phone and server-side metadata.
- The React/web dashboard is not part of the MVP; clients must not access the database directly.
- Telegram user data is untrusted until authorized by server-side records/environment configuration.
- Uploaded filenames are ignored; server-side paths are generated from trusted camera metadata.
- Nginx is the public entry point; FastAPI binds to loopback only.
- PostgreSQL is not exposed by the VPS.

## Data Ownership and Identity

- Camera identity is determined by the authenticated camera credential and requested camera slug.
- Telegram identity is determined by Telegram user ID.
- Export jobs preserve the destination chat at request time for deterministic delivery.
- Worker jobs must be idempotent and safe to resume after process restart.

## Failure and Recovery Model

- Phone offline: images remain in the SQLite queue and retry with backoff.
- Server/API unavailable: queued uploads remain local until confirmed.
- Duplicate upload: server returns `already_stored` without creating another image row.
- Worker interruption: database job state supports reclaim/resume behavior.
- Telegram delivery failure: export artifacts remain until retry or expiry.
- Database/file mismatch: reconciliation repairs, marks, or quarantines according to file state.
