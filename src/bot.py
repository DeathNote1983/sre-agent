"""Telegram bot handlers, auth, session manager."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.agent import SYSTEM_PROMPT, run_agent
from src.config import AppSettings
from src.grafana_client import GrafanaClient
from src.tools import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class Session:
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_active: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self, idle_seconds: int):
        self._idle = idle_seconds
        self._data: dict[int, Session] = {}

    def get(self, user_id: int) -> Session:
        sess = self._data.get(user_id)
        now = time.time()
        if sess is None or (now - sess.last_active) > self._idle:
            sess = Session(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
            self._data[user_id] = sess
        sess.last_active = now
        return sess

    def reset(self, user_id: int) -> None:
        self._data.pop(user_id, None)


def build_app(settings: AppSettings) -> Application:
    """Tạo Telegram Application với handlers + bot_data."""
    openai_client = AsyncOpenAI(
        api_key=settings.llm_api_key, base_url=settings.llm_base_url
    )
    grafana = GrafanaClient(
        base_url=settings.grafana_url,
        ds_uid=settings.grafana_ds_uid,
        token=settings.grafana_token,
        user=settings.grafana_user,
        password=settings.grafana_password,
    )
    tool_ctx = ToolContext(client=grafana, thresholds=settings.thresholds)
    sessions = SessionStore(idle_seconds=settings.session_idle_minutes * 60)

    app = Application.builder().token(settings.telegram_bot_token).build()

    # Stash dependencies vào bot_data (per-Application, không phải per-update)
    app.bot_data["settings"] = settings
    app.bot_data["openai_client"] = openai_client
    app.bot_data["tool_ctx"] = tool_ctx
    app.bot_data["sessions"] = sessions

    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("reset", on_reset))
    app.add_handler(CommandHandler("help", on_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    return app


def _is_authorized(update: Update, settings: AppSettings) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return settings.whitelist.allows(user.id)


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: AppSettings = context.bot_data["settings"]
    user = update.effective_user
    if not _is_authorized(update, settings):
        logger.warning("Unauthorized /start from user_id=%s", user.id if user else "?")
        await update.message.reply_text(
            "Bạn chưa nằm trong whitelist. Vui lòng liên hệ admin để được cấp quyền."
        )
        return
    await update.message.reply_text(
        "Chào anh 👋\n\n"
        "Tôi là SRE assistant. Anh có thể hỏi tôi về:\n"
        "• Tình trạng 1 host theo IP, vd: `10.1.2.3 thế nào?`\n"
        "• Tình trạng 1 cluster, vd: `cluster pxc-prod-1 còn ổn không?`\n"
        "• Cluster Redis: `redis cache-main`\n\n"
        "Commands: /reset (xóa context), /help",
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: AppSettings = context.bot_data["settings"]
    if not _is_authorized(update, settings):
        return
    await update.message.reply_text(
        "Hỗ trợ:\n"
        "• Linux host: CPU/RAM/Disk/IO/Load\n"
        "• Percona XtraDB Cluster: size, wsrep state, flow-control\n"
        "• Redis Cluster: cluster_state, slots, memory, eviction\n\n"
        "Cứ nhập IP hoặc tên cluster, hỏi câu hỏi tự nhiên."
    )


async def on_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: AppSettings = context.bot_data["settings"]
    if not _is_authorized(update, settings):
        return
    sessions: SessionStore = context.bot_data["sessions"]
    sessions.reset(update.effective_user.id)
    await update.message.reply_text("Đã xóa context. Bắt đầu lại nhé.")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: AppSettings = context.bot_data["settings"]
    if not _is_authorized(update, settings):
        user = update.effective_user
        logger.warning(
            "Unauthorized message from user_id=%s text=%r",
            user.id if user else "?",
            update.message.text[:60] if update.message and update.message.text else "",
        )
        await update.message.reply_text("Bạn chưa nằm trong whitelist.")
        return

    sessions: SessionStore = context.bot_data["sessions"]
    openai_client: AsyncOpenAI = context.bot_data["openai_client"]
    tool_ctx: ToolContext = context.bot_data["tool_ctx"]
    model = settings.llm_model

    sess = sessions.get(update.effective_user.id)
    sess.messages.append({"role": "user", "content": update.message.text})

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    try:
        reply, sess.messages = await run_agent(
            openai_client, model, sess.messages, tool_ctx
        )
    except Exception:
        logger.exception("agent failed")
        await update.message.reply_text(
            "Có lỗi khi xử lý. Anh thử lại sau hoặc /reset."
        )
        return

    # Telegram MarkdownV2 yêu cầu escape phức tạp → dùng plain text cho an toàn
    await update.message.reply_text(reply or "(không có nội dung trả lời)")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("update error: %s", context.error)
