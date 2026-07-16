# AGENTS.md

Guidelines for AI agents and contributors working in this repository.

This file is a stable project rulebook. Do not change it unless the change is necessary and explicitly approved by a human maintainer.

## Project Principles

- Preserve the MVP scope: Android still-image time-lapse security camera, VPS ingestion/storage, motion/health workers, and Telegram operations.
- Make small, reviewable, reversible changes.
- Understand the relevant code path before editing.
- Prefer behavior-preserving cleanup only when it directly supports the task.
- Keep runtime behavior out of request handlers when it belongs in workers.
- Keep all timestamps stored and processed as UTC unless a user-facing Telegram date requires Asia/Jakarta conversion.

## Implementation Order

Follow the current task plan unless a human changes priority:

1. Stabilize quality gates.
2. Camera health worker and alerts.
3. Motion detection worker and event grouping.
4. Telegram authorization and retrieval commands.
5. Export generation and delivery.
6. Retention, disk protection, and reconciliation.
7. QA, operations docs, and soak-test evidence.

When implementing behavior, use TDD:

1. Add or update a failing test for the expected behavior.
2. Implement the smallest change that passes.
3. Refactor only after tests are green.
4. Run the focused tests and relevant quality checks.

## Code Style

- Python only for application code in this repository.
- Follow Ruff configuration in `server/pyproject.toml`.
- Keep imports sorted and formatted by Ruff.
- Use type hints for new Python code.
- Prefer pure functions for algorithms and classification logic.
- Use explicit, stable condition/error codes for worker decisions and API errors.
- Keep tests descriptive and state-based; avoid asserting implementation call order unless testing an external boundary.

## Project Rules

- Server runtime code lives under `server/src/timelapse`.
- Android agent code lives under `camera-agent/src/camera_agent`.
- Server database changes require SQLAlchemy model updates, Alembic migrations, and tests.
- Integration tests that need PostgreSQL must use `TEST_DATABASE_URL`; never point tests at production data.
- Production deployment uses native systemd services and a shared Python virtual environment, not Docker Compose.
- The database target is Neon PostgreSQL with pooled runtime and direct migration URLs.
- Telegram operational messages must be English only.
- Initial Telegram administrator access uses `TELEGRAM_ADMIN_USER_ID`; do not require `TELEGRAM_ADMIN_CHAT_ID`.
- Use `alert_states` for persistent alert deduplication.
- Heartbeat daily aggregation and detailed heartbeat expiry are required Milestone 5 scope.

## Security Do's and Don'ts

Do:

- Keep credentials, database URLs, bot tokens, peppers, and camera secrets out of Git.
- Use HMAC/constant-time comparisons for camera credential validation.
- Redact secrets in logs and summaries.
- Scope camera data by camera identity and Telegram access rules.
- Treat client filenames and Telegram/user input as untrusted.

Don't:

- Commit `.env`, camera credentials, generated ZIPs, uploaded images, or local queue data.
- Log Authorization headers, plaintext camera credentials, or Telegram bot tokens.
- Run destructive database commands unless the user explicitly approves them.
- Add public network listeners for FastAPI or PostgreSQL.
- Put long-running work in FastAPI request handlers.

## Verification Expectations

Before reporting completion, run the narrowest relevant checks. Common commands:

```bash
PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q
cd server && ../.venv/bin/pytest tests/unit -q
.venv/bin/ruff check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests
.venv/bin/ruff format --check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests
```

If a check cannot run because of missing services, credentials, or device access, report the exact blocker and the command to run later.
