# Todo: Telegram Webhook, Daily Videos, Voice Playback, Mini App Dashboard

## Task 1: Update repository rules for dashboard frontend

**Description:** Explicitly allow React/Tailwind dashboard application code under `dashboard/` while preserving Python-only rules for server and camera-agent runtime code.

**Acceptance criteria:**
- [x] Repository rules/docs allow `dashboard/` React/Tailwind code.
- [x] Server and camera-agent Python-only rule remains intact.
- [x] The exception is narrow and documented.

**Verification:**
- [x] Documentation links/tests still pass: `cd server && ../.venv/bin/pytest tests/unit/test_documentation_links.py -q`

**Dependencies:** None

**Files likely touched:**
- `AGENTS.md`
- `README.md`
- `docs/ARCHITECTURE.md`

**Estimated scope:** Small

---

## Task 2: Decide and document dashboard build/deployment strategy

**Description:** Select how Vite/React/Tailwind is built in production and how Nginx serves the compiled `dist` directory.

**Acceptance criteria:**
- [x] Node.js 22 LTS/npm installation via documented NodeSource setup is documented.
- [x] npm registry is pinned to `https://registry.npmjs.org/` before generating `package-lock.json`.
- [x] Build strategy requires a committed project-local `dashboard/.npmrc` with the intended registry once the dashboard package is created.
- [x] Deployment runs `npm ci` and `npm run build` before switching the current release.
- [x] Deployment path for `dashboard/dist` is defined as `/var/www/android-remote/dashboard`.
- [x] Nginx static serving path is defined.

**Verification:**
- [x] Static review of docs and deployment plan.

**Dependencies:** Task 1

**Files likely touched:**
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/operator/server-installation.md`
- `infrastructure/bootstrap-ubuntu.sh`
- `infrastructure/deploy-systemd.sh`

**Estimated scope:** Medium

---

## Task 3: Refactor Telegram bot for webhook dispatch

**Description:** Separate Telegram command handler registration from long-polling startup so FastAPI can initialize and process updates through the existing command handlers.

**Acceptance criteria:**
- [ ] Existing command handlers are registered through a reusable builder.
- [ ] Long-polling entry point can be removed or left only for local debugging if explicitly documented.
- [ ] Existing command behavior remains unchanged.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/unit/test_telegram_authorization.py tests/integration/test_telegram_commands.py -q`

**Dependencies:** None

**Files likely touched:**
- `server/src/timelapse/bot/application.py`
- `server/tests/integration/test_telegram_commands.py`

**Estimated scope:** Medium

---

## Task 4: Add Telegram webhook endpoint

**Description:** Add FastAPI endpoint for Telegram updates with secret-token header validation and update dispatch.

**Acceptance criteria:**
- [ ] `POST /api/v1/telegram/webhook` validates `X-Telegram-Bot-Api-Secret-Token` with constant-time comparison.
- [ ] Valid updates are dispatched to the Telegram application.
- [ ] Invalid/missing secret is rejected without processing.
- [ ] Bot token and raw update payloads are not logged.

**Verification:**
- [ ] New unit/integration webhook tests pass.
- [ ] `cd server && ../.venv/bin/pytest tests/unit tests/integration/test_telegram_commands.py -q`

**Dependencies:** Task 3

**Files likely touched:**
- `server/src/timelapse/api/main.py`
- `server/src/timelapse/api/telegram.py`
- `server/src/timelapse/configuration.py`
- `server/tests/unit/`
- `server/tests/integration/`

**Estimated scope:** Medium

---

## Task 5: Add automatic Telegram webhook setup on API startup

**Description:** During FastAPI lifespan startup, configure Telegram webhook automatically when Telegram settings are present.

**Acceptance criteria:**
- [ ] Startup calls Telegram `setWebhook` with `https://{PUBLIC_DOMAIN}/api/v1/telegram/webhook`.
- [ ] Webhook secret is included in setup.
- [ ] Setup is idempotent and uses a short timeout.
- [ ] Webhook setup failure fails API startup so systemd marks the API unhealthy.
- [ ] Failures are logged without leaking bot token.
- [ ] Existing API liveness still works when setup succeeds.

**Verification:**
- [ ] Unit tests for webhook setup client behavior.
- [ ] `cd server && ../.venv/bin/pytest tests/unit/test_liveness.py tests/unit/test_telegram_client.py -q`

