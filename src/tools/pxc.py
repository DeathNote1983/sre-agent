"""get_pxc_cluster: topology + health của Percona XtraDB Cluster.

Metrics nguồn: mysqld_exporter expose mysql_global_status_wsrep_*.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.grafana_client import GrafanaClient, first_scalar, to_label_map


def _sel(cluster: str) -> str:
    return f'cluster="{cluster}"'


async def get_pxc_cluster(client: GrafanaClient, cluster_name: str) -> dict[str, Any]:
    sel = _sel(cluster_name)

    size_q = f'max(mysql_global_status_wsrep_cluster_size{{{sel}}})'
    state_q = f'mysql_global_status_wsrep_local_state{{{sel}}}'
    queue_q = f'mysql_global_status_wsrep_local_recv_queue_avg{{{sel}}}'
    fc_paused_q = f'avg(mysql_global_status_wsrep_flow_control_paused{{{sel}}}) * 100'
    primary_q = f'count(mysql_global_status_wsrep_cluster_status{{{sel}}} == 1)'

    size_r, state_r, queue_r, fc_r, primary_r = await asyncio.gather(
        client.instant_query(size_q),
        client.instant_query(state_q),
        client.instant_query(queue_q),
        client.instant_query(fc_paused_q),
        client.instant_query(primary_q),
    )

    size = int(first_scalar(size_r) or 0)
    fc_pct = first_scalar(fc_r) or 0.0
    primary_count = int(first_scalar(primary_r) or 0)

    state_by_instance = {labels.get("instance", "?"): val for labels, val in to_label_map(state_r)}
    queue_by_instance = {labels.get("instance", "?"): val for labels, val in to_label_map(queue_r)}

    nodes: list[dict[str, Any]] = []
    for instance, state in state_by_instance.items():
        ip = instance.split(":")[0]
        nodes.append(
            {
                "ip": ip,
                "instance": instance,
                "wsrep_local_state": int(state),
                "queue_avg": round(queue_by_instance.get(instance, 0.0), 2),
            }
        )

    return {
        "cluster_name": cluster_name,
        "size": size,
        "primary_count": primary_count,
        "nodes": sorted(nodes, key=lambda n: n["ip"]),
        "flow_control_paused_pct": round(fc_pct, 2),
    }
