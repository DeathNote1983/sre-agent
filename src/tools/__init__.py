"""Tool registry cho OpenAI function calling.

Mỗi tool gồm:
  - schema: dict theo định dạng OpenAI tools (function calling)
  - handler: async callable(client, thresholds, **args) -> dict

`OPENAI_TOOLS` = list[schema] truyền vào openai SDK.
`dispatch(name, args, ctx)` thực thi tool theo tên.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.config import Thresholds
from src.grafana_client import GrafanaClient

from . import assess as assess_mod
from . import discovery as discovery_mod
from . import host as host_mod
from . import pxc as pxc_mod
from . import redis as redis_mod


@dataclass
class ToolContext:
    client: GrafanaClient
    thresholds: Thresholds


Handler = Callable[..., Awaitable[dict[str, Any]]]


async def _find_target(ctx: ToolContext, query: str) -> dict[str, Any]:
    return await discovery_mod.find_target(ctx.client, query)


async def _get_host_metrics(ctx: ToolContext, ip: str, range: str = "5m") -> dict[str, Any]:
    return await host_mod.get_host_metrics(ctx.client, ip, range)


async def _get_pxc_cluster(ctx: ToolContext, cluster_name: str) -> dict[str, Any]:
    return await pxc_mod.get_pxc_cluster(ctx.client, cluster_name)


async def _get_redis_cluster(ctx: ToolContext, cluster_name: str) -> dict[str, Any]:
    return await redis_mod.get_redis_cluster(ctx.client, cluster_name)


async def _assess(ctx: ToolContext, metrics: dict, tech: str) -> dict[str, Any]:
    # sync wrap thành async để đồng nhất signature
    return assess_mod.assess(metrics, tech, ctx.thresholds)


_HANDLERS: dict[str, Handler] = {
    "find_target": _find_target,
    "get_host_metrics": _get_host_metrics,
    "get_pxc_cluster": _get_pxc_cluster,
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
                "PHẢI gọi đầu tiên trước khi dùng các tool khác để biết tech (linux/pxc/redis) "
                "và danh sách node."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "IP (vd '10.1.2.3') hoặc tên cluster (vd 'pxc-prod-1').",
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
            "name": "get_pxc_cluster",
            "description": "Lấy topology + health của Percona XtraDB Cluster theo cluster name.",
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
                "Truyền vào output của các tool get_* và tech tương ứng (linux/pxc/redis). "
                "LLM phải dùng kết quả của tool này để kết luận, KHÔNG tự suy đoán status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics": {
                        "type": "object",
                        "description": "Dict metrics lấy từ get_host_metrics / get_pxc_cluster / get_redis_cluster.",
                        "additionalProperties": True,
                    },
                    "tech": {
                        "type": "string",
                        "enum": ["linux", "pxc", "redis"],
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
