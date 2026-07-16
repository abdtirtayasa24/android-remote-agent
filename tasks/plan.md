# Implementation Plan: Telegram Webhook, Daily Videos, Voice Playback, and Mini App Dashboard

## Overview

This plan adds four coordinated feature areas to the Android Time-Lapse Security Camera project:

1. Daily Asia/Jakarta time-lapse MP4 generation from stored still images, automatically sent via Telegram and deleted immediately after successful delivery.
2. Telegram voice-note playback on the Android phone through a server-side camera command queue and Android polling loop.
3. Migration from `timelapse-bot.service` long polling to Telegram Webhook API handled inside `timelapse-api.service`, with webhook setup performed automatically during API startup.
4. A mobile-fit Telegram Mini App dashboard built with React and Tailwind, compiled to static assets and served by Nginx from the dashboard `dist` directory.

The design preserves the existing architecture rule that request handlers stay lightweight: video generation, audio normalization, command execution, and retries happen in workers or the Android agent, not inside FastAPI request handlers.

## Confirmed Product Decisions

- Daily video boundaries use the Asia/Jakarta calendar day and are converted to UTC internally.
- Generated daily MP4 files are deleted immediately after successful Telegram send; database metadata remains for history/reporting.
- Voice-note playback requires a Telegram command to configure which camera should receive voice playback commands.
- React and Tailwind are approved for the Mini App dashboard, even though the current repository rule says Python-only application code. The repository rule/docs should be updated explicitly before implementation.
- Telegram webhook registration should be performed automatically during API startup.

## Current Architecture Fit

Existing durable patterns to preserve:

- `timelapse-api.service` handles HTTP request boundaries and liveness.
- `timelapse-worker.service` handles long-running jobs and retryable delivery.
- Telegram operational messages remain English only.
- Telegram user-facing timestamps remain Asia/Jakarta.
- Backend timestamps remain UTC.
- Camera-scoped data access continues to use camera credentials and HMAC/constant-time credential validation.
- Authorized Telegram access continues through `telegram_principals` and `TELEGRAM_ADMIN_USER_ID` bootstrap.
- Production remains native systemd, Nginx, Certbot, Neon PostgreSQL, and filesystem storage.

## Architecture Decisions

### 1. Telegram webhook lives in FastAPI

`timelapse-api.service` will initialize a `python-telegram-bot` application during FastAPI lifespan. A new webhook route receives Telegram updates, validates `X-Telegram-Bot-Api-Secret-Token`, converts payloads into `telegram.Update`, and dispatches them to the application.

Automatic setup on API startup:

- If `TELEGRAM_BOT_TOKEN`, `PUBLIC_DOMAIN`, and `TELEGRAM_WEBHOOK_SECRET` are configured, startup calls Telegram `setWebhook` with `https://{PUBLIC_DOMAIN}/api/v1/telegram/webhook`.
- Setup must be idempotent.
- Setup should use a short timeout and redact bot tokens in logs.
- Existing deployment uses one Uvicorn worker, which avoids duplicate startup setup. If API workers are increased later, webhook setup needs a leader/lock or deployment-managed setup.

### 2. Daily video generation is a worker-owned durable job

Add video job tables modelled after export jobs. The worker creates one job per enabled camera per Asia/Jakarta day, snapshots stored scheduled images, generates MP4 with `ffmpeg`, sends it through Telegram, then deletes the MP4 file immediately after successful send.

The database keeps job metadata for dashboard/history:

- status
- image count
- file size/hash before deletion
- Telegram message ID
- completion timestamp
- `deleted_at`/`file_deleted_at`
- stable error code when failed

### 3. Voice-note playback uses camera commands

Voice notes received by Telegram are converted into durable server-side `camera_commands`. The Android agent polls an authenticated command API, downloads audio media, verifies checksum, plays it locally, and reports result.

This avoids inbound connectivity to the Android phone and keeps command execution camera-scoped.

Target camera selection:

- Add Telegram command `/speakcamera [camera]`.
- Without an argument, it shows current configured playback camera and available enabled cameras.
- With a camera slug, it stores that camera as the authorized user's voice playback target.
- If a user sends a voice note without a configured camera, the bot replies with a short instruction to run `/speakcamera <camera>`.

### 4. Dashboard is React + Tailwind static build served by Nginx

Add a new frontend package, likely `dashboard/`, using Vite + React + TypeScript + Tailwind. It builds to `dashboard/dist`. Nginx serves `/dashboard/` directly from the compiled `dist` folder and proxies `/api/` to FastAPI.

The Mini App uses:

```html
<script src="https://telegram.org/js/telegram-web-app.js"></script>
```

Dashboard API requests include Telegram `initData`; FastAPI validates `initData` HMAC using the bot token before returning data.

### 5. Nginx serves static dashboard and proxies APIs

