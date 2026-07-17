# Todo: Telegram Webhook, Daily Videos, Voice Playback


## Task 3: Refactor Telegram bot for webhook dispatch

**Description:** Separate Telegram command handler registration from long-polling startup so FastAPI can initialize and process updates through the existing command handlers.

**Acceptance criteria:**
- [x] Existing command handlers are registered through a reusable builder.
- [x] Long-polling entry point can be removed or left only for local debugging if explicitly documented.
- [x] Existing command behavior remains unchanged.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit/test_telegram_authorization.py tests/integration/test_telegram_commands.py -q` (covered by unit and integration suite runs with `.env.test.local`)

**Dependencies:** None

**Files likely touched:**
- `server/src/timelapse/bot/application.py`
- `server/tests/integration/test_telegram_commands.py`

**Estimated scope:** Medium

---

## Task 4: Add Telegram webhook endpoint

**Description:** Add FastAPI endpoint for Telegram updates with secret-token header validation and update dispatch.

**Acceptance criteria:**
- [x] `POST /api/v1/telegram/webhook` validates `X-Telegram-Bot-Api-Secret-Token` with constant-time comparison.
- [x] Valid updates are dispatched to the Telegram application.
- [x] Invalid/missing secret is rejected without processing.
- [x] Bot token and raw update payloads are not logged.

**Verification:**
- [x] New unit webhook tests pass.
- [x] `cd server && ../.venv/bin/pytest tests/unit tests/integration/test_telegram_commands.py -q` (covered by unit and integration suite runs with `.env.test.local`)

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
- [x] Startup calls Telegram `setWebhook` with `https://{PUBLIC_DOMAIN}/api/v1/telegram/webhook`.
- [x] Webhook secret is included in setup.
- [x] Setup is idempotent and uses a short timeout.
- [x] Webhook setup failure fails API startup so systemd marks the API unhealthy.
- [x] Failures are logged without leaking bot token.
- [x] Existing API liveness still works when setup succeeds.

**Verification:**
- [x] Unit tests for webhook setup client behavior.
- [x] `cd server && ../.venv/bin/pytest tests/unit/test_liveness.py tests/unit/test_telegram_client.py -q`

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
- [x] Deploy script stops old bot service safely.
- [x] Deploy script no longer installs/restarts/enables `timelapse-bot.service`.
- [x] `timelapse-camera.target` no longer requires the bot service.
- [x] Nginx proxies the webhook endpoint.
- [x] Operator docs use API logs for webhook troubleshooting.

**Verification:**
- [x] Bash syntax and static deployment contract tests pass.
- [x] Documentation link tests pass.

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
- [x] `timelapse_video_jobs` table exists.
- [x] `timelapse_video_job_images` and `timelapse_video_deliveries` tables exist.
- [x] Unique camera/date constraint prevents duplicate daily jobs.
- [x] Worker-claiming indexes exist.
- [x] SQLAlchemy models and schema metadata tests are updated.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit/test_schema_metadata.py -q`
- [x] Transactional PostgreSQL migration-chain test covers upgrade and downgrade through `20260717_0003`.

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
- [x] Previous Asia/Jakarta day is converted to correct UTC range.
- [x] Job creation is idempotent.
- [x] Snapshot image ordering is deterministic.
- [x] Empty image days are handled with stable status/error code.

**Verification:**
- [x] Focused date-window behavior is covered by integration tests.
- [x] Integration tests for idempotent job creation and snapshots.

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
- [x] `ffmpeg` is invoked through argument lists, not shell strings.
- [x] Images are ordered by snapshot ordinal.
- [x] Generated MP4 metadata includes size and SHA-256.
- [x] Failure paths produce stable error codes.
- [x] Temporary files are cleaned up.

**Verification:**
- [x] Unit tests for command construction and failure handling.
- [x] Real local `ffmpeg` generation check passed with two JPEG frames.

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
- [x] Worker uses stale-aware leases to claim pending/processing jobs safely.
- [x] Per-recipient delivery state prevents duplicate sends after partial failure.
- [x] Storage pressure defers generation and removes retained retry artifacts.
- [x] MP4 is sent once per completed job.
- [x] Restart after send does not resend completed videos.
- [x] MP4 file is deleted immediately after successful send.
- [x] Job metadata remains for history.

**Verification:**
- [x] Integration tests for generate/send/delete/resume behavior.
- [x] `cd server && ../.venv/bin/pytest tests/integration/test_timelapse_video_worker.py -q`

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
- [x] Images referenced by pending/processing/uploading video jobs are protected.
- [x] Completed/failed old jobs do not permanently block retention.
- [x] Tests cover retention/video race cases.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/integration/test_retention.py -q`

