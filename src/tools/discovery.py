"""find_target: tìm host hoặc cluster theo IP hoặc tên qua Prometheus labels.

Quy ước labels (chuẩn của hệ thống monitor Zalopay):
- `instance`     : "<ip>:<port>" do Prometheus scrape config gán
- `job`          : tên job (vd: "node", "mysqld", "redis", "pxc", "kafka")
- `cluster`      : tên cluster (vd: "pxc-prod-1", "cache-main")
- `role`         : tùy job (vd: "master"/"slave" với Redis, "primary"/"replica" với MySQL)

Map job → tech:
  node            → linux
  mysqld / pxc    → pxc
  redis           → redis
"""
from __future__ import annotations

import ipaddress
from typing import Any

from src.grafana_client import GrafanaClient

_JOB_TO_TECH = {
    "node": "linux",
    "node_exporter": "linux",
    "mysqld": "pxc",
    "pxc": "pxc",
    "percona": "pxc",
    "redis": "redis",
    "redis_exporter": "redis",
}


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.split(":")[0])
        return True
    except ValueError:
        return False


async def find_target(client: GrafanaClient, query: str) -> dict[str, Any]:
    """Search Prometheus series cho query (IP hoặc cluster name).

    Trả về:
        {
          "type": "host" | "cluster" | "unknown",
          "tech": "linux" | "pxc" | "redis" | None,
          "members": [{"ip": "...", "role": "...", "cluster": "..."}],
          "match": "exact" | "partial" | "none",
          "query": <input>
        }
    """
    q = query.strip()
    if not q:
        return _empty(query, "none")

    if _is_ip(q):
        return await _find_by_ip(client, q)
    return await _find_by_cluster(client, q)


async def _find_by_ip(client: GrafanaClient, ip: str) -> dict[str, Any]:
    # series có instance bắt đầu bằng ip (port có thể khác nhau giữa các exporter)
    selector = f'{{instance=~"{ip}(:.*)?"}}'
    series = await client.series(selector)
    if not series:
        return _empty(ip, "none")

    techs: set[str] = set()
    cluster: str | None = None
    role: str | None = None
    for s in series:
        job = s.get("job", "")
        tech = _JOB_TO_TECH.get(job)
        if tech:
            techs.add(tech)
        if not cluster and s.get("cluster"):
            cluster = s["cluster"]
        if not role and s.get("role"):
            role = s["role"]

    # Ưu tiên tech cluster (pxc/redis) hơn linux nếu có
    chosen = None
    for pref in ("pxc", "redis", "linux"):
        if pref in techs:
            chosen = pref
            break

    return {
        "type": "host",
        "tech": chosen,
        "members": [{"ip": ip, "role": role, "cluster": cluster}],
        "match": "exact",
        "query": ip,
    }


async def _find_by_cluster(client: GrafanaClient, name: str) -> dict[str, Any]:
    # Thử exact trước
    series = await client.series(f'{{cluster="{name}"}}')
    match = "exact"
    if not series:
        # fallback partial
        series = await client.series(f'{{cluster=~".*{name}.*"}}')
        match = "partial"
    if not series:
        return _empty(name, "none")

    members_by_instance: dict[str, dict[str, Any]] = {}
    techs: set[str] = set()
    cluster_name = name
    for s in series:
        inst = s.get("instance", "")
        ip = inst.split(":")[0] if inst else None
        job = s.get("job", "")
        tech = _JOB_TO_TECH.get(job)
        if tech:
            techs.add(tech)
        if s.get("cluster"):
            cluster_name = s["cluster"]
        if not ip:
            continue
        m = members_by_instance.setdefault(
            ip, {"ip": ip, "role": None, "cluster": cluster_name}
        )
        if s.get("role") and not m["role"]:
            m["role"] = s["role"]

    chosen = None
    for pref in ("pxc", "redis", "linux"):
        if pref in techs:
            chosen = pref
            break

    return {
        "type": "cluster",
        "tech": chosen,
        "members": sorted(members_by_instance.values(), key=lambda x: x["ip"]),
        "match": match,
        "query": name,
        "cluster_name": cluster_name,
    }


def _empty(query: str, match: str) -> dict[str, Any]:
    return {
        "type": "unknown",
        "tech": None,
        "members": [],
        "match": match,
        "query": query,
    }
