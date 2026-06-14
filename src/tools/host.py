"""get_host_metrics: lấy CPU/RAM/DISK/IO/load của 1 host (node_exporter)."""
from __future__ import annotations

import asyncio
from typing import Any

from src.grafana_client import GrafanaClient, first_scalar, to_label_map


def _inst_selector(ip: str) -> str:
    """Match instance="<ip>:<any port>" — node_exporter thường :9100."""
    return f'instance=~"{ip}(:.*)?"'


async def get_host_metrics(client: GrafanaClient, ip: str, range_: str = "5m") -> dict[str, Any]:
    """Trả về dict metrics + raw để LLM hiểu context."""
    sel = _inst_selector(ip)

    cpu_q = (
        f'100 - (avg by (instance) (rate(node_cpu_seconds_total{{mode="idle",{sel}}}[{range_}])) * 100)'
    )
    ram_q = (
        f'(1 - node_memory_MemAvailable_bytes{{{sel}}} / node_memory_MemTotal_bytes{{{sel}}}) * 100'
    )
    # Loại bỏ tmpfs, overlay khỏi disk usage
    disk_q = (
        f'100 * (1 - node_filesystem_avail_bytes{{{sel},fstype!~"tmpfs|overlay|squashfs"}} '
        f'/ node_filesystem_size_bytes{{{sel},fstype!~"tmpfs|overlay|squashfs"}})'
    )
    io_q = f'rate(node_disk_io_time_seconds_total{{{sel}}}[{range_}]) * 100'
    load1_q = f'node_load1{{{sel}}}'
    cpu_count_q = f'count by (instance) (node_cpu_seconds_total{{mode="idle",{sel}}})'

    cpu_r, ram_r, disk_r, io_r, load_r, cpu_cnt_r = await asyncio.gather(
        client.instant_query(cpu_q),
        client.instant_query(ram_q),
        client.instant_query(disk_q),
        client.instant_query(io_q),
        client.instant_query(load1_q),
        client.instant_query(cpu_count_q),
    )

    cpu_pct = first_scalar(cpu_r)
    ram_pct = first_scalar(ram_r)
    load1 = first_scalar(load_r)
    cpu_count = first_scalar(cpu_cnt_r) or 1.0

    disk_used: list[dict] = []
    for labels, val in to_label_map(disk_r):
        mount = labels.get("mountpoint") or labels.get("device") or "?"
        # Bỏ mount path "lặt vặt"
        if mount.startswith(("/proc", "/sys", "/run")):
            continue
        disk_used.append({"mount": mount, "pct": round(val, 2)})

    disk_io: list[dict] = []
    for labels, val in to_label_map(io_r):
        dev = labels.get("device") or "?"
        # Loại loop, ram
        if dev.startswith(("loop", "ram")):
            continue
        disk_io.append({"device": dev, "pct": round(val, 2)})

    load_per_cpu = (load1 / cpu_count) if (load1 is not None and cpu_count) else 0.0

    return {
        "ip": ip,
        "cpu_pct": round(cpu_pct, 2) if cpu_pct is not None else None,
        "ram_pct": round(ram_pct, 2) if ram_pct is not None else None,
        "disk_used_pct": disk_used,
        "disk_io_util": disk_io,
        "load1": load1,
        "cpu_count": int(cpu_count),
        "load_per_cpu": round(load_per_cpu, 3),
    }
