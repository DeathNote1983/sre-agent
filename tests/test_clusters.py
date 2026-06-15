"""Test ClusterMap.resolve + load_settings với/không clusters.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

from src.config import ClusterDef, ClusterMap, load_settings

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


def _map() -> ClusterMap:
    return ClusterMap(
        clusters=[
            ClusterDef(
                name="Promotion Redis Cluster",
                tech="redis",
                members=["10.60.59.2", "10.60.59.3", "10.60.59.4"],
            ),
            ClusterDef(name="Mysql Prod 1", tech="mysql", members=["10.1.1.1"]),
        ]
    )


def test_resolve_exact_case_insensitive():
    c = _map().resolve("promotion redis cluster")
    assert c is not None and c.name == "Promotion Redis Cluster" and c.tech == "redis"


def test_resolve_substring():
    c = _map().resolve("promotion")
    assert c is not None and c.name == "Promotion Redis Cluster"
    assert c.members == ["10.60.59.2", "10.60.59.3", "10.60.59.4"]


def test_resolve_no_match():
    assert _map().resolve("nonexistent") is None


def test_resolve_empty_query():
    assert _map().resolve("") is None


def test_empty_map_resolve():
    assert ClusterMap().resolve("anything") is None


def test_parse_from_dict():
    m = ClusterMap(**{"clusters": [{"name": "X", "tech": "linux", "members": ["1.1.1.1"]}]})
    c = m.resolve("x")
    assert c is not None and c.tech == "linux"


def _write_base_config(d: Path) -> None:
    (d / "thresholds.yaml").write_text(yaml.safe_dump(_THRESHOLDS), encoding="utf-8")
    (d / "whitelist.yaml").write_text(yaml.safe_dump({"users": [1]}), encoding="utf-8")


def _set_env(monkeypatch) -> None:
    monkeypatch.setenv("GRAFANA_URL", "https://g")
    monkeypatch.setenv("GRAFANA_DS_UID", "104")
    monkeypatch.setenv("GRAFANA_TOKEN", "t0k")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")


def test_load_settings_without_clusters_file(tmp_path, monkeypatch):
    _write_base_config(tmp_path)
    _set_env(monkeypatch)
    s = load_settings(config_dir=tmp_path)
    assert s.clusters.clusters == []  # không có file -> map rỗng, không lỗi


def test_load_settings_with_clusters_file(tmp_path, monkeypatch):
    _write_base_config(tmp_path)
    _set_env(monkeypatch)
    (tmp_path / "clusters.yaml").write_text(
        yaml.safe_dump(
            {
                "clusters": [
                    {
                        "name": "Promotion Redis Cluster",
                        "tech": "redis",
                        "members": ["10.60.59.2", "10.60.59.3"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    s = load_settings(config_dir=tmp_path)
    c = s.clusters.resolve("promotion")
    assert c is not None and c.tech == "redis" and "10.60.59.2" in c.members
