"""Entrypoint: load config, start Telegram long-polling."""
from __future__ import annotations

import logging

from dotenv import load_dotenv

from src.bot import build_app
from src.config import load_settings


def main() -> None:
    load_dotenv()
    settings = load_settings()

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # python-telegram-bot httpx khá ồn ở INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.INFO)

    app = build_app(settings)
    logging.getLogger(__name__).info(
        "SRE agent online (model=%s, whitelist=%d users)",
        settings.openai_model,
        len(settings.whitelist.users),
    )
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