Nginx will add:

- `/dashboard/` static alias to the built dashboard dist directory.
- `/api/v1/dashboard/*` proxy to FastAPI.
- `/api/v1/telegram/webhook` proxy to FastAPI.
- Existing camera image/heartbeat API routes remain.

## Data Model Plan

### New/updated enums

Add stable status/type values while preserving existing enum conventions.

Potential new enums:

- `VideoJobStatus`: `pending`, `processing`, `uploading`, `completed`, `failed`
- `CameraCommandStatus`: `pending`, `claimed`, `started`, `completed`, `failed`, `expired`
- `CameraCommandType`: initially `play_audio`

### New table: `timelapse_video_jobs`

Suggested columns:

- `id` UUID primary key
- `camera_id` FK to `cameras`
- `local_date_jakarta` date, unique per camera/date
- `start_at_utc`, `end_at_utc`
- `status`
- `image_count`
- `storage_path` nullable after deletion
- `file_size_bytes`
- `sha256`
- `telegram_message_id`
- `claimed_at`
- `created_at`, `started_at`, `completed_at`
- `file_deleted_at`
- `error_code`

Constraints/indexes:

- unique `(camera_id, local_date_jakarta)`
- pending/processing index for worker claiming
- recent jobs index for dashboard

### New table: `timelapse_video_job_images`

Snapshot table to protect deterministic job contents:

- `job_id`
- `image_id`
- `ordinal`

Constraints:

- primary key `(job_id, image_id)`
- unique `(job_id, ordinal)`

Retention must protect images referenced by active/pending/processing/uploading video jobs.

### New table: `timelapse_video_deliveries`

Per-recipient delivery state prevents successful recipients from receiving duplicate videos when a later recipient fails:

- `job_id`
- `telegram_chat_id`
- `status`: `pending` or `sent`
- `telegram_message_id`
- `sent_at`
- `error_code`

Constraints/indexes:

- primary key `(job_id, telegram_chat_id)`
- partial pending-delivery index

### New table: `camera_commands`

Suggested columns:

- `id` UUID primary key
- `camera_id` FK to `cameras`
- `command_type`, initially `play_audio`
- `status`
- `payload` JSONB
- `media_storage_path`
- `media_mime_type`
- `media_size_bytes`
- `media_sha256`
- `requested_by_telegram_user_id`
- `requested_in_telegram_chat_id`
- `telegram_message_id`
- `claimed_at`, `started_at`, `completed_at`
- `expires_at`
- `error_code`
- `created_at`

Indexes:

- pending commands by `(camera_id, status, created_at)`
- recent commands by requester/dashboard

### Update table: `telegram_principals`

Add nullable `voice_playback_camera_id` FK to `cameras.id`.

This stores each Telegram user's selected camera for voice playback.

## API Plan

### Telegram webhook

```http
POST /api/v1/telegram/webhook
X-Telegram-Bot-Api-Secret-Token: <secret>
```

Responsibilities:

- Validate secret header using constant-time comparison.
- Parse Telegram update.
- Dispatch to Telegram application.
- Return quickly.
- Do not log raw update payloads, bot token, voice file URLs, or private payloads.

### Camera command polling

```http
GET /api/v1/cameras/{camera_slug}/commands/next
Authorization: Bearer cam_...
```

Returns either no command or one claimed command.

```http
GET /api/v1/cameras/{camera_slug}/commands/{command_id}/media
Authorization: Bearer cam_...
```

Streams private audio media for that camera command only.

```http
POST /api/v1/cameras/{camera_slug}/commands/{command_id}/result
Authorization: Bearer cam_...
```

Reports `started`, `completed`, or `failed` with stable error code.

### Dashboard APIs

All dashboard endpoints require Telegram Mini App `initData` validation.

Suggested initial endpoints:

```http
GET /api/v1/dashboard/summary
GET /api/v1/dashboard/cameras
GET /api/v1/dashboard/cameras/{camera_slug}
GET /api/v1/dashboard/recent-images
GET /api/v1/dashboard/recent-events
GET /api/v1/dashboard/jobs
GET /api/v1/dashboard/commands
```

Response policy:

- Do not expose filesystem paths.
- Use stable, typed JSON shapes.
- Include Asia/Jakarta formatted display timestamps or UTC ISO timestamps plus timezone metadata; prefer one consistent pattern across endpoints.
- Scope all data to authorized Telegram users. Current system has global viewer/admin roles, not per-camera ACLs, so preserve existing access semantics unless explicitly expanded later.

## Telegram Bot Command Plan

Existing commands remain:

- `/help`
- `/status [camera]`
- `/latest [camera]`
- `/images ...`
- `/exports`
- `/cancel <job-id>`

Add commands:

### `/speakcamera [camera]`