**Dependencies:** Task 7

**Files likely touched:**
- `server/src/timelapse/services/retention.py`
- `server/tests/integration/test_retention.py`

**Estimated scope:** Small

---

## Task 12: Add camera command schema and voice playback preference

**Description:** Add durable camera commands and store each Telegram principal's selected voice playback camera.

**Acceptance criteria:**
- [x] `camera_commands` table exists.
- [x] `telegram_principals.voice_playback_camera_id` exists.
- [x] Pending command index supports camera polling.
- [x] Schema tests are updated.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/unit/test_schema_metadata.py -q`

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
- [x] `/speakcamera` shows current target and available enabled cameras.
- [x] `/speakcamera <camera>` validates and saves target camera.
- [x] Unauthorized users get generic denial.
- [x] Help text includes the new command.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/integration/test_telegram_commands.py -q`

**Dependencies:** Task 12, Task 3 or Task 4 depending on implementation order

**Files likely touched:**
- `server/src/timelapse/bot/application.py`
- `server/src/timelapse/bot/commands.py`
- `server/src/timelapse/services/voice_playback_preferences.py`
- `server/tests/integration/test_telegram_commands.py`

**Estimated scope:** Medium

---

## Task 14: Add Telegram voice-note handler

**Description:** Handle authorized Telegram voice notes by queuing durable preparation work; the worker downloads/normalizes audio and creates a playable command for the user's configured camera.

**Acceptance criteria:**
- [x] Voice notes require configured target camera.
- [x] Duration and file size limits are enforced.
- [x] Telegram file download errors produce safe user-facing messages.
- [x] Audio is normalized with `ffmpeg` to a format playable by `termux-media-player`.
- [x] Command is queued with expiry and audit metadata.
- [x] Generated audio artifacts are deleted after command success or failure, retaining only metadata.
- [x] No raw Telegram file URLs or tokens are logged.

**Verification:**
- [x] Focused tests for limit/target-camera decisions.
- [x] Integration tests for command creation.

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
- [x] Camera credentials authorize only matching camera commands.
- [x] Expired commands are not delivered.
- [x] Media download verifies command ownership and status.
- [x] Result endpoint accepts stable command states/error codes.
- [x] Filesystem paths are never exposed.

**Verification:**
- [x] `cd server && ../.venv/bin/pytest tests/integration/test_camera_commands.py -q`

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
- [x] Command loop runs alongside capture/upload/heartbeat without blocking captures.
- [x] Downloaded audio SHA-256 and size are verified.
- [x] Playback uses `termux-media-player play <downloaded-audio-file>` with subprocess arguments, not shell interpolation.
- [x] Playback success/failure is reported.
- [x] Temp audio files are removed immediately after success or failure.
- [x] Credentials and media paths are not logged.

**Verification:**
- [x] `PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q`

**Dependencies:** Task 15

**Files likely touched:**
- `camera-agent/src/camera_agent/configuration.py`
- `camera-agent/src/camera_agent/main.py`
- `camera-agent/src/camera_agent/commands.py`
- `camera-agent/tests/`
- `camera-agent/config.example.json`

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

**Description:** Update project and operator docs to reflect webhook bot, daily videos, voice playback, new services, and acceptance steps.

**Acceptance criteria:**
- [ ] README describes new commands.
- [ ] Architecture doc reflects webhook, video jobs, and command queue.
- [ ] Implemented features doc is updated.
- [ ] Operator docs include webhook troubleshooting, voice playback setup, and daily video acceptance.

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
- [ ] Real-environment acceptance checklist is documented.

**Verification:**
- [ ] `cd server && ../.venv/bin/pytest tests/unit -q`
- [ ] `PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q`
- [ ] `.venv/bin/ruff check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests`
- [ ] `.venv/bin/ruff format --check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests`

**Dependencies:** All implementation tasks

**Files likely touched:**
- `docs/operator/acceptance-coverage.md`
- `docs/operator/soak-test-report.md`

**Estimated scope:** Medium
