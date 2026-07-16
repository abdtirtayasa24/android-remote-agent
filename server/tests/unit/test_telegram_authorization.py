from __future__ import annotations

from timelapse.bot.authorization import (
    ExistingTelegramPrincipal,
    TelegramAuthorizationDecision,
    TelegramAuthorizationRequest,
    decide_telegram_authorization,
)


def test_unauthorized_user_receives_generic_denial() -> None:
    decision = decide_telegram_authorization(
        TelegramAuthorizationRequest(
            telegram_user_id=111,
            telegram_chat_id=222,
            display_name="Unknown",
        ),
        existing_principal=None,
        admin_user_id=999,
    )

    assert decision == TelegramAuthorizationDecision(
        authorized=False,
        role=None,
        should_bootstrap_admin=False,
        denial_message="Unauthorized.",
    )


def test_admin_user_id_bootstraps_without_chat_id_env_var() -> None:
    decision = decide_telegram_authorization(
        TelegramAuthorizationRequest(
            telegram_user_id=999,
            telegram_chat_id=222,
            display_name="Admin",
        ),
        existing_principal=None,
        admin_user_id=999,
    )

    assert decision.authorized is True
    assert decision.role == "administrator"
    assert decision.should_bootstrap_admin is True


def test_existing_enabled_user_is_authorized() -> None:
    decision = decide_telegram_authorization(
        TelegramAuthorizationRequest(
            telegram_user_id=111,
            telegram_chat_id=222,
            display_name="Viewer",
        ),
        existing_principal=ExistingTelegramPrincipal(
            role="viewer",
            enabled=True,
        ),
        admin_user_id=999,
    )

    assert decision.authorized is True
    assert decision.role == "viewer"
    assert decision.should_bootstrap_admin is False


def test_disabled_user_is_denied_without_camera_details() -> None:
    decision = decide_telegram_authorization(
        TelegramAuthorizationRequest(
            telegram_user_id=111,
            telegram_chat_id=222,
            display_name="Disabled",
        ),
        existing_principal=ExistingTelegramPrincipal(
            role="administrator",
            enabled=False,
        ),
        admin_user_id=999,
    )

    assert decision.authorized is False
    assert decision.denial_message == "Unauthorized."