- No args: show current voice playback camera and available enabled cameras.
- One arg: validate camera slug, set preferred voice playback camera for that Telegram principal.
- Authorized users only.
- English messages only.

### `/dashboard`

- Sends a Telegram keyboard/button that opens the Mini App.
- Authorized users only.
- Button URL points to `https://{PUBLIC_DOMAIN}/dashboard/`.

### Deferred `/timelapse [camera]`

- Defer this command until after the dashboard is fully implemented.
- The dashboard is the primary surface for daily video job statuses.
- Add `/timelapse` later only if operators still need a lightweight command-line-style Telegram status view.

## Android Agent Plan

Add a new command loop alongside existing capture/upload/cleanup/heartbeat loops.

Behavior:

1. Poll command endpoint every few seconds.
2. If no command, wait.
3. If `play_audio`, download media to a temp path.
4. Verify size and SHA-256.
5. Report `started`.
6. Play with `termux-media-player play <downloaded-audio-file>` using a subprocess argument list.
7. Report `completed` or `failed`.
8. Delete temp file immediately after playback succeeds or fails, retaining only command metadata.

Configuration additions:

- `command_poll_seconds`
- `command_request_timeout_seconds` or reuse existing request timeout
- `voice_playback_enabled`

Implementation must not block capture scheduling. Playback can run in a separate task/thread, but the system should avoid playing multiple voice notes concurrently on the same camera. If `termux-media-player` is missing, times out, or returns a non-zero exit code, report a stable playback error code and delete the temporary audio file.

## Mini App Frontend Plan

### Package

Create `dashboard/`:

```text
dashboard/
  .npmrc
  package.json
  package-lock.json
  index.html
  src/
    main.tsx
    App.tsx
    api.ts
    telegram.ts
    components/
    styles.css
  tailwind.config.*
  vite.config.*
  tsconfig.json
```

`dashboard/.npmrc` should pin the npm registry to `https://registry.npmjs.org/`. Before generating or updating `package-lock.json`, confirm the current environment is using the intended registry, for example with `npm config get registry`, and set it if necessary.

Preferred stack:

- Vite
- React
- TypeScript
- Tailwind CSS
- Minimal dependencies

### UI principles

- Mobile-first, works at 320px width.
- Uses Telegram theme params for light/dark mode.
- Uses Telegram BackButton where navigation depth exists.
- Uses haptic feedback for refresh/actions when available.
- Loading, empty, and error states for every view.
- No unsafe `innerHTML` for API-provided content.
- Avoid heavy client state libraries initially; local state and small typed API helpers are enough.

### Initial screens

1. Overview
   - health summary
   - latest capture time
   - daily image count
   - active issues
2. Cameras
   - per-camera health, queue, battery, temperature
3. Motion
   - recent motion event summaries
4. Daily videos
   - recent video generation/delivery statuses
5. Exports
   - recent export jobs
6. Voice playback
   - current selected camera and recent command statuses

## Deployment Plan

### System packages

Add to bootstrap:

- `ffmpeg`
- Node.js 22 LTS and npm from a documented, pinned NodeSource setup

Dashboard build strategy:

- Commit `dashboard/package-lock.json`.
- Commit `dashboard/.npmrc` with `registry=https://registry.npmjs.org/`.
- Before first lockfile generation in the current environment, run `npm config get registry` and set the registry to `https://registry.npmjs.org/` if needed.
- During deployment, run `npm ci` and `npm run build` inside the release's `dashboard/` directory before switching `/opt/android-remote/current`.
- If dashboard build fails, deployment fails and the current release remains unchanged.
- Publish only compiled assets to `/var/www/android-remote/dashboard` with Nginx-readable permissions.
- If Telegram webhook setup fails during API startup, API startup fails so systemd reports an unhealthy service instead of silently running without Telegram operations.

### Storage layout

Add directories:

```text
/srv/timelapse/timelapses
/srv/timelapse/audio-commands
```

### systemd

- Remove `timelapse-bot.service` from deployment/startup.
- Keep `timelapse-api.service` and `timelapse-worker.service`.
- Ensure API has network access for Telegram webhook setup.
- Keep Uvicorn worker count at 1 unless webhook setup is redesigned for multi-worker startup.

### Nginx

Add routes:

- `/dashboard/` static alias to dashboard dist
- `/api/v1/telegram/webhook` proxy to API
- `/api/v1/dashboard/` proxy to API
- `/api/v1/cameras/*/commands/*` proxy to API

Review body size limits:

- Existing image upload limit remains 5 MiB.
- Telegram webhook JSON is small.
- Camera command media download is server-to-camera response, not upload.

### Environment

Suggested additions:

```env
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_WEBHOOK_AUTO_SETUP=true
TELEGRAM_WEBAPP_ENABLED=true
DAILY_TIMELAPSE_ENABLED=true
DAILY_TIMELAPSE_SEND_HOUR_JAKARTA=0
DAILY_TIMELAPSE_SEND_MINUTE_JAKARTA=10
DAILY_TIMELAPSE_FRAME_RATE=24
VOICE_PLAYBACK_ENABLED=true
VOICE_PLAYBACK_MAX_DURATION_SECONDS=60
VOICE_PLAYBACK_COMMAND_TTL_SECONDS=120
CAMERA_COMMAND_POLL_SECONDS=3
```

## Phased Task List

### Phase 0: Rule and dependency preparation

- Task 1: Update repository docs/rules to permit `dashboard/` React/Tailwind application code.
- Task 2: Choose and document frontend build/deployment strategy.

### Phase 1: Telegram webhook migration

- Task 3: Refactor bot application for reusable webhook dispatch.
- Task 4: Add FastAPI Telegram webhook endpoint with secret validation.
- Task 5: Add automatic Telegram webhook setup during API startup.
- Task 6: Update Nginx, systemd, deployment, and operations docs to remove long polling bot service.

### Phase 2: Daily time-lapse videos

- Task 7: Add video job schema/migration/models/tests.
- Task 8: Add daily job creation and snapshot service.
- Task 9: Add MP4 generation service using `ffmpeg`.
- Task 10: Add worker loop for generation, Telegram send, and immediate file deletion.
- Task 11: Add video job status surfaces for Telegram/dashboard.

### Phase 3: Voice-note playback

- Task 12: Add camera command schema and voice playback camera preference.
- Task 13: Add `/speakcamera [camera]` command.
- Task 14: Add a Telegram voice-note handler that queues worker-side audio preparation.
- Task 15: Add authenticated camera command polling/media/result APIs.
- Task 16: Add Android agent command polling and playback loop.

### Phase 4: Mini App dashboard

- Task 17: Scaffold Vite React TypeScript Tailwind dashboard package.
- Task 18: Add Telegram Mini App `initData` verification service and tests.
- Task 19: Add dashboard API endpoints.
- Task 20: Build mobile dashboard UI and Telegram native integrations.
- Task 21: Add `/dashboard` Telegram command and Nginx static serving.

### Phase 5: Hardening, docs, and acceptance

- Task 22: Add storage/reconciliation/retention cleanup for video/audio artifacts.
- Task 23: Update README, architecture, implemented features, and operator docs.
- Task 24: Run full local verification and document real-device/VPS acceptance checklist.

## Checkpoints

### Checkpoint A: Webhook migration complete

- Existing Telegram commands work through webhook.
- `timelapse-bot.service` is no longer required.
- API startup configures Telegram webhook automatically.
- Deployment rollback path is documented.

### Checkpoint B: Daily videos complete

- A daily video is generated for the previous Asia/Jakarta day.
- Video is delivered through Telegram.
- Video file is deleted immediately after successful send.
- Job metadata remains visible.

### Checkpoint C: Voice playback complete

- User configures target camera with `/speakcamera`.
- Voice note creates a camera command.
- Android phone plays the audio and reports completion/failure.
- Expired/failed commands are auditable.

### Checkpoint D: Dashboard complete

- Dashboard opens as Telegram Mini App.
- React/Tailwind build produces static dist.
- Nginx serves `/dashboard/` from dist.
- Dashboard API validates Telegram `initData`.
- UI works on mobile and Telegram light/dark themes.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---:|---|
| Webhook setup fails on API startup | High | Fail API startup, short timeout, clear redacted logs, operator docs, deployment rollback path |
| Multiple API workers duplicate webhook setup | Medium | Keep `--workers 1`; document constraint |
| Daily MP4 too large for Telegram | Medium | Configurable frame rate/CRF, target size policy, stable `video_too_large` failure |
| `ffmpeg` consumes CPU/storage | Medium | Worker-only processing, one job at a time initially, temp dirs, cleanup on failure |
| Voice playback abuse | High | Authorized users only, configured camera target, duration/size limits, expiry, audit events |
| Android audio command unavailable | Medium | Detect command failure, stable error code, operator setup docs |
| Mini App auth spoofing | High | Server-side Telegram `initData` HMAC validation and freshness checks |
| Frontend supply chain complexity | Medium | Minimal dependencies, lockfile, build verification, no unnecessary UI libraries |
| Retention deletes images while video job runs | Medium | Snapshot table and retention protection for active video jobs |
| Generated files leak through static serving | High | Store videos/audio outside dashboard dist; never serve media by filesystem path |

## Resolved Implementation Decisions

- Android voice playback uses `termux-media-player play <downloaded-audio-file>`.
- Generated video and audio artifacts are deleted immediately after success or failure; only database metadata is retained.
- `/timelapse [camera]` is deferred until after the dashboard is fully implemented.
- Telegram webhook setup failure fails API startup so systemd marks `timelapse-api.service` unhealthy.
