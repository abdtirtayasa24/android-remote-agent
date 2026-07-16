from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from timelapse.models.entities import TelegramPrincipal

GENERIC_DENIAL_MESSAGE = "Unauthorized."


@dataclass(frozen=True)
class TelegramAuthorizationRequest:
    telegram_user_id: int
    telegram_chat_id: int
    display_name: str | None = None


@dataclass(frozen=True)
class ExistingTelegramPrincipal:
    role: str
    enabled: bool


@dataclass(frozen=True)
class TelegramAuthorizationDecision:
    authorized: bool
    role: str | None
    should_bootstrap_admin: bool
    denial_message: str = GENERIC_DENIAL_MESSAGE


@dataclass(frozen=True)
class AuthorizedTelegramUser:
    telegram_user_id: int
    telegram_chat_id: int
    role: str


def decide_telegram_authorization(
    request: TelegramAuthorizationRequest,
    *,
    existing_principal: ExistingTelegramPrincipal | None,
    admin_user_id: int | None,
) -> TelegramAuthorizationDecision:
    if existing_principal is not None:
        if existing_principal.enabled:
            return TelegramAuthorizationDecision(
                authorized=True,
                role=existing_principal.role,
                should_bootstrap_admin=False,
            )

        return TelegramAuthorizationDecision(
            authorized=False,
            role=None,
            should_bootstrap_admin=False,
        )

    if admin_user_id is not None and request.telegram_user_id == admin_user_id:
        return TelegramAuthorizationDecision(
            authorized=True,
            role="administrator",
            should_bootstrap_admin=True,
        )

    return TelegramAuthorizationDecision(
        authorized=False,
        role=None,
        should_bootstrap_admin=False,
    )


async def authorize_telegram_user(
    *,
    session: AsyncSession,
    request: TelegramAuthorizationRequest,
    admin_user_id: int | None,
) -> AuthorizedTelegramUser | None:
    principal = await session.scalar(
        select(TelegramPrincipal)
        .where(TelegramPrincipal.telegram_user_id == request.telegram_user_id)
        .order_by(TelegramPrincipal.created_at.desc())
        .limit(1)
    )
    existing = None

    if principal is not None:
        existing = ExistingTelegramPrincipal(
            role=principal.role,
            enabled=principal.enabled,
        )

    decision = decide_telegram_authorization(
        request,
        existing_principal=existing,
        admin_user_id=admin_user_id,
    )

    if not decision.authorized or decision.role is None:
        return None

    if decision.should_bootstrap_admin:
        principal = TelegramPrincipal(
            telegram_user_id=request.telegram_user_id,
            telegram_chat_id=request.telegram_chat_id,
            display_name=request.display_name,
            role=decision.role,
        )
        session.add(principal)
    elif principal is not None and principal.telegram_chat_id != request.telegram_chat_id:
        principal.telegram_chat_id = request.telegram_chat_id

    return AuthorizedTelegramUser(
        telegram_user_id=request.telegram_user_id,
        telegram_chat_id=request.telegram_chat_id,
        role=decision.role,
    )