**Dependencies:** Task 4

**Files likely touched:**
- `server/src/timelapse/api/main.py`
- `server/src/timelapse/services/telegram_client.py`
- `server/src/timelapse/configuration.py`
- `server/tests/unit/`

**Estimated scope:** Medium

---

## Task 6: Remove production long-polling bot service

**Description:** Update production deployment so Telegram handling is inside `timelapse-api.service` and `timelapse-bot.service` is no longer installed/started.

**Acceptance criteria:**
- [ ] Deploy script stops old bot service safely.
- [ ] Deploy script no longer installs/restarts/enables `timelapse-bot.service`.
- [ ] `timelapse-camera.target` no longer requires the bot service.
- [ ] Nginx proxies the webhook endpoint.
- [ ] Operator docs use API logs for webhook troubleshooting.

**Verification:**
- [ ] Shellcheck/static review of deployment script changes.
- [ ] Documentation link tests pass.

**Dependencies:** Task 5

**Files likely touched:**
- `infrastructure/deploy-systemd.sh`
- `infrastructure/systemd/timelapse-camera.target`
- `infrastructure/systemd/timelapse-api.service`
- `infrastructure/nginx/timelapse-camera.conf.template`
- `docs/operator/server-installation.md`
- `docs/operator/operations.md`

**Estimated scope:** Medium

---

## Task 7: Add daily time-lapse video schema

**Description:** Add database models and Alembic migration for daily video jobs and video job image snapshots.

