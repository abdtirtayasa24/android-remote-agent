# Android Time-Lapse Security Camera

> A self-hosted time-lapse security camera system that uses an Android phone as the camera and a Python server for secure image ingestion, storage, processing, and monitoring.

The Android device runs a lightweight Python agent inside Termux. It periodically captures JPEG images and sends them to a FastAPI server over HTTPS. The server authenticates each camera, validates uploaded images, stores image metadata in PostgreSQL-compatible storage, and writes image files to the VPS filesystem.

## Architecture

```text
Android phone
    │
    │ HTTPS multipart upload
    ▼
Nginx
    │
    │ Reverse proxy to localhost
    ▼
FastAPI API
    ├── SQLAlchemy + asyncpg ──► Neon PostgreSQL
    └── Image files ───────────► /srv/timelapse
```

Production services are managed by systemd:

`timelapse-migrate.service`
`timelapse-api.service`
`timelapse-worker.service`
`timelapse-bot.service`
`timelapse-camera.target`

## Technology stack

| Area | Stacks |
| --- | --- |
| Android camera agent | Android 9+, Termux, Python, Pillow, Android camera commands through `termux-camera-photo` |
| Server | Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2, asyncpg, Alembic, Pydantic Settings, Pillow, PostgreSQL advisory transaction locks |
| Infrastructure | Ubuntu VPS, Native systemd services, Nginx, Certbot, Neon PostgreSQL, UFW firewall |
| Development tooling | pytest, pytest-asyncio, HTTPX, Ruff |

## Repository structure

```text
.
├── android-remote-agent/
│   ├── requirements.txt
│   ├── config.example.json
│   ├── scripts/
│   ├── src/camera_agent/
│   └── tests/
│
├── server/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── migrations/
│   ├── src/timelapse/
│   └── tests/
│
├── infrastructure/
│   ├── systemd/
│   ├── nginx/
│   ├── bootstrap-ubuntu.sh
│   ├── deploy-systemd.sh
│   ├── verify-foundation.sh
│   ├── camera-admin.sh
│   └── environment.example
│
└── docs/
```

## Local server setup

### 1. Clone the repository

```bash
git clone https://github.com/abdtirtayasa24/android-remote-agent.git
cd android-remote-agent
```

### 2. Create the environment

```bash
cd server

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 3. Configure environment variables

```bash
cp ../infrastructure/environment.example ../infrastructure/.env

# Minimum local configuration:
ENVIRONMENT=development
LOG_LEVEL=INFO
DATABASE_URL="postgresql://user:password@pooled-host/neondb"
DATABASE_MIGRATION_URL="postgresql://user:password@direct-host/neondb"
DATABASE_POOL_SIZE=3
DATABASE_MAX_OVERFLOW=2
DATABASE_CONNECT_TIMEOUT_SECONDS=15
STORAGE_ROOT=/tmp/timelapse
CAMERA_TOKEN_PEPPER=replace-with-at-least-32-random-characters 
REQUIRE_HTTPS=false
```

> Use the Neon pooled endpoint for `DATABASE_URL` and the direct endpoint for `DATABASE_MIGRATION_URL`.

```bash
# Load the environment:
set -a
source ../infrastructure/.env
set +a
```

### 4. Run migrations

```bash
alembic -c alembic.ini upgrade head
alembic -c alembic.ini current
```

### Build

```bash
cd server
python -m pip install -e ".[dev]"

# To build distributable packages:
python -m pip install build
python -m build
```

Artifacts are written to `server/dist`.

### Linting and formatting

```bash
# From server/:
ruff check src tests
ruff format --check src tests

# Apply fixes:
ruff check --fix src tests
ruff format src tests

# Check both Python applications from the repository root:
ruff check \
    --config server/pyproject.toml \
    server/src server/tests \
    camera-agent/src camera-agent/tests
    
ruff format \
    --check \
    --config server/pyproject.toml \
    server/src server/tests \
    camera-agent/src camera-agent/tests
```

### Testing

```bash
# Unit tests
cd server
pytest tests/unit -v

# Integration tests
# Use a dedicated database ending in _test:
export TEST_DATABASE_URL="postgresql://timelapse_test:test-password@127.0.0.1:5432/timelapse_test"
export TEST_DATABASE_MIGRATION_URL="$TEST_DATABASE_URL"

pytest tests/integration -v

# Run the complete suite:
pytest -v
```

### Run locally

```bash
# Load the environment and start Uvicorn:
cd server

set -a
source ../infrastructure/.env
set +a

uvicorn timelapse.api.main:app \
    --host 127.0.0.1 \
    --port 8100 \
    --reload

# Check livenes:
curl http://127.0.0.1:8100/health/live

# Expected response:
{"status":"ok"}

# Local HTTP development requires:
REQUIRE_HTTPS=false
```
Production should use `REQUIRE_HTTPS=true`.

### Camera credentials

```bash
# Register a camera:
timelapse-credentials \
    register-camera \
    --slug front-door \
    --display-name "Front Door"

# Issue another credential:
timelapse-credentials \
    issue \
    --camera front-door \
    --valid-hours 24

# List credentials:
timelapse-credentials list --camera front-door

# Revoke a credential:
timelapse-credentials revoke --token-id <token-id>
```

> The plaintext credential is displayed only when created. Store it securely.

```bash
# On the VPS. use:
sudo ./infrastructure/camera-admin.sh \
    register-camera \
    --slug front-door \
    --display-name "Front Door"
```

### Android camera setup

Install Termux and the matching Termux application.

```bash
# Inside Termux:
cd camera-agent
./scripts/install-termux.sh

cp config.example.json config.json
./scripts/camera-self-test.sh

# Run camera validation:
PYTHONPATH=src python -m camera_agent.validation
```

### Production deployment

```bash
# Configure production
cp infrastructure/environment.example infrastructure/.env

# Configure at least:
ENVIRONMENT=production
PUBLIC_DOMAIN=camera.example.com 
LETSENCRYPT_EMAIL=admin@example.com
API_PORT=8100
DATABASE_URL='postgresql://user:password@pooled-host/neondb' 
DATABASE_MIGRATION_URL='postgresql://user:password@direct-host/neondb'
STORAGE_ROOT=/srv/timelapse 
CAMERA_TOKEN_PEPPER=replace-with-a-long-random-value 
REQUIRE_HTTPS=true

# Bootstrap the VPS
sudo ./infrastructure/bootstrap-ubuntu.sh

# Deploy
sudo ./infrastructure/deploy-systemd.sh

# Verify
sudo ./infrastructure/verify-foundation.sh

# Check services:
sudo systemctl status \
    timelapse-migrate.service \
    timelapse-api.service \
    timelapse-worker.service \
    timelapse-bot.service \
    --no-pager

# Inspect logs:
sudo journalctl -u timelapse-api.service --follow

sudo journalctl \
    -u timelapse-migrate.service \
    --since "10 minutes ago" \
    --no-pager \
    --output=cat
```
