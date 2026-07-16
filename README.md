# Android Time-Lapse Security Camera

A self-hosted security camera system that turns an Android phone into a scheduled still-image camera. The Android agent runs in Termux, captures JPEG images, queues uploads when offline, and sends images/heartbeats to a FastAPI server on an Ubuntu VPS.

For system design details, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Repository Structure

```text
.
├── camera-agent/              # Termux Python camera agent
│   ├── config.example.json
│   ├── requirements.txt
│   ├── scripts/
│   ├── src/camera_agent/
│   └── tests/
├── server/                    # FastAPI server, workers, bot, models, migrations
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
├── tasks/                     # Implementation plan and task list
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
cp config.example.json config.json
chmod 600 config.json
./scripts/camera-self-test.sh
PYTHONPATH=src python -m camera_agent.validation
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

Production runs on Ubuntu with native systemd services. Configure `infrastructure/.env`, then run:

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
