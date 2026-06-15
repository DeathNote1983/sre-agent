"""Test combine: get_mysql_cluster tự kèm resource (host metrics ở DS resource) per node,
và 2 datasource (db vs host) được gọi đúng proxy path."""
from __future__ import annotations

import httpx
import pytest
import respx

from src.config import DatasourceMap
from src.grafana_client import GrafanaClient
from src.tools import ToolContext, dispatch

BASE = "https://grafana.test"
DB_PROXY = f"{BASE}/api/datasources/proxy/83/api/v1"      # database (mysql_exporter)
HOST_PROXY = f"{BASE}/api/datasources/proxy/104/api/v1"   # resource (node_exporter)


def _vector(instance: str, val: str):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {"instance": instance}, "value": [1700000000, val]}],
        },
    }


def _empty():
    return {"status": "success", "data": {"resultType": "vector", "result": []}}


@respx.mock
@pytest.mark.asyncio
async def test_get_mysql_cluster_attaches_resource(thresholds):
    # DB query (DS 83): chỉ mysql_up trả 1 node để có IP; còn lại rỗng.
    def db_side_effect(request):
        q = request.url.params.get("query", "")
        if "mysql_up{" in q:
            return httpx.Response(200, json=_vector("10.0.0.1:8306", "1"))
        return httpx.Response(200, json=_empty())

    db_route = respx.get(f"{DB_PROXY}/query").mock(side_effect=db_side_effect)
    # Host query (DS 104): trả giá trị để có resource.
    host_route = respx.get(f"{HOST_PROXY}/query").mock(
        return_value=httpx.Response(200, json=_vector("10.0.0.1:9100", "12"))
    )

    client = GrafanaClient(base_url=BASE, ds_uid="prom", token="t0k", timeout=5.0)
    ctx = ToolContext(
        client=client,
        thresholds=thresholds,
        datasources=DatasourceMap(datasources={"mysql": "83", "host": "104"}),
    )
    result = await dispatch("get_mysql_cluster", {"cluster_name": "anything"}, ctx)

    assert "error" not in result
    assert db_route.called and host_route.called  # cả 2 DS đúng path
    nodes = result["nodes"]
    assert len(nodes) == 1 and nodes[0]["ip"] == "10.0.0.1"
    assert nodes[0]["up"] == 1
    assert nodes[0]["resource"] is not None and "cpu_pct" in nodes[0]["resource"]
    assert nodes[0]["resource_assessment"]["status"] in ("OK", "WARN", "CRIT")
