"""Entrypoint: load config, start Telegram long-polling."""
from __future__ import annotations

import logging

from dotenv import load_dotenv

from src.bot import build_app
from src.config import load_settings
from src.extra_hosts import apply_extra_hosts
from src.health import start_health_server


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

    # Ghi host alias (vd Grafana) vào /etc/hosts. Đặt sau basicConfig để log hiển
    # thị; vẫn chạy trước build_app/mọi call tới Grafana.
    apply_extra_hosts()

    app = build_app(settings)

    # Health server cho AgentBase Runtime (port 8080 / GET /health) — chạy nền,
    # phải bật trước run_polling vì run_polling block luồng chính.
    start_health_server()

    logging.getLogger(__name__).info(
        "SRE agent online (model=%s, whitelist=%d users)",
        settings.llm_model,
        len(settings.whitelist.users),
    )
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
