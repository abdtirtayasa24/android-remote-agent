# Server Installation

This guide installs the VPS side of the Android Time-Lapse Security Camera on Ubuntu Server 24.04 using native systemd services and Neon PostgreSQL.

## Prerequisites

- Ubuntu Server 24.04 with SSH access.
- A DNS record for `PUBLIC_DOMAIN` pointing at the VPS.
- Neon PostgreSQL URLs:
  - `DATABASE_URL`: pooled runtime URL.
  - `DATABASE_MIGRATION_URL`: direct migration URL.
- A random `CAMERA_TOKEN_PEPPER` with at least 32 characters.
- Optional Telegram bot token and `TELEGRAM_ADMIN_USER_ID` for operations commands.

Do not use Docker Compose for production deployment in this project.

## 1. Bootstrap Ubuntu

From the repository root on the VPS:

```sh
sudo SSH_PORT=22 ./infrastructure/bootstrap-ubuntu.sh
```

This installs Python 3.12, Nginx, Certbot, PostgreSQL client tools, OpenCV runtime packages, `ffmpeg`, Node.js 22 LTS/npm for dashboard builds, creates `/srv/timelapse`, prepares `/var/www/android-remote/dashboard`, and enables Nginx.

If you want the bootstrap script to enable UFW after adding SSH/HTTP/HTTPS rules:

```sh
sudo ENABLE_UFW=1 SSH_PORT=22 ./infrastructure/bootstrap-ubuntu.sh
```

## 2. Configure environment

Create the production environment file:

```sh
cp infrastructure/environment.example infrastructure/.env
chmod 600 infrastructure/.env
nano infrastructure/.env
```

Set at least:

```env
ENVIRONMENT=production
PUBLIC_DOMAIN=camera.example.com
LETSENCRYPT_EMAIL=admin@example.com
API_PORT=8100
DATABASE_URL=postgresql://...-pooler.../neondb
DATABASE_MIGRATION_URL=postgresql://...direct.../neondb
STORAGE_ROOT=/srv/timelapse
STORAGE_HARD_MIN_FREE_BYTES=536870912
STORAGE_SEVERE_MIN_FREE_BYTES=1073741824
CAMERA_TOKEN_PEPPER=replace-with-a-32-byte-random-value
TELEGRAM_BOT_TOKEN=123456:replace-with-real-token
TELEGRAM_WEBHOOK_SECRET=replace-with-random-letters-digits-underscore-hyphen
TELEGRAM_ADMIN_USER_ID=123456789
```

`TELEGRAM_ADMIN_USER_ID` bootstraps the first administrator. Do not configure or require `TELEGRAM_ADMIN_CHAT_ID`. `TELEGRAM_WEBHOOK_SECRET` is required when the bot token is configured and must contain 1–256 letters, digits, underscores, or hyphens.

## 3. Deploy systemd services

Run:

```sh
sudo ./infrastructure/deploy-systemd.sh
```

The deployment script validates Neon URL roles, installs the server package into `/opt/android-remote/.venv`, builds the React/Tailwind dashboard when `dashboard/package.json` is present, publishes dashboard static assets to `/var/www/android-remote/dashboard`, runs Alembic migrations, installs systemd units, configures Nginx, obtains/uses Let's Encrypt certificates, and starts:

- `timelapse-api.service` (FastAPI and Telegram webhook)
- `timelapse-worker.service`
- `timelapse-camera.target`

During API startup, the application registers `https://PUBLIC_DOMAIN/api/v1/telegram/webhook` with Telegram. Registration failure fails API startup and therefore fails deployment liveness verification.

Dashboard builds use npm with a committed lockfile. Before generating or updating `dashboard/package-lock.json`, confirm the npm registry points to the public registry:

```sh
npm config get registry
npm config set registry https://registry.npmjs.org/
```

The dashboard package should include `dashboard/.npmrc` containing `registry=https://registry.npmjs.org/`. Deployment runs `npm ci` and `npm run build` from the release copy, then Nginx serves the compiled Mini App at `/dashboard/`.

## 4. Verify foundation

Run:

```sh
sudo ./infrastructure/verify-foundation.sh
```

Then check service status:

```sh
systemctl status timelapse-api.service timelapse-worker.service --no-pager
curl -fsS https://camera.example.com/health/live
```

## 5. Register a camera without source changes

Use the installed helper on the VPS:

```sh
sudo ./infrastructure/camera-admin.sh register-camera \
  --slug front-door \
  --display-name "Front Door"

sudo ./infrastructure/camera-admin.sh issue \
  --camera front-door \
  --valid-hours 8760
```

Copy the plaintext credential into the Android device's `$HOME/timelapse/config.json`. The plaintext credential is only printed once.

## 6. Android device configuration

On the Android phone, edit:

```sh
nano "$HOME/timelapse/config.json"
```

Set:

```json
{
  "api_base_url": "https://camera.example.com",
  "camera_slug": "front-door",
  "camera_credential": "cam_...",
  "camera_id": 0
}
```

Restart the agent:

```sh
$HOME/timelapse/bin/start-agent.sh
```

## 7. Telegram bootstrap

Start a chat with the bot from the Telegram account whose user ID equals `TELEGRAM_ADMIN_USER_ID`, then send:

```text
/help
/status
```

Telegram command timestamps are displayed as Asia/Jakarta time. The `/images` command accepts Asia/Jakarta timestamps and the backend stores/query ranges as UTC.

## 8. Deployment rollback

If a migration fails, `deploy-systemd.sh` does not switch `/opt/android-remote/current` to the new release. If a runtime issue appears after deployment, inspect recent releases:

```sh
ls -1 /opt/android-remote/releases | tail
readlink -f /opt/android-remote/current
```

Rollback by repointing `current` to the previous release and restarting services:

```sh
sudo ln -sfnT /opt/android-remote/releases/<previous-release> /opt/android-remote/current
sudo systemctl restart timelapse-api.service timelapse-worker.service
```
