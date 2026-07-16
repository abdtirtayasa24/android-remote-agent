from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from telegram import Update
from telegram.ext import Application

from timelapse.configuration import Settings, get_settings

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


@router.post(
    "/webhook",
    include_in_schema=False,
    status_code=status.HTTP_200_OK,
)
async def receive_telegram_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    payload: Annotated[dict[str, Any], Body()],
    secret_token: Annotated[
        str | None,
        Header(alias="X-Telegram-Bot-Api-Secret-Token"),
    ] = None,
) -> dict[str, str]:
    configured_secret = settings.telegram_webhook_secret

    if (
        configured_secret is None
        or secret_token is None
        or not hmac.compare_digest(
            secret_token,
            configured_secret.get_secret_value(),
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="telegram_webhook_forbidden",
        )

    telegram_application: Application | None = getattr(
        request.app.state,
        "telegram_application",
        None,
    )

    if telegram_application is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telegram_webhook_unavailable",
        )

    update = Update.de_json(payload, telegram_application.bot)
    await telegram_application.update_queue.put(update)
    return {"status": "accepted"}
