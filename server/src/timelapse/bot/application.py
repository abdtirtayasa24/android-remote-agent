from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from timelapse.bot.authorization import (
    GENERIC_DENIAL_MESSAGE,
    AuthorizedTelegramUser,
    TelegramAuthorizationRequest,
    authorize_telegram_user,
)
from timelapse.bot.commands import (
    handle_cancel_command,
    handle_exports_command,
    handle_help_command,
    handle_images_command,
    handle_latest_command,
    handle_status_command,
)
from timelapse.configuration import get_settings
from timelapse.database import session_scope

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotSender:
    context: ContextTypes.DEFAULT_TYPE

    async def send_message(self, *, chat_id: int, text: str) -> int | None:
        message = await self.context.bot.send_message(chat_id=chat_id, text=text)
        return message.message_id

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str | None = None,
    ) -> int | None:
        with photo_path.open("rb") as photo:
            message = await self.context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
            )
        return message.message_id

    async def send_document(
        self,
        *,
        chat_id: int,
        document_path: Path,
        caption: str | None = None,
    ) -> int | None:
        with document_path.open("rb") as document:
            message = await self.context.bot.send_document(
                chat_id=chat_id,
                document=document,
                caption=caption,
            )
        return message.message_id


async def _authorize_update(
    update: Update,
) -> AuthorizedTelegramUser | None:
    if update.effective_user is None or update.effective_chat is None:
        return None

    settings = get_settings()

    async with session_scope() as session:
        return await authorize_telegram_user(
            session=session,
            request=TelegramAuthorizationRequest(
                telegram_user_id=update.effective_user.id,
                telegram_chat_id=update.effective_chat.id,
                display_name=update.effective_user.full_name,
            ),
            admin_user_id=settings.telegram_admin_user_id,
        )


async def _reply_unauthorized(update: Update) -> None:
    if update.effective_chat is not None:
        await update.effective_chat.send_message(GENERIC_DENIAL_MESSAGE)


async def _help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _authorize_update(update)

    if user is None:
        await _reply_unauthorized(update)
        return

    if update.effective_chat is not None:
        await update.effective_chat.send_message(handle_help_command(role=user.role))


async def _status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _authorize_update(update)

    if user is None:
        await _reply_unauthorized(update)
        return

    async with session_scope() as session:
        text = await handle_status_command(session=session, args=list(context.args))

    if update.effective_chat is not None:
        await update.effective_chat.send_message(text)


async def _latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _authorize_update(update)

    if user is None:
        await _reply_unauthorized(update)
        return

    async with session_scope() as session:
        text = await handle_latest_command(
            session=session,
            args=list(context.args),
            chat_id=user.telegram_chat_id,
            sender=BotSender(context),
        )

    if update.effective_chat is not None and text != "Latest image sent.":
        await update.effective_chat.send_message(text)


async def _images(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _authorize_update(update)

    if user is None:
        await _reply_unauthorized(update)
        return

    async with session_scope() as session:
        text = await handle_images_command(session=session, args=list(context.args), user=user)

    if update.effective_chat is not None:
        await update.effective_chat.send_message(text)


async def _exports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _authorize_update(update)

    if user is None:
        await _reply_unauthorized(update)
        return

    async with session_scope() as session:
        text = await handle_exports_command(session=session, user=user)

    if update.effective_chat is not None:
        await update.effective_chat.send_message(text)


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _authorize_update(update)

    if user is None:
        await _reply_unauthorized(update)
        return

    try:
        async with session_scope() as session:
            text = await handle_cancel_command(
                session=session,
                args=list(context.args),
                user=user,
            )
    except (PermissionError, ValueError) as error:
        text = str(error)

    if update.effective_chat is not None:
        await update.effective_chat.send_message(text)


def build_application(*, bot_token: str | None = None) -> Application:
    if bot_token is None:
        settings = get_settings()

        if settings.telegram_bot_token is None:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

        bot_token = settings.telegram_bot_token.get_secret_value()

    application = Application.builder().token(bot_token).build()
    application.add_handler(CommandHandler("help", _help))
    application.add_handler(CommandHandler("status", _status))
    application.add_handler(CommandHandler("latest", _latest))
    application.add_handler(CommandHandler("images", _images))
    application.add_handler(CommandHandler("exports", _exports))
    application.add_handler(CommandHandler("cancel", _cancel))
    return application


async def start_webhook_application(
    *,
    application: Application,
    webhook_url: str,
    webhook_secret: str,
) -> None:
    await application.initialize()

    try:
        await application.start()
        webhook_configured = await application.bot.set_webhook(
            url=webhook_url,
            secret_token=webhook_secret,
            allowed_updates=["message"],
            connect_timeout=10,
            read_timeout=10,
            write_timeout=10,
            pool_timeout=10,
        )

        if not webhook_configured:
            raise RuntimeError("telegram_webhook_setup_failed")
    except Exception as error:
        if application.running:
            await application.stop()
        await application.shutdown()
        LOGGER.warning(
            "telegram_webhook_setup_failed error_type=%s",
            type(error).__name__,
        )
        raise RuntimeError("telegram_webhook_setup_failed") from None


async def stop_webhook_application(*, application: Application) -> None:
    if application.running:
        await application.stop()
    await application.shutdown()
