# Android Time-Lapse Security Camera

A self-hosted security camera system that turns an Android phone into a scheduled still-image camera. The Android agent runs in Termux, captures JPEG images, queues uploads when offline, and sends images/heartbeats to a FastAPI server on an Ubuntu VPS. The worker generates an MP4 from each completed Asia/Jakarta day and sends it automatically through Telegram.

For system design details, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). For the current feature inventory, see [`docs/IMPLEMENTED.md`](docs/IMPLEMENTED.md).

Operator documentation:

- [`docs/operator/server-installation.md`](docs/operator/server-installation.md)
- [`docs/operator/android-installation.md`](docs/operator/android-installation.md)
- [`docs/operator/credential-rotation.md`](docs/operator/credential-rotation.md)
- [`docs/operator/operations.md`](docs/operator/operations.md)
- [`docs/operator/incident-recovery.md`](docs/operator/incident-recovery.md)
- [`docs/operator/acceptance-coverage.md`](docs/operator/acceptance-coverage.md)
- [`docs/operator/soak-test-report.md`](docs/operator/soak-test-report.md)

## Repository Structure

```text
.
├── camera-agent/              # Termux Python camera agent
│   ├── config.example.json
│   ├── requirements.txt
│   ├── scripts/
│   ├── src/camera_agent/
│   └── tests/
├── server/                    # FastAPI/webhook server, workers, models, migrations
│   ├── alembic.ini
│   ├── migrations/
│   ├── pyproject.toml
│   ├── src/timelapse/
│   └── tests/
├── infrastructure/            # VPS bootstrap, systemd, Nginx, deployment scripts
│   ├── environment.example
│   ├── nginx/
│   └── systemd/
├── docs/                      # Project documentation
└── AGENTS.md                  # Contributor and AI-agent rules
```

## Local Server Setup

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Create an environment file:

```bash
cp ../infrastructure/environment.example ../infrastructure/.env
```

Configure at least:

```env
ENVIRONMENT=development
DATABASE_URL=postgresql://user:password@pooled-host/neondb
DATABASE_MIGRATION_URL=postgresql://user:password@direct-host/neondb
STORAGE_ROOT=/tmp/timelapse
CAMERA_TOKEN_PEPPER=replace-with-at-least-32-random-characters
REQUIRE_HTTPS=false
# When TELEGRAM_BOT_TOKEN is set, also configure TELEGRAM_WEBHOOK_SECRET.
```

Load the environment and run migrations:

```bash
set -a
source ../infrastructure/.env
set +a

alembic -c alembic.ini upgrade head
```

Run the API locally:

```bash
uvicorn timelapse.api.main:app --host 127.0.0.1 --port 8100 --reload
curl http://127.0.0.1:8100/health/live
```

Expected response:

```json
{"status":"ok"}
```

## Android Agent Setup

Inside Termux:

```bash
cd camera-agent
./scripts/install-termux.sh
# Edit $HOME/timelapse/config.json, then inspect camera IDs:
$HOME/timelapse/bin/camera-self-test.sh info
# Test one candidate camera ID:
$HOME/timelapse/bin/camera-self-test.sh once 0
```

Install boot integration when the phone setup is ready:

```bash
./scripts/install-boot-script.sh
```

## Common Commands

```bash
# Camera-agent tests
PYTHONPATH=camera-agent/src .venv/bin/pytest camera-agent/tests -q

# Server unit tests
cd server && ../.venv/bin/pytest tests/unit -q

# Shared lint and format checks
.venv/bin/ruff check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests
.venv/bin/ruff format --check --config server/pyproject.toml server/src server/tests camera-agent/src camera-agent/tests
```

Integration tests require a dedicated test database:

```bash
export TEST_DATABASE_URL=postgresql://user:password@host/timelapse_test
export TEST_DATABASE_MIGRATION_URL="$TEST_DATABASE_URL"
cd server && ../.venv/bin/pytest tests/integration -q
```

## Dashboard Frontend Build Strategy

The Telegram Mini App dashboard is allowed to use React, TypeScript, and Tailwind under `dashboard/`. The dashboard is compiled to static assets and served by Nginx; it does not add a Node.js runtime service.

Before generating or updating `dashboard/package-lock.json`, ensure npm uses the public npm registry:

```bash
npm config get registry
npm config set registry https://registry.npmjs.org/
```

The dashboard package should commit `dashboard/.npmrc` with:

```ini
registry=https://registry.npmjs.org/
```

Production bootstrap installs Node.js 22 LTS and npm for dashboard builds. Deployment runs `npm ci` and `npm run build` when `dashboard/package.json` is present, then publishes `dashboard/dist` to `/var/www/android-remote/dashboard` for Nginx to serve at `/dashboard/`.

## Camera Credentials

After migrations and environment setup:

```bash
timelapse-credentials register-camera --slug front-door --display-name "Front Door"
timelapse-credentials issue --camera front-door --valid-hours 24
timelapse-credentials list --camera front-door
timelapse-credentials revoke --token-id <token-id>
```

The plaintext camera credential is shown only when created. Store it securely.

## Production Deployment

Production runs on Ubuntu with native systemd services. Telegram webhook handling runs inside `timelapse-api.service`; there is no separate bot service. When Telegram is enabled, API startup registers the webhook automatically and fails if registration fails. Configure `infrastructure/.env`, then run:

```bash
sudo ./infrastructure/bootstrap-ubuntu.sh
sudo ./infrastructure/deploy-systemd.sh
sudo ./infrastructure/verify-foundation.sh
```

Use the helper for camera administration on the VPS:

```bash
sudo ./infrastructure/camera-admin.sh register-camera --slug front-door --display-name "Front Door"
```

## Security Notes

- Do not commit `.env`, camera credentials, bot tokens, database URLs, generated exports, or uploaded images.
- Keep Android `config.json` mode `0600`.
- Production uploads must use HTTPS.
- Client filenames are ignored by the server.

## License

MIT
