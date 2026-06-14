"""Test discovery.find_target: IP vs cluster name, exact/partial match."""
from __future__ import annotations

import httpx
import pytest
import respx

from src.grafana_client import GrafanaClient
from src.tools.discovery import find_target


BASE = "https://grafana.test"
DS_UID = "prom"
PROXY = f"{BASE}/api/datasources/proxy/uid/{DS_UID}/api/v1"


def _client() -> GrafanaClient:
    return GrafanaClient(base_url=BASE, ds_uid=DS_UID, token="t0k", timeout=5.0)


@respx.mock
@pytest.mark.asyncio
async def test_find_by_ip_returns_host_with_tech():
    respx.get(f"{PROXY}/series").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {"__name__": "node_load1", "job": "node", "instance": "10.0.0.1:9100"},
                    {
                        "__name__": "mysql_global_status_wsrep_local_state",
                        "job": "mysqld",
                        "instance": "10.0.0.1:9104",
                        "cluster": "pxc-prod-1",
                    },
                ],
            },
        )
    )
    out = await find_target(_client(), "10.0.0.1")
    assert out["type"] == "host"
    assert out["tech"] == "pxc"  # ưu tiên hơn linux
    assert out["members"][0]["ip"] == "10.0.0.1"
    assert out["members"][0]["cluster"] == "pxc-prod-1"


@respx.mock
@pytest.mark.asyncio
async def test_find_by_cluster_exact_match():
    respx.get(f"{PROXY}/series").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {"job": "redis", "instance": "10.1.1.1:9121", "cluster": "cache-main", "role": "master"},
                    {"job": "redis", "instance": "10.1.1.2:9121", "cluster": "cache-main", "role": "slave"},
                ],
            },
        )
    )
    out = await find_target(_client(), "cache-main")
    assert out["type"] == "cluster"
    assert out["tech"] == "redis"
    assert out["match"] == "exact"
    ips = sorted(m["ip"] for m in out["members"])
    assert ips == ["10.1.1.1", "10.1.1.2"]


@respx.mock
@pytest.mark.asyncio
async def test_find_unknown_returns_empty():
    respx.get(f"{PROXY}/series").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    out = await find_target(_client(), "nope-xyz")
    assert out["type"] == "unknown"
    assert out["tech"] is None
    assert out["members"] == []


@respx.mock
@pytest.mark.asyncio
async def test_find_by_cluster_partial_fallback():
    """First exact selector → empty, second partial selector → có data."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"status": "success", "data": []})
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {"job": "mysqld", "instance": "10.0.0.5:9104", "cluster": "pxc-prod-99"},
                ],
            },
        )

    respx.get(f"{PROXY}/series").mock(side_effect=handler)
    out = await find_target(_client(), "prod-99")
    assert out["match"] == "partial"
    assert out["cluster_name"] == "pxc-prod-99"
