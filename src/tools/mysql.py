"""get_mysql_cluster: health của MySQL (mysql_exporter) — KHÔNG dùng wsrep/PXC.

Kiểm tra: up/down, connections saturation, replication (IO/SQL running + lag).
Metrics nguồn: prometheus/mysqld_exporter (mysql_*). Instance label dạng "ip:port".
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.grafana_client import GrafanaClient, to_label_map


def _sel(cluster: str, member_ips: list[str] | None = None) -> str:
    # Có danh sách IP (từ mapping) -> lọc theo instance; ngược lại theo label cluster.
    # KHÔNG escape '.' — PromQL string literal không cho '\.' (giống host._inst_selector).
    if member_ips:
        pat = "|".join(member_ips)
        return f'instance=~"({pat})(:.*)?"'
    return f'cluster="{cluster}"'


def _by_inst(result: list[dict]) -> dict[str, float]:
    return {labels.get("instance", "?"): val for labels, val in to_label_map(result)}


async def get_mysql_cluster(
    client: GrafanaClient,
    cluster_name: str,
    member_ips: list[str] | None = None,
    ds: str | None = None,
) -> dict[str, Any]:
    sel = _sel(cluster_name, member_ips)

    up_q = f"mysql_up{{{sel}}}"
    conn_q = f"mysql_global_status_threads_connected{{{sel}}}"
    maxconn_q = f"mysql_global_variables_max_connections{{{sel}}}"
    io_q = f"mysql_slave_status_slave_io_running{{{sel}}}"
    sql_q = f"mysql_slave_status_slave_sql_running{{{sel}}}"
    lag_q = f"mysql_slave_status_seconds_behind_master{{{sel}}}"
    slow_q = f"rate(mysql_global_status_slow_queries{{{sel}}}[5m])"
    qps_q = f"rate(mysql_global_status_queries{{{sel}}}[5m])"

    up_r, conn_r, maxc_r, io_r, sql_r, lag_r, slow_r, qps_r = await asyncio.gather(
        client.instant_query(up_q, ds=ds),
        client.instant_query(conn_q, ds=ds),
        client.instant_query(maxconn_q, ds=ds),
        client.instant_query(io_q, ds=ds),
        client.instant_query(sql_q, ds=ds),
        client.instant_query(lag_q, ds=ds),
        client.instant_query(slow_q, ds=ds),
        client.instant_query(qps_q, ds=ds),
    )

    up = _by_inst(up_r)
    conn = _by_inst(conn_r)
    maxc = _by_inst(maxc_r)
    io = _by_inst(io_r)
    sql = _by_inst(sql_r)
    lag = _by_inst(lag_r)
    slow = _by_inst(slow_r)
    qps = _by_inst(qps_r)

    nodes: list[dict[str, Any]] = []
    for inst in sorted(set(up) | set(conn) | set(io) | set(lag)):
        ip = inst.split(":")[0]
        is_slave = inst in lag or inst in io
        mc = maxc.get(inst)
        tc = conn.get(inst)
        cpct = round(tc / mc * 100, 1) if (tc is not None and mc) else None
        nodes.append(
            {
                "ip": ip,
                "instance": inst,
                "up": int(up[inst]) if inst in up else None,
                "role": "slave" if is_slave else "master",
                "threads_connected": int(tc) if tc is not None else None,
                "max_connections": int(mc) if mc else None,
                "connections_pct": cpct,
                "slave_io_running": int(io[inst]) if inst in io else None,
                "slave_sql_running": int(sql[inst]) if inst in sql else None,
                "seconds_behind_master": round(lag[inst], 1) if inst in lag else None,
                "slow_queries_rate": round(slow.get(inst, 0.0), 2),
                "qps": round(qps.get(inst, 0.0), 2),
            }
        )

    return {
        "cluster_name": cluster_name,
        "node_count": len(nodes),
        "nodes": sorted(nodes, key=lambda n: (n["role"], n["ip"])),
    }
