from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from timelapse.api.heartbeats import router as heartbeats_router
from timelapse.api.images import router as images_router
from timelapse.api.telegram import router as telegram_router
from timelapse.bot.application import (
    build_application,
    start_webhook_application,
    stop_webhook_application,
)
from timelapse.configuration import get_settings
from timelapse.database import close_database
from timelapse.services.image_files import ensure_storage_layout


@asynccontextmanager
async def lifespan(
    app: FastAPI,
) -> AsyncIterator[None]:
    settings = get_settings()
    telegram_application = None
    telegram_started = False

    try:
        await asyncio.to_thread(
            ensure_storage_layout,
            settings,
        )

        if settings.telegram_bot_token is not None:
            if settings.public_domain is None or settings.telegram_webhook_secret is None:
                raise RuntimeError(
                    "PUBLIC_DOMAIN and TELEGRAM_WEBHOOK_SECRET are required when "
                    "TELEGRAM_BOT_TOKEN is configured"
                )

            telegram_application = build_application(
                bot_token=settings.telegram_bot_token.get_secret_value(),
            )
            await start_webhook_application(
                application=telegram_application,
                webhook_url=(f"https://{settings.public_domain}/api/v1/telegram/webhook"),
                webhook_secret=settings.telegram_webhook_secret.get_secret_value(),
            )
            telegram_started = True
            app.state.telegram_application = telegram_application

        yield
    finally:
        if telegram_started and telegram_application is not None:
            await stop_webhook_application(application=telegram_application)
            del app.state.telegram_application

        await close_database()


app = FastAPI(
    title="Time-lapse Camera API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.include_router(images_router)
app.include_router(heartbeats_router)
app.include_router(telegram_router)


@app.get(
    "/health/live",
    include_in_schema=False,
    status_code=200,
)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}
