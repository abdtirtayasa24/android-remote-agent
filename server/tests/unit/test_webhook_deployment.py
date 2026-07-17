from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_systemd_target_runs_telegram_through_api_service() -> None:
    target = read("infrastructure/systemd/timelapse-camera.target")

    assert "timelapse-api.service" in target
    assert "timelapse-worker.service" in target
    assert "timelapse-bot.service" not in target


def test_deployment_retires_bot_service_without_reinstalling_it() -> None:
    deployment = read("infrastructure/deploy-systemd.sh")
    verification = read("infrastructure/verify-foundation.sh")
    installed_units = deployment.split("for unit_name in ", maxsplit=1)[1].split(
        "do", maxsplit=1
    )[0]

    assert "systemctl disable --now timelapse-bot.service" in deployment
    assert "timelapse-bot.service" not in installed_units
    assert "timelapse-bot.service" not in verification


def test_nginx_proxies_only_post_requests_to_telegram_webhook() -> None:
    nginx = read("infrastructure/nginx/timelapse-camera.conf.template")

    assert "location = /api/v1/telegram/webhook" in nginx
    assert "limit_except POST" in nginx
    assert "proxy_pass http://127.0.0.1:${API_PORT};" in nginx


def test_nginx_proxies_camera_command_endpoints_with_narrow_methods() -> None:
    nginx = read("infrastructure/nginx/timelapse-camera.conf.template")

    assert "/commands/next$" in nginx
    assert "/commands/[0-9a-f-]+/media$" in nginx
    assert "/commands/[0-9a-f-]+/result$" in nginx
    assert nginx.count("limit_except GET") >= 3
    assert nginx.count("limit_except POST") >= 3


def test_environment_template_requires_webhook_secret() -> None:
    environment = read("infrastructure/environment.example")

    assert "TELEGRAM_WEBHOOK_SECRET=" in environment
