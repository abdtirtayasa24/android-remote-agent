from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import TelegramPrincipal


async def load_telegram_recipient_chat_ids(
    *,
    session: AsyncSession,
    admin_user_id: int | None,
) -> Sequence[int]:
    principals = (
        await session.scalars(
            select(TelegramPrincipal).order_by(TelegramPrincipal.telegram_user_id)
        )
    ).all()
    chat_ids = [principal.telegram_chat_id for principal in principals if principal.enabled]
    known_user_ids = {principal.telegram_user_id for principal in principals}

    if admin_user_id is not None and admin_user_id not in known_user_ids:
        chat_ids.append(admin_user_id)

    return tuple(dict.fromkeys(chat_ids))
