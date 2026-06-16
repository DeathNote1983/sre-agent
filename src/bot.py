"""Telegram bot handlers, auth, memory-backed conversation."""
from __future__ import annotations

import logging
import time

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
from src.memory_store import AgentMemory
from src.tools import ToolContext

logger = logging.getLogger(__name__)

_DEFAULT_SESSION = "main"


def _session_id(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    """Session hiện tại của user (mặc định 'main'; /reset đổi sang session mới)."""
    return context.bot_data.get("session_ids", {}).get(user_id, _DEFAULT_SESSION)


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
    tool_ctx = ToolContext(
        client=grafana,
        thresholds=settings.thresholds,
        clusters=settings.clusters,
        datasources=settings.datasources,
    )
    memory = AgentMemory(settings.memory_id, settings.memory_strategy_id)

    app = Application.builder().token(settings.telegram_bot_token).build()

    # Stash dependencies vào bot_data (per-Application, không phải per-update)
    app.bot_data["settings"] = settings
    app.bot_data["openai_client"] = openai_client
    app.bot_data["tool_ctx"] = tool_ctx
    app.bot_data["memory"] = memory
    app.bot_data["session_ids"] = {}  # user_id -> session_id hiện tại (/reset đổi)

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
        "• Tình trạng 1 cluster, vd: `Dev Mysql Cluster còn ổn không?`\n"
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
        "• MySQL (mysql_exporter): up/down, connections, replication (lag, IO/SQL)\n"
        "• Redis Cluster: cluster_state, slots, memory, eviction\n\n"
        "Cứ nhập IP hoặc tên cluster, hỏi câu hỏi tự nhiên."
    )


async def on_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: AppSettings = context.bot_data["settings"]
    if not _is_authorized(update, settings):
        return
    # Đổi sang session mới → quên lịch sử hội thoại gần đây (facts long-term vẫn giữ).
    context.bot_data.setdefault("session_ids", {})[update.effective_user.id] = f"s{int(time.time())}"
    await update.message.reply_text("Đã xóa context hội thoại. Bắt đầu lại nhé.")


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

    memory: AgentMemory = context.bot_data["memory"]
    openai_client: AsyncOpenAI = context.bot_data["openai_client"]
    tool_ctx: ToolContext = context.bot_data["tool_ctx"]
    model = settings.llm_model

    user_id = update.effective_user.id
    actor = str(user_id)
    session = _session_id(context, user_id)
    text = update.message.text

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    # Lịch sử hội thoại (sống qua restart) + facts long-term liên quan của user
    history = await memory.load_history(actor, session)
    facts = await memory.recall(actor, text)
    system = SYSTEM_PROMPT
    if facts:
        system += "\n\n# Ghi nhớ về user/hệ thống (long-term memory):\n- " + "\n- ".join(facts)
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": text},
    ]

    try:
        reply, _ = await run_agent(openai_client, model, messages, tool_ctx)
    except Exception:
        logger.exception("agent failed")
        await update.message.reply_text(
            "Có lỗi khi xử lý. Anh thử lại sau hoặc /reset."
        )
        return

    # Telegram MarkdownV2 yêu cầu escape phức tạp → dùng plain text cho an toàn
    await update.message.reply_text(reply or "(không có nội dung trả lời)")

    # Lưu lượt hội thoại vào memory (auto-extract facts theo strategy SEMANTIC)
    await memory.append_turn(actor, session, "user", text)
    await memory.append_turn(actor, session, "assistant", reply or "")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("update error: %s", context.error)
