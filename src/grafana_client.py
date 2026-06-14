"""Wrapper Grafana datasource proxy API (PromQL)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class GrafanaError(RuntimeError):
    """Lỗi từ Grafana/Prometheus query."""


@dataclass
class GrafanaClient:
    base_url: str            # https://grafana.internal/...
    ds_uid: str              # datasource UID, vd "prometheus"
    token: str
    timeout: float = 15.0

    @property
    def _proxy_root(self) -> str:
        # Grafana datasource proxy v9+: /api/datasources/proxy/uid/<uid>/api/v1
        return f"{self.base_url}/api/datasources/proxy/uid/{self.ds_uid}/api/v1"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    async def _get(self, path: str, params: dict[str, Any]) -> dict:
        url = f"{self._proxy_root}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=self._headers)
        if resp.status_code != 200:
            raise GrafanaError(
                f"Grafana {resp.status_code} for {path}: {resp.text[:200]}"
            )
        body = resp.json()
        if body.get("status") != "success":
            raise GrafanaError(f"Prometheus error: {body.get('error') or body}")
        return body.get("data", {})

    async def instant_query(self, promql: str) -> list[dict]:
        """Query instant. Trả về list[{metric: {...}, value: [ts, val_str]}]."""
        data = await self._get("/query", {"query": promql})
        if data.get("resultType") not in ("vector", "scalar"):
            return []
        return data.get("result", [])

    async def range_query(
        self, promql: str, start: float, end: float, step: str = "60s"
    ) -> list[dict]:
        """Query range. Trả về list[{metric: {...}, values: [[ts, val_str], ...]}]."""
        data = await self._get(
            "/query_range",
            {"query": promql, "start": start, "end": end, "step": step},
        )
        return data.get("result", [])

    async def label_values(self, label: str, match: str | None = None) -> list[str]:
        """Lấy giá trị của 1 label, optional match[] series selector."""
        params: dict[str, Any] = {}
        if match:
            params["match[]"] = match
        data = await self._get(f"/label/{label}/values", params)
        if isinstance(data, list):
            return data
        return []

    async def series(self, match: str) -> list[dict[str, str]]:
        """Lấy series metadata match selector."""
        data = await self._get("/series", {"match[]": match})
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
