"""Tool registry cho OpenAI function calling.

Mỗi tool gồm:
  - schema: dict theo định dạng OpenAI tools (function calling)
  - handler: async callable(client, thresholds, **args) -> dict

`OPENAI_TOOLS` = list[schema] truyền vào openai SDK.
`dispatch(name, args, ctx)` thực thi tool theo tên.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.config import ClusterMap, DatasourceMap, Thresholds
from src.grafana_client import GrafanaClient

from . import assess as assess_mod
from . import discovery as discovery_mod
from . import host as host_mod
from . import mysql as mysql_mod
from . import redis as redis_mod


@dataclass
class ToolContext:
    client: GrafanaClient
    thresholds: Thresholds
    clusters: ClusterMap = field(default_factory=ClusterMap)
    datasources: DatasourceMap = field(default_factory=DatasourceMap)


Handler = Callable[..., Awaitable[dict[str, Any]]]


async def _attach_resources(
    ctx: ToolContext, db_result: dict[str, Any], host_ds: str
) -> dict[str, Any]:
    """Đính kèm resource (host metrics DS resource) + verdict linux cho TỪNG node DB."""
    nodes = db_result.get("nodes") or []
    ips = [n.get("ip") for n in nodes if n.get("ip")]
    if not ips:
        return db_result
    metrics = await asyncio.gather(
        *(host_mod.get_host_metrics(ctx.client, ip, ds=host_ds) for ip in ips),
        return_exceptions=True,
    )
    by_ip = dict(zip(ips, metrics))
    for n in nodes:
        m = by_ip.get(n.get("ip"))
        if isinstance(m, Exception) or m is None:
            n["resource"] = None
            n["resource_assessment"] = None
        else:
            n["resource"] = m
            n["resource_assessment"] = assess_mod.assess_linux(m, ctx.thresholds)
    return db_result


async def _find_target(ctx: ToolContext, query: str) -> dict[str, Any]:
    return await discovery_mod.find_target(ctx.client, query, ctx.clusters)


async def _get_host_metrics(ctx: ToolContext, ip: str, range: str = "5m") -> dict[str, Any]:
    ds = ctx.datasources.ds_for("host", ctx.client.ds_uid)
    return await host_mod.get_host_metrics(ctx.client, ip, range, ds=ds)


async def _get_mysql_cluster(ctx: ToolContext, cluster_name: str) -> dict[str, Any]:
    mapped = ctx.clusters.resolve(cluster_name)
    member_ips = mapped.members if mapped else None
    db_ds = ctx.datasources.ds_for("mysql", ctx.client.ds_uid)
    host_ds = ctx.datasources.ds_for("host", ctx.client.ds_uid)
    result = await mysql_mod.get_mysql_cluster(ctx.client, cluster_name, member_ips, ds=db_ds)
    return await _attach_resources(ctx, result, host_ds)


async def _get_redis_cluster(ctx: ToolContext, cluster_name: str) -> dict[str, Any]:
    mapped = ctx.clusters.resolve(cluster_name)
    member_ips = mapped.members if mapped else None
    db_ds = ctx.datasources.ds_for("redis", ctx.client.ds_uid)
    host_ds = ctx.datasources.ds_for("host", ctx.client.ds_uid)
    result = await redis_mod.get_redis_cluster(ctx.client, cluster_name, member_ips, ds=db_ds)
    return await _attach_resources(ctx, result, host_ds)


async def _assess(ctx: ToolContext, metrics: dict, tech: str) -> dict[str, Any]:
    # sync wrap thành async để đồng nhất signature
    return assess_mod.assess(metrics, tech, ctx.thresholds)


_HANDLERS: dict[str, Handler] = {
    "find_target": _find_target,
    "get_host_metrics": _get_host_metrics,
    "get_mysql_cluster": _get_mysql_cluster,
    "get_redis_cluster": _get_redis_cluster,
    "assess": _assess,
}


OPENAI_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "find_target",
            "description": (
                "Tìm host hoặc cluster theo IP hoặc tên cluster. "
                "PHẢI gọi đầu tiên trước khi dùng các tool khác để biết tech (linux/mysql/redis) "
                "và danh sách node."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "IP (vd '10.1.2.3') hoặc tên cluster (vd 'Dev Mysql Cluster').",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_host_metrics",
            "description": "Lấy CPU/RAM/DISK/IO/load của 1 Linux host theo IP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "IP của host."},
                    "range": {
                        "type": "string",
                        "description": "Time range cho rate(), mặc định '5m'.",
                        "default": "5m",
                    },
                },
                "required": ["ip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_mysql_cluster",
            "description": "Lấy health MySQL (mysql_exporter) theo cluster name: up/down, connections, replication (IO/SQL running + lag). TỰ kèm resource (CPU/RAM/disk) từng node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cluster_name": {"type": "string"},
                },
                "required": ["cluster_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_redis_cluster",
            "description": "Lấy topology + health của Redis Cluster theo cluster name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cluster_name": {"type": "string"},
                },
                "required": ["cluster_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assess",
            "description": (
                "Đánh giá metrics → status (OK/WARN/CRIT) + reasons + suggestion. "
                "Truyền vào output của các tool get_* và tech tương ứng (linux/mysql/redis). "
                "LLM phải dùng kết quả của tool này để kết luận, KHÔNG tự suy đoán status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics": {
                        "type": "object",
                        "description": "Dict metrics lấy từ get_host_metrics / get_mysql_cluster / get_redis_cluster.",
                        "additionalProperties": True,
                    },
                    "tech": {
                        "type": "string",
                        "enum": ["linux", "mysql", "redis"],
                    },
                },
                "required": ["metrics", "tech"],
            },
        },
    },
]


async def dispatch(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return await handler(ctx, **(args or {}))
    except TypeError as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}
    except Exception as exc:  # surface lỗi cho LLM thấy để giải thích
        return {"error": f"{type(exc).__name__}: {exc}"}
