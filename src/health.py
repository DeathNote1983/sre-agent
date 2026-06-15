"""HTTP server tối thiểu phục vụ health check cho AgentBase Runtime.

Runtime yêu cầu container listen port 8080 và trả 200 ở GET /health thì mới
được đánh dấu ACTIVE. Bot Telegram chạy long-polling (kết nối ra ngoài), không
tự mở HTTP server, nên ta chạy một server nền nhỏ trong daemon thread song song
với run_polling.
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

HEALTH_PORT = 8080


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - chữ ký do BaseHTTPRequestHandler quy định
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:  # tắt access log mặc định cho đỡ ồn
        return


def start_health_server(port: int = HEALTH_PORT) -> None:
    """Khởi động health server nền (daemon thread), không block luồng chính."""
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever, name="health-server", daemon=True
    )
    thread.start()
    logger.info("Health server listening on :%d (GET /health)", port)
