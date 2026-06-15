"""Shared fixtures."""
from __future__ import annotations

import pytest

from src.config import Thresholds


@pytest.fixture
def thresholds() -> Thresholds:
    """Cùng giá trị với config/thresholds.yaml — duplicate có chủ ý để test
    không phụ thuộc file system."""
    return Thresholds(
        **{
            "linux": {
                "cpu_pct": {"warn": 75, "crit": 90, "sustained_min": 15},
                "ram_pct": {"warn": 80, "crit": 92},
                "disk_used_pct": {"warn": 80, "crit": 90},
                "disk_io_util": {"warn": 70, "crit": 90},
                "load_per_cpu": {"warn": 1.5, "crit": 3.0},
            },
            "mysql": {
                "connections_pct": {"warn": 80, "crit": 90},
                "replication_lag_sec": {"warn": 30, "crit": 300},
            },
            "redis": {
                "used_memory_pct": {"warn": 80, "crit": 90},
                "evicted_keys_rate": {"warn": 100, "crit": 1000},
                "master_link_ok": "up",
                "cluster_state_ok": "ok",
            },
        }
    )
