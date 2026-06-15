"""Test redis tool: selector theo label cluster vs theo member IPs (mapping)."""
from __future__ import annotations

import httpx
import pytest
import respx

from src.grafana_client import GrafanaClient
from src.tools.redis import _sel, get_redis_cluster

BASE = "https://grafana.test"
DS = "prom"
PROXY = f"{BASE}/api/datasources/proxy/uid/{DS}/api/v1"


def test_sel_by_cluster_label():
    assert _sel("cache-main") == 'cluster="cache-main"'


def test_sel_by_member_ips():
    sel = _sel("Promotion Redis Cluster", ["10.60.59.2", "10.60.59.3"])
    assert sel == 'instance=~"(10.60.59.2|10.60.59.3)(:.*)?"'


@respx.mock
@pytest.mark.asyncio
async def test_get_redis_cluster_uses_member_ip_selector():
    route = respx.get(f"{PROXY}/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": []}}
        )
    )
    client = GrafanaClient(base_url=BASE, ds_uid=DS, token="t0k", timeout=5.0)
    await get_redis_cluster(
        client, "Promotion Redis Cluster", ["10.60.59.2", "10.60.59.3", "10.60.59.4"]
    )
    assert route.called
    queries = [c.request.url.params.get("query") or "" for c in route.calls]
    assert any("instance=~" in q and "10.60.59.2" in q for q in queries)
    # không được dùng selector theo label cluster
    assert all('cluster="Promotion' not in q for q in queries)
