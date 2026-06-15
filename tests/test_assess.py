"""Test pure-function assess() — cover boundary OK/WARN/CRIT cho mỗi tech."""
from __future__ import annotations

import pytest

from src.tools.assess import assess


# ---------- Linux ----------

def _linux_base() -> dict:
    return {
        "cpu_pct": 20.0,
        "ram_pct": 30.0,
        "disk_used_pct": [{"mount": "/", "pct": 40.0}],
        "disk_io_util": [{"device": "sda", "pct": 10.0}],
        "load_per_cpu": 0.3,
    }


def test_linux_all_ok(thresholds):
    out = assess(_linux_base(), "linux", thresholds)
    assert out["status"] == "OK"
    assert out["reasons"] == []
    assert out["suggestion"] is None


def test_linux_cpu_warn(thresholds):
    m = _linux_base() | {"cpu_pct": 80.0}
    out = assess(m, "linux", thresholds)
    assert out["status"] == "WARN"
    assert any("CPU" in r for r in out["reasons"])


def test_linux_ram_crit(thresholds):
    m = _linux_base() | {"ram_pct": 95.0}
    out = assess(m, "linux", thresholds)
    assert out["status"] == "CRIT"
    assert any("RAM" in r for r in out["reasons"])
    assert out["suggestion"] is not None


def test_linux_disk_crit_takes_priority(thresholds):
    m = _linux_base() | {
        "cpu_pct": 80.0,           # WARN
        "disk_used_pct": [{"mount": "/data", "pct": 95.0}],  # CRIT
    }
    out = assess(m, "linux", thresholds)
    assert out["status"] == "CRIT"


def test_linux_io_warn(thresholds):
    m = _linux_base() | {"disk_io_util": [{"device": "sda", "pct": 75.0}]}
    out = assess(m, "linux", thresholds)
    assert out["status"] == "WARN"


def test_linux_load_per_cpu_crit(thresholds):
    m = _linux_base() | {"load_per_cpu": 3.5}
    out = assess(m, "linux", thresholds)
    assert out["status"] == "CRIT"


# ---------- MySQL ----------

def _mysql_base() -> dict:
    return {
        "nodes": [
            {"ip": "10.0.0.1", "up": 1, "role": "master", "connections_pct": 20.0,
             "slave_io_running": None, "slave_sql_running": None, "seconds_behind_master": None},
            {"ip": "10.0.0.2", "up": 1, "role": "slave", "connections_pct": 30.0,
             "slave_io_running": 1, "slave_sql_running": 1, "seconds_behind_master": 2.0},
        ],
    }


def test_mysql_all_ok(thresholds):
    out = assess(_mysql_base(), "mysql", thresholds)
    assert out["status"] == "OK"
    assert out["reasons"] == []


def test_mysql_node_down_crit(thresholds):
    m = _mysql_base()
    m["nodes"][0]["up"] = 0
    out = assess(m, "mysql", thresholds)
    assert out["status"] == "CRIT"
    assert any("DOWN" in r for r in out["reasons"])


def test_mysql_connections_warn(thresholds):
    m = _mysql_base()
    m["nodes"][0]["connections_pct"] = 85.0
    out = assess(m, "mysql", thresholds)
    assert out["status"] == "WARN"
    assert any("connections" in r.lower() for r in out["reasons"])


def test_mysql_connections_crit(thresholds):
    m = _mysql_base()
    m["nodes"][0]["connections_pct"] = 95.0
    out = assess(m, "mysql", thresholds)
    assert out["status"] == "CRIT"
    assert out["suggestion"] is not None


def test_mysql_replication_not_running_crit(thresholds):
    m = _mysql_base()
    m["nodes"][1]["slave_sql_running"] = 0
    out = assess(m, "mysql", thresholds)
    assert out["status"] == "CRIT"
    assert any("replication" in r.lower() for r in out["reasons"])


def test_mysql_replication_lag_warn(thresholds):
    m = _mysql_base()
    m["nodes"][1]["seconds_behind_master"] = 60.0
    out = assess(m, "mysql", thresholds)
    assert out["status"] == "WARN"
    assert any("lag" in r.lower() for r in out["reasons"])


def test_mysql_replication_lag_crit(thresholds):
    m = _mysql_base()
    m["nodes"][1]["seconds_behind_master"] = 600.0
    out = assess(m, "mysql", thresholds)
    assert out["status"] == "CRIT"


# ---------- Redis ----------

def _redis_base() -> dict:
    return {
        "nodes": [
            {"ip": "10.1.1.1", "role": "master", "master_link_status": None},
            {"ip": "10.1.1.2", "role": "slave", "master_link_status": "up"},
            {"ip": "10.1.1.3", "role": "slave", "master_link_status": "up"},
        ],
        "cluster_state": "ok",
        "slots_ok": 16384,
        "used_memory_pct": 40.0,
        "ops_per_sec": 1000.0,
        "evicted_keys_rate": 0.0,
    }


def test_redis_all_ok(thresholds):
    out = assess(_redis_base(), "redis", thresholds)
    assert out["status"] == "OK"


def test_redis_cluster_state_not_ok_crit(thresholds):
    m = _redis_base() | {"cluster_state": "fail"}
    out = assess(m, "redis", thresholds)
    assert out["status"] == "CRIT"


def test_redis_slots_missing_crit(thresholds):
    m = _redis_base() | {"slots_ok": 16000}
    out = assess(m, "redis", thresholds)
    assert out["status"] == "CRIT"


def test_redis_memory_warn(thresholds):
    m = _redis_base() | {"used_memory_pct": 85.0}
    out = assess(m, "redis", thresholds)
    assert out["status"] == "WARN"


def test_redis_memory_crit(thresholds):
    m = _redis_base() | {"used_memory_pct": 92.0}
    out = assess(m, "redis", thresholds)
    assert out["status"] == "CRIT"


def test_redis_eviction_high_crit(thresholds):
    m = _redis_base() | {"evicted_keys_rate": 1500.0}
    out = assess(m, "redis", thresholds)
    assert out["status"] == "CRIT"


def test_redis_slave_link_down_crit(thresholds):
    m = _redis_base()
    m["nodes"][1]["master_link_status"] = "down"
    out = assess(m, "redis", thresholds)
    assert out["status"] == "CRIT"


# ---------- Misc ----------

def test_unknown_tech_returns_ok_with_note(thresholds):
    out = assess({}, "kafka", thresholds)
    assert out["status"] == "OK"
    assert "kafka" in (out["suggestion"] or "").lower()
