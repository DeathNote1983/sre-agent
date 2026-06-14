"""get_redis_cluster: topology + health của Redis Cluster.

Metrics nguồn: oliver006/redis_exporter (redis_*).
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.grafana_client import GrafanaClient, first_scalar, to_label_map


def _sel(cluster: str) -> str:
    return f'cluster="{cluster}"'


async def get_redis_cluster(client: GrafanaClient, cluster_name: str) -> dict[str, Any]:
    sel = _sel(cluster_name)

    role_q = f'redis_instance_info{{{sel}}}'  # labels chứa role=master|slave
    mem_pct_q = (
        f'(redis_memory_used_bytes{{{sel}}} '
        f'/ clamp_min(redis_memory_max_bytes{{{sel}}}, 1)) * 100'
    )
    cluster_state_q = f'redis_cluster_enabled{{{sel}}}'  # 1 = cluster mode
    slots_q = f'max(redis_cluster_slots_ok{{{sel}}})'
    ops_q = f'sum(rate(redis_commands_processed_total{{{sel}}}[5m]))'
    evict_q = f'sum(rate(redis_evicted_keys_total{{{sel}}}[5m]))'
    link_q = f'redis_connected_slaves{{{sel}}}'
    master_link_q = f'redis_master_link_up{{{sel}}}'  # exporter expose này khi slave

    role_r, mem_r, cs_r, slots_r, ops_r, evict_r, link_r, ml_r = await asyncio.gather(
        client.instant_query(role_q),
        client.instant_query(mem_pct_q),
        client.instant_query(cluster_state_q),
        client.instant_query(slots_q),
        client.instant_query(ops_q),
        client.instant_query(evict_q),
        client.instant_query(link_q),
        client.instant_query(master_link_q),
    )

    mem_by_instance = {labels.get("instance", "?"): val for labels, val in to_label_map(mem_r)}
    ml_by_instance = {labels.get("instance", "?"): val for labels, val in to_label_map(ml_r)}

    nodes: list[dict[str, Any]] = []
    for labels, _ in to_label_map(role_r):
        instance = labels.get("instance", "?")
        ip = instance.split(":")[0]
        role = labels.get("role", "")
        link_val = ml_by_instance.get(instance)
        nodes.append(
            {
                "ip": ip,
                "instance": instance,
                "role": role,
                "master_link_status": (
                    "up" if link_val == 1.0
                    else "down" if link_val == 0.0
                    else None
                ),
            }
        )

    # used_memory_pct: lấy max của các node để conservative
    mem_pct = max(mem_by_instance.values()) if mem_by_instance else 0.0

    cs_val = first_scalar(cs_r) or 0
    cluster_state = "ok" if cs_val == 1 else "disabled"
    slots_ok = int(first_scalar(slots_r) or 0)
    ops = first_scalar(ops_r) or 0.0
    evicted = first_scalar(evict_r) or 0.0

    return {
        "cluster_name": cluster_name,
        "nodes": sorted(nodes, key=lambda n: (n["role"], n["ip"])),
        "cluster_state": cluster_state,
        "slots_ok": slots_ok,
        "used_memory_pct": round(mem_pct, 2),
        "ops_per_sec": round(ops, 2),
        "evicted_keys_rate": round(evicted, 2),
    }
