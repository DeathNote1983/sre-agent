"""Thêm host alias tĩnh vào /etc/hosts khi khởi động.

Dùng khi container cần resolve một domain không có trong DNS của runtime (vd
Grafana dashboard nội bộ). Cấu hình qua biến môi trường EXTRA_HOSTS, mỗi entry
theo đúng định dạng /etc/hosts ("IP hostname [hostname...]"); nhiều entry phân
tách bằng dấu ';'.

Ví dụ: EXTRA_HOSTS=49.213.117.10 dashboard.zalopay.vn

Connect vẫn theo hostname nên TLS/SNI và việc validate cert không bị ảnh hưởng.
Ghi /etc/hosts cần quyền root (image python:3.11-slim chạy root mặc định); nếu
không ghi được thì chỉ cảnh báo, không chặn app.
"""
from __future__ import annotations

import logging
import os
from typing import Mapping

logger = logging.getLogger(__name__)


def apply_extra_hosts(
    hosts_path: str = "/etc/hosts", env: Mapping[str, str] | None = None
) -> None:
    raw = (env if env is not None else os.environ).get("EXTRA_HOSTS", "").strip()
    if not raw:
        return

    entries = [e.strip() for e in raw.split(";") if e.strip()]
    if not entries:
        return

    try:
        existing = ""
        if os.path.exists(hosts_path):
            with open(hosts_path, "r", encoding="utf-8") as f:
                existing = f.read()

        existing_lines = {ln.strip() for ln in existing.splitlines()}
        with open(hosts_path, "a", encoding="utf-8") as f:
            for entry in entries:
                if entry in existing_lines:
                    logger.info("Host alias đã có, bỏ qua: %s", entry)
                    continue
                f.write(f"{entry}\n")
                logger.info("Đã thêm host alias: %s", entry)
    except OSError as exc:
        logger.warning("Không ghi được %s (%s); bỏ qua EXTRA_HOSTS", hosts_path, exc)
