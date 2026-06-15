"""Test grafana_client wrapper với respx mock."""
from __future__ import annotations

import httpx
import pytest
import respx

from src.grafana_client import GrafanaClient, GrafanaError, first_scalar, to_label_map


BASE = "https://grafana.test"
DS_UID = "prom"
PROXY = f"{BASE}/api/datasources/proxy/uid/{DS_UID}/api/v1"


def _client() -> GrafanaClient:
    return GrafanaClient(base_url=BASE, ds_uid=DS_UID, token="t0k", timeout=5.0)


@respx.mock
@pytest.mark.asyncio
async def test_instant_query_success():
    respx.get(f"{PROXY}/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"instance": "10.0.0.1:9100"}, "value": [1700000000, "42.5"]}
                    ],
                },
            },
        )
    )
    res = await _client().instant_query('up{job="node"}')
    assert len(res) == 1
    assert first_scalar(res) == pytest.approx(42.5)
    labels_vals = to_label_map(res)
    assert labels_vals[0][0]["instance"] == "10.0.0.1:9100"


@respx.mock
@pytest.mark.asyncio
async def test_instant_query_auth_header_sent():
    route = respx.get(f"{PROXY}/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": []}}
        )
    )
    await _client().instant_query("up")
    assert route.called
    sent = route.calls.last.request
    assert sent.headers.get("authorization") == "Bearer t0k"
    assert sent.url.params.get("query") == "up"


@respx.mock
@pytest.mark.asyncio
async def test_session_login_then_cookie_used():
    login = respx.post(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200,
            json={"message": "Logged in"},
            headers={"Set-Cookie": "grafana_session=sess-abc; Path=/; HttpOnly"},
        )
    )
    query = respx.get(f"{PROXY}/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": []}}
        )
    )
    client = GrafanaClient(base_url=BASE, ds_uid=DS_UID, user="svc", password="p@ss", timeout=5.0)
    await client.instant_query("up")
    assert login.called
    assert query.called
    sent = query.calls.last.request
    assert "grafana_session=sess-abc" in sent.headers.get("cookie", "")
    # session-based → KHÔNG gửi Authorization header
    assert "authorization" not in sent.headers


@respx.mock
@pytest.mark.asyncio
async def test_session_relogin_on_401():
    login = respx.post(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200,
            json={"message": "Logged in"},
            headers={"Set-Cookie": "grafana_session=fresh; Path=/"},
        )
    )

    calls = {"n": 0}

    def _query_side_effect(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"message": "Unauthorized"})
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": []}}
        )

    query = respx.get(f"{PROXY}/query").mock(side_effect=_query_side_effect)
    client = GrafanaClient(base_url=BASE, ds_uid=DS_UID, user="svc", password="pw", timeout=5.0)
    await client.instant_query("up")
    assert query.call_count == 2   # 401 rồi retry thành công
    assert login.call_count == 2   # login ban đầu + re-login sau 401


@respx.mock
@pytest.mark.asyncio
async def test_instant_query_http_error_raises():
    respx.get(f"{PROXY}/query").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(GrafanaError):
        await _client().instant_query("up")


@respx.mock
@pytest.mark.asyncio
async def test_instant_query_prometheus_error_raises():
    respx.get(f"{PROXY}/query").mock(
        return_value=httpx.Response(200, json={"status": "error", "error": "bad query"})
    )
    with pytest.raises(GrafanaError):
        await _client().instant_query("up")


@respx.mock
@pytest.mark.asyncio
async def test_label_values():
    respx.get(f"{PROXY}/label/cluster/values").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["pxc-prod-1", "pxc-prod-2"]}
        )
    )
    out = await _client().label_values("cluster")
    assert out == ["pxc-prod-1", "pxc-prod-2"]


@respx.mock
@pytest.mark.asyncio
async def test_series_with_selector():
    route = respx.get(f"{PROXY}/series").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {"__name__": "up", "job": "node", "instance": "10.0.0.1:9100"},
                    {"__name__": "up", "job": "mysqld", "instance": "10.0.0.1:9104",
                     "cluster": "pxc-prod-1"},
                ],
            },
        )
    )
    out = await _client().series('{instance=~"10.0.0.1(:.*)?"}')
    assert route.called
    assert out[0]["job"] == "node"
    assert out[1]["cluster"] == "pxc-prod-1"


@respx.mock
@pytest.mark.asyncio
async def test_range_query_passes_params():
    route = respx.get(f"{PROXY}/query_range").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "matrix", "result": []},
            },
        )
    )
    await _client().range_query("up", start=1.0, end=2.0, step="30s")
    sent = route.calls.last.request
    assert sent.url.params.get("start") == "1.0"
    assert sent.url.params.get("end") == "2.0"
    assert sent.url.params.get("step") == "30s"


@respx.mock
@pytest.mark.asyncio
async def test_numeric_ds_uses_numeric_proxy_path():
    numeric_proxy = f"{BASE}/api/datasources/proxy/104/api/v1"
    route = respx.get(f"{numeric_proxy}/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": []}}
        )
    )
    client = GrafanaClient(base_url=BASE, ds_uid="104", token="t0k", timeout=5.0)
    await client.instant_query("up")
    assert route.called


def test_first_scalar_empty():
    assert first_scalar([]) is None


def test_first_scalar_invalid_value():
    assert first_scalar([{"value": [1, "not_a_number"]}]) is None
