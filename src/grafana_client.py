"""Wrapper Grafana datasource proxy API (PromQL)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class GrafanaError(RuntimeError):
    """Lỗi từ Grafana/Prometheus query."""


@dataclass
class GrafanaClient:
    base_url: str            # https://grafana.internal/...
    ds_uid: str              # datasource UID, vd "prometheus"
    token: str | None = None       # API key (Bearer) — dùng khi không có user/password
    user: str | None = None        # form-login user (ưu tiên hơn token nếu có cả password)
    password: str | None = None    # form-login password
    timeout: float = 15.0
    # session cookie lấy từ POST /login, tái dùng cho các call sau (re-login khi 401)
    _session_cookie: str | None = field(default=None, init=False, repr=False, compare=False)

    def _proxy_root(self, ds: str | None = None) -> str:
        # Grafana datasource proxy:
        #   - numeric id -> /api/datasources/proxy/<id>/api/v1       (Grafana 7.x)
        #   - string uid -> /api/datasources/proxy/uid/<uid>/api/v1  (Grafana 9+)
        # ds có thể là id số ("104") hoặc uid chuỗi; None -> DS gốc (self.ds_uid).
        d = str(ds or self.ds_uid)
        seg = d if d.isdigit() else f"uid/{d}"
        return f"{self.base_url}/api/datasources/proxy/{seg}/api/v1"

    async def _login(self) -> None:
        """Đăng nhập form-login Grafana bằng user/password → lưu grafana_session cookie.

        Dùng khi Grafana tắt basic auth / API key nhưng vẫn cho đăng nhập bằng
        username + password như browser. Cookie được tái dùng cho các call sau.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/login",
                json={"user": self.user, "password": self.password},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        if resp.status_code != 200:
            raise GrafanaError(f"Grafana login {resp.status_code}: {resp.text[:200]}")
        cookie = resp.cookies.get("grafana_session")
        if not cookie:
            raise GrafanaError("Grafana login không trả về cookie grafana_session")
        self._session_cookie = cookie

    async def _get(
        self, path: str, params: dict[str, Any], ds: str | None = None, _retry: bool = True
    ) -> dict:
        url = f"{self._proxy_root(ds)}{path}"
        headers = {"Accept": "application/json"}

        if self.user and self.password:
            # Ưu tiên session login (user/pass). Login lazily nếu chưa có cookie.
            if not self._session_cookie:
                await self._login()
            headers["Cookie"] = f"grafana_session={self._session_cookie}"
        elif self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=headers)

        # Session hết hạn → login lại và thử đúng 1 lần
        if resp.status_code == 401 and self.user and self.password and _retry:
            self._session_cookie = None
            await self._login()
            return await self._get(path, params, ds=ds, _retry=False)

        if resp.status_code != 200:
            raise GrafanaError(
                f"Grafana {resp.status_code} for {path}: {resp.text[:200]}"
            )
        body = resp.json()
        if body.get("status") != "success":
            raise GrafanaError(f"Prometheus error: {body.get('error') or body}")
        return body.get("data", {})

    async def instant_query(self, promql: str, ds: str | None = None) -> list[dict]:
        """Query instant. Trả về list[{metric: {...}, value: [ts, val_str]}]."""
        data = await self._get("/query", {"query": promql}, ds=ds)
        if data.get("resultType") not in ("vector", "scalar"):
            return []
        return data.get("result", [])

    async def range_query(
        self, promql: str, start: float, end: float, step: str = "60s", ds: str | None = None
    ) -> list[dict]:
        """Query range. Trả về list[{metric: {...}, values: [[ts, val_str], ...]}]."""
        data = await self._get(
            "/query_range",
            {"query": promql, "start": start, "end": end, "step": step},
            ds=ds,
        )
        return data.get("result", [])

    async def label_values(
        self, label: str, match: str | None = None, ds: str | None = None
    ) -> list[str]:
        """Lấy giá trị của 1 label, optional match[] series selector."""
        params: dict[str, Any] = {}
        if match:
            params["match[]"] = match
        data = await self._get(f"/label/{label}/values", params, ds=ds)
        if isinstance(data, list):
            return data
        return []

    async def series(self, match: str, ds: str | None = None) -> list[dict[str, str]]:
        """Lấy series metadata match selector."""
        data = await self._get("/series", {"match[]": match}, ds=ds)
        if isinstance(data, list):
            return data
        return []


def first_scalar(result: list[dict]) -> float | None:
    """Helper: lấy giá trị float đầu tiên từ instant_query result."""
    for item in result:
        val = item.get("value")
        if val and len(val) == 2:
            try:
                return float(val[1])
            except (TypeError, ValueError):
                continue
    return None


def to_label_map(result: list[dict]) -> list[tuple[dict[str, str], float]]:
    """Chuyển instant_query result -> list[(labels, value)]."""
    out: list[tuple[dict[str, str], float]] = []
    for item in result:
        labels = item.get("metric") or {}
        val = item.get("value")
        if val and len(val) == 2:
            try:
                out.append((labels, float(val[1])))
            except (TypeError, ValueError):
                continue
    return out