**Acceptance criteria:**
- [ ] `timelapse_video_jobs` table exists.
- [ ] `timelapse_video_job_images` table exists.
- [ ] Unique camera/date constraint prevents duplicate daily jobs.
- [ ] Worker-claiming indexes exist.
- [ ] SQLAlchemy models and schema metadata tests are updated.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/unit/test_schema_metadata.py -q`
- [ ] Integration migration tests pass when `TEST_DATABASE_URL` is configured.

**Dependencies:** None

**Files likely touched:**
- `server/src/timelapse/models/entities.py`
- `server/src/timelapse/models/enums.py`
- `server/migrations/versions/*.py`
- `server/tests/unit/test_schema_metadata.py`

**Estimated scope:** Medium

---

## Task 8: Add daily video job creation and snapshot service

**Description:** Create a service that creates one previous-day Asia/Jakarta video job per enabled camera and snapshots stored scheduled images in UTC order.

**Acceptance criteria:**
- [ ] Previous Asia/Jakarta day is converted to correct UTC range.
- [ ] Job creation is idempotent.
- [ ] Snapshot image ordering is deterministic.
- [ ] Empty image days are handled with stable status/error code.

**Verification:**
- [ ] Focused unit tests for date-window logic.
- [ ] Integration tests for idempotent job creation and snapshots.

**Dependencies:** Task 7

**Files likely touched:**
- `server/src/timelapse/services/timelapse_video_requests.py`
- `server/tests/unit/`
- `server/tests/integration/`

**Estimated scope:** Medium

---

## Task 9: Add MP4 generation service using ffmpeg

**Description:** Generate MP4 files from snapshot images using `ffmpeg` in a temporary directory with atomic finalization.

**Acceptance criteria:**
- [ ] `ffmpeg` is invoked through argument lists, not shell strings.
- [ ] Images are ordered by snapshot ordinal.
- [ ] Generated MP4 metadata includes size and SHA-256.
- [ ] Failure paths produce stable error codes.
- [ ] Temporary files are cleaned up.

**Verification:**
- [ ] Unit tests for command construction and failure handling.
- [ ] Optional integration test skipped when `ffmpeg` is unavailable.

**Dependencies:** Task 8

**Files likely touched:**
- `server/src/timelapse/services/timelapse_video_generator.py`
- `server/tests/unit/`
- `server/tests/integration/`
- `infrastructure/bootstrap-ubuntu.sh`

**Estimated scope:** Medium

---

## Task 10: Add daily video worker loop and Telegram delivery

**Description:** Add worker processing that claims video jobs, generates MP4s, sends them via Telegram, then deletes files immediately after successful delivery.

**Acceptance criteria:**
- [ ] Worker claims pending/processing jobs safely with row locks.
- [ ] MP4 is sent once per completed job.
- [ ] Restart after send does not resend completed videos.
- [ ] MP4 file is deleted immediately after successful send.
- [ ] Job metadata remains for dashboard/history.

**Verification:**
- [ ] Integration tests for generate/send/delete/resume behavior.
- [ ] `cd server && ../.venv/bin/pytest tests/integration/test_timelapse_video_worker.py -q`

**Dependencies:** Task 9

**Files likely touched:**
- `server/src/timelapse/workers/application.py`
- `server/src/timelapse/services/timelapse_video_worker.py`
- `server/src/timelapse/services/telegram_client.py`
- `server/src/timelapse/configuration.py`
- `server/tests/integration/`

**Estimated scope:** Medium

---

## Task 11: Protect video job images from retention

**Description:** Ensure retention does not delete images referenced by active daily video jobs.

**Acceptance criteria:**
- [ ] Images referenced by pending/processing/uploading video jobs are protected.
- [ ] Completed/failed old jobs do not permanently block retention.
- [ ] Tests cover retention/video race cases.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/integration/test_retention.py -q`

**Dependencies:** Task 7

**Files likely touched:**
- `server/src/timelapse/services/retention.py`
- `server/tests/integration/test_retention.py`

**Estimated scope:** Small

---

## Task 12: Add camera command schema and voice playback preference

**Description:** Add durable camera commands and store each Telegram principal's selected voice playback camera.

**Acceptance criteria:**
- [ ] `camera_commands` table exists.
- [ ] `telegram_principals.voice_playback_camera_id` exists.
- [ ] Pending command index supports camera polling.
- [ ] Schema tests are updated.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/unit/test_schema_metadata.py -q`

**Dependencies:** None

**Files likely touched:**
- `server/src/timelapse/models/entities.py`
- `server/src/timelapse/models/enums.py`
- `server/migrations/versions/*.py`
- `server/tests/unit/test_schema_metadata.py`

**Estimated scope:** Medium

---

## Task 13: Add `/speakcamera [camera]` Telegram command

**Description:** Let authorized Telegram users view or set the camera used for voice-note playback.

**Acceptance criteria:**
- [ ] `/speakcamera` shows current target and available enabled cameras.
- [ ] `/speakcamera <camera>` validates and saves target camera.
- [ ] Unauthorized users get generic denial.
- [ ] Help text includes the new command.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/integration/test_telegram_commands.py -q`

**Dependencies:** Task 12, Task 3 or Task 4 depending on implementation order

**Files likely touched:**
- `server/src/timelapse/bot/application.py`
- `server/src/timelapse/bot/commands.py`
- `server/src/timelapse/services/voice_playback_preferences.py`
- `server/tests/integration/test_telegram_commands.py`

**Estimated scope:** Medium

---

## Task 14: Add Telegram voice-note handler

**Description:** Handle authorized Telegram voice notes by downloading/normalizing audio and creating a `play_audio` camera command for the user's configured camera.

**Acceptance criteria:**
- [ ] Voice notes require configured target camera.
- [ ] Duration and file size limits are enforced.
- [ ] Telegram file download errors produce safe user-facing messages.
- [ ] Audio is normalized with `ffmpeg` to a format playable by `termux-media-player`.
- [ ] Command is queued with expiry and audit metadata.
- [ ] Generated audio artifacts are deleted after command success or failure, retaining only metadata.
- [ ] No raw Telegram file URLs or tokens are logged.

**Verification:**
- [ ] Unit tests for limit/target-camera decisions.
- [ ] Integration tests for command creation.

**Dependencies:** Task 13

**Files likely touched:**
- `server/src/timelapse/bot/application.py`
- `server/src/timelapse/services/voice_note_commands.py`
- `server/src/timelapse/services/telegram_client.py`
- `server/src/timelapse/configuration.py`
- `server/tests/unit/`
- `server/tests/integration/`

**Estimated scope:** Medium

---

## Task 15: Add camera command polling/media/result APIs

**Description:** Add authenticated camera APIs for Android agents to fetch commands, download media, and report command results.

**Acceptance criteria:**
- [ ] Camera credentials authorize only matching camera commands.
- [ ] Expired commands are not delivered.
- [ ] Media download verifies command ownership and status.
- [ ] Result endpoint accepts stable command states/error codes.
- [ ] Filesystem paths are never exposed.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/integration/test_camera_commands.py -q`

**Dependencies:** Task 12

**Files likely touched:**
- `server/src/timelapse/api/commands.py`
- `server/src/timelapse/api/main.py`
- `server/src/timelapse/schemas/commands.py`
- `server/src/timelapse/services/camera_commands.py`
- `server/tests/integration/`

**Estimated scope:** Medium

---

## Task 16: Add Android command polling and audio playback loop

**Description:** Extend the Android agent with a command loop that polls for `play_audio`, downloads media, verifies it, plays it, reports result, and cleans temporary files.

**Acceptance criteria:**
- [ ] Command loop runs alongside capture/upload/heartbeat without blocking captures.
- [ ] Downloaded audio SHA-256 and size are verified.
- [ ] Playback uses `termux-media-player play <downloaded-audio-file>` with subprocess arguments, not shell interpolation.
- [ ] Playback success/failure is reported.
- [ ] Temp audio files are removed immediately after success or failure.
- [ ] Credentials and media paths are not logged.

**Verification:**
- [ ] `PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q`

**Dependencies:** Task 15

**Files likely touched:**
- `camera-agent/src/camera_agent/configuration.py`
- `camera-agent/src/camera_agent/main.py`
- `camera-agent/src/camera_agent/commands.py`
- `camera-agent/tests/`
- `camera-agent/config.example.json`

**Estimated scope:** Medium

---

## Task 17: Scaffold Vite React TypeScript Tailwind dashboard

**Description:** Add a new `dashboard/` package that builds static Mini App assets to `dashboard/dist`.

**Acceptance criteria:**
- [ ] Vite React TypeScript app is present.
- [ ] Tailwind is configured.
- [ ] `dashboard/.npmrc` pins `registry=https://registry.npmjs.org/`.
- [ ] `package-lock.json` is generated after confirming the current npm registry points to `https://registry.npmjs.org/`.
- [ ] `npm run build` emits static `dist` assets.
- [ ] Lockfile is committed.
- [ ] Initial page loads Telegram Web App script.

**Verification:**
- [ ] `cd dashboard && npm ci && npm run build`

**Dependencies:** Task 1, Task 2

**Files likely touched:**
- `dashboard/.npmrc`
- `dashboard/package.json`
- `dashboard/package-lock.json`
- `dashboard/index.html`
- `dashboard/src/*`
- `dashboard/tailwind.config.*`
- `dashboard/vite.config.*`

**Estimated scope:** Medium

---

## Task 18: Add Telegram Mini App initData verification

**Description:** Verify Telegram Mini App `initData` server-side and map it to an authorized Telegram principal.

**Acceptance criteria:**
- [ ] HMAC verification follows Telegram Web App rules.
- [ ] Expired `auth_date` is rejected.
- [ ] Tampered data is rejected.
- [ ] Unauthorized users get generic denial.
- [ ] Existing `TELEGRAM_ADMIN_USER_ID` bootstrap remains valid.

**Verification:**
- [ ] Unit tests with valid, tampered, expired, and unauthorized initData.

**Dependencies:** Task 4

**Files likely touched:**
- `server/src/timelapse/services/telegram_webapp_auth.py`
- `server/src/timelapse/api/dashboard.py`
- `server/tests/unit/`

**Estimated scope:** Medium

---

## Task 19: Add dashboard API endpoints

**Description:** Add JSON endpoints for dashboard summary, cameras, recent images/events, jobs, and command history.

**Acceptance criteria:**
- [ ] Endpoints require valid Mini App initData.
- [ ] Responses expose no filesystem paths.
- [ ] Timestamps are consistent and dashboard-ready.
- [ ] Dashboard data includes daily videos and voice command statuses.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/integration/test_dashboard_api.py -q`

**Dependencies:** Task 18, Task 10, Task 12

**Files likely touched:**
- `server/src/timelapse/api/dashboard.py`
- `server/src/timelapse/schemas/dashboard.py`
- `server/src/timelapse/services/dashboard_reports.py`
- `server/tests/integration/`

**Estimated scope:** Medium

---

## Task 20: Build Mini App dashboard UI

**Description:** Implement mobile-first React/Tailwind UI consuming dashboard APIs and Telegram native features.

**Acceptance criteria:**
- [ ] Overview, Cameras, Motion, Daily Videos, Exports, and Voice Playback views exist.
- [ ] UI handles loading, error, and empty states.
- [ ] Telegram light/dark theme params are respected.
- [ ] BackButton and haptic feedback are used where appropriate.
- [ ] App works at 320px mobile width.
- [ ] No unsafe rendering of untrusted strings.

**Verification:**
- [ ] `cd dashboard && npm run build`
- [ ] Manual mobile-width review.

**Dependencies:** Task 19

**Files likely touched:**
- `dashboard/src/App.tsx`
- `dashboard/src/api.ts`
- `dashboard/src/telegram.ts`
- `dashboard/src/components/*`
- `dashboard/src/styles.css`

**Estimated scope:** Medium

---

## Task 21: Serve dashboard via Nginx and add `/dashboard` command

**Description:** Deploy static dashboard assets and add a Telegram command that opens the Mini App.

**Acceptance criteria:**
- [ ] Nginx serves `/dashboard/` from built `dist` assets.
- [ ] Deep/static asset paths resolve correctly.
- [ ] `/dashboard` command sends a Web App button to authorized users.
- [ ] Unauthorized users get generic denial.

**Verification:**
- [ ] `curl -fsS https://<domain>/dashboard/` in deployed environment.
- [ ] Focused Telegram command tests.

**Dependencies:** Task 17, Task 20, Task 6

**Files likely touched:**
- `infrastructure/nginx/timelapse-camera.conf.template`
- `infrastructure/deploy-systemd.sh`
- `server/src/timelapse/bot/application.py`
- `server/src/timelapse/bot/commands.py`
- `server/tests/integration/test_telegram_commands.py`

**Estimated scope:** Medium

---

## Task 22: Add cleanup/reconciliation for video and audio artifacts

**Description:** Extend storage layout, reconciliation, and cleanup logic for generated videos and voice-note audio files.

**Acceptance criteria:**
- [ ] Sent video files are deleted immediately after successful Telegram delivery.
- [ ] Failed video artifacts are deleted with only metadata retained.
- [ ] Audio command files are deleted immediately after command success or failure.
- [ ] Stale temp files for video/audio are cleaned.
- [ ] Referenced active files are protected.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/integration/test_reconciliation.py -q`

**Dependencies:** Task 10, Task 14

**Files likely touched:**
- `server/src/timelapse/services/reconciliation.py`
- `server/src/timelapse/services/image_files.py`
- `server/tests/integration/test_reconciliation.py`

**Estimated scope:** Medium

---

## Task 23: Update documentation and operator runbooks

**Description:** Update project and operator docs to reflect webhook bot, daily videos, voice playback, dashboard, new services, and acceptance steps.

**Acceptance criteria:**
- [ ] README describes new dashboard and commands.
- [ ] Architecture doc reflects webhook, video jobs, command queue, and React dashboard.
- [ ] Implemented features doc is updated.
- [ ] Operator docs include webhook troubleshooting, dashboard deployment, voice playback setup, and daily video acceptance.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/unit/test_documentation_links.py -q`

**Dependencies:** Tasks 6, 10, 16, 21

**Files likely touched:**
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/IMPLEMENTED.md`
- `docs/operator/server-installation.md`
- `docs/operator/android-installation.md`
- `docs/operator/operations.md`
- `docs/operator/acceptance-coverage.md`

**Estimated scope:** Medium

---

## Task 24: Final verification and acceptance evidence

**Description:** Run local quality checks and document real VPS/Android acceptance steps for the new features.

**Acceptance criteria:**
- [ ] Server unit tests pass.
- [ ] Camera-agent tests pass.
- [ ] Ruff check passes.
- [ ] Ruff format check passes.
- [ ] Dashboard build passes.
- [ ] Real-environment acceptance checklist is documented.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/unit -q`
- [ ] `PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q`
- [ ] `.venv/bin/ruff check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests`
- [ ] `.venv/bin/ruff format --check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests`
- [ ] `cd dashboard && npm ci && npm run build`

**Dependencies:** All implementation tasks

**Files likely touched:**
- `docs/operator/acceptance-coverage.md`
- `docs/operator/soak-test-report.md`

**Estimated scope:** Medium
