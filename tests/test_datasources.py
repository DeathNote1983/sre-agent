"""Test DatasourceMap.ds_for + load_settings với/không datasources.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

from src.config import DatasourceMap, load_settings

_THRESHOLDS = {
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


def test_ds_for_mapped():
    m = DatasourceMap(datasources={"host": "104", "mysql": "83"})
    assert m.ds_for("host", "1") == "104"
    assert m.ds_for("mysql", "1") == "83"


def test_ds_for_fallback_default():
    m = DatasourceMap(datasources={"host": "104"})
    assert m.ds_for("redis", "999") == "999"


def test_ds_for_coerce_int():
    m = DatasourceMap(**{"datasources": {"host": 104}})  # YAML có thể parse số
    assert m.ds_for("host", "1") == "104"


def test_empty_map_uses_default():
    assert DatasourceMap().ds_for("host", "104") == "104"


def _write_base_config(d: Path) -> None:
    (d / "thresholds.yaml").write_text(yaml.safe_dump(_THRESHOLDS), encoding="utf-8")
    (d / "whitelist.yaml").write_text(yaml.safe_dump({"users": [1]}), encoding="utf-8")


def _set_env(monkeypatch) -> None:
    monkeypatch.setenv("GRAFANA_URL", "https://g")
    monkeypatch.setenv("GRAFANA_DS_UID", "104")
    monkeypatch.setenv("GRAFANA_TOKEN", "t0k")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")


def test_load_settings_with_datasources(tmp_path, monkeypatch):
    _write_base_config(tmp_path)
    _set_env(monkeypatch)
    (tmp_path / "datasources.yaml").write_text(
        yaml.safe_dump({"datasources": {"host": 104, "mysql": 83}}), encoding="utf-8"
    )
    s = load_settings(config_dir=tmp_path)
    assert s.datasources.ds_for("host", "x") == "104"
    assert s.datasources.ds_for("mysql", "x") == "83"


def test_load_settings_without_datasources(tmp_path, monkeypatch):
    _write_base_config(tmp_path)
    _set_env(monkeypatch)
    s = load_settings(config_dir=tmp_path)
    assert s.datasources.ds_for("host", "104") == "104"  # fallback default
