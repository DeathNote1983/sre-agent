"""Đánh giá rule-based: input metrics + thresholds, output verdict.

Pure function — không I/O, dễ test. LLM dùng kết quả này để diễn giải,
không tự "phán" số liệu để tránh ảo giác.
"""
from __future__ import annotations

from typing import Literal

from src.config import Thresholds

Status = Literal["OK", "WARN", "CRIT"]
_RANK = {"OK": 0, "WARN": 1, "CRIT": 2}


def _worse(a: Status, b: Status) -> Status:
    return a if _RANK[a] >= _RANK[b] else b


def _bound(value: float, warn: float, crit: float) -> Status:
    """Threshold tăng dần (giá trị cao là xấu)."""
    if value >= crit:
        return "CRIT"
    if value >= warn:
        return "WARN"
    return "OK"


def assess_linux(metrics: dict, t: Thresholds) -> dict:
    """metrics: {cpu_pct, ram_pct, disk_used_pct: list[{mount, pct}], disk_io_util: list[{device, pct}], load_per_cpu}."""
    lt = t.linux
    reasons: list[str] = []
    status: Status = "OK"

    cpu = float(metrics.get("cpu_pct") or 0)
    s = _bound(cpu, lt.cpu_pct.warn, lt.cpu_pct.crit)
    if s != "OK":
        reasons.append(f"CPU {cpu:.1f}% (>{lt.cpu_pct.warn if s == 'WARN' else lt.cpu_pct.crit}%)")
    status = _worse(status, s)

    ram = float(metrics.get("ram_pct") or 0)
    s = _bound(ram, lt.ram_pct.warn, lt.ram_pct.crit)
    if s != "OK":
        reasons.append(f"RAM {ram:.1f}% (>{lt.ram_pct.warn if s == 'WARN' else lt.ram_pct.crit}%)")
    status = _worse(status, s)

    for disk in metrics.get("disk_used_pct") or []:
        pct = float(disk.get("pct") or 0)
        mount = disk.get("mount", "?")
        s = _bound(pct, lt.disk_used_pct.warn, lt.disk_used_pct.crit)
        if s != "OK":
            reasons.append(f"Disk {mount} used {pct:.1f}%")
        status = _worse(status, s)

    for dev in metrics.get("disk_io_util") or []:
        pct = float(dev.get("pct") or 0)
        name = dev.get("device", "?")
        s = _bound(pct, lt.disk_io_util.warn, lt.disk_io_util.crit)
        if s != "OK":
            reasons.append(f"Disk IO util {name} {pct:.1f}%")
        status = _worse(status, s)

    load = float(metrics.get("load_per_cpu") or 0)
    s = _bound(load, lt.load_per_cpu.warn, lt.load_per_cpu.crit)
    if s != "OK":
        reasons.append(f"Load/CPU {load:.2f}")
    status = _worse(status, s)

    suggestion = _linux_suggestion(status, reasons, metrics)
    return {"status": status, "reasons": reasons, "suggestion": suggestion}


def _linux_suggestion(status: Status, reasons: list[str], metrics: dict) -> str | None:
    if status == "OK":
        return None
    hints: list[str] = []
    cpu = float(metrics.get("cpu_pct") or 0)
    ram = float(metrics.get("ram_pct") or 0)
    if cpu >= 90:
        hints.append("CPU sát ngưỡng — xem xét scale-up CPU hoặc scale-out node")
    if ram >= 90:
        hints.append("RAM sát ngưỡng — tăng RAM hoặc kiểm tra memory leak")
    for disk in metrics.get("disk_used_pct") or []:
        if float(disk.get("pct") or 0) >= 90:
            hints.append(f"Disk {disk.get('mount')} sắp đầy — dọn log/snapshot hoặc tăng dung lượng")
    return "; ".join(hints) if hints else "Cần theo dõi sát, kiểm tra workload bất thường"


def assess_pxc(metrics: dict, t: Thresholds) -> dict:
    """metrics: {size, primary_count, nodes: [{ip, wsrep_local_state, queue_avg}], flow_control_paused_pct, max_lag}."""
    pt = t.pxc
    reasons: list[str] = []
    status: Status = "OK"

    size = int(metrics.get("size") or 0)
    if size < pt.cluster_size_min:
        reasons.append(f"Cluster size {size} < min {pt.cluster_size_min}")
        status = _worse(status, "CRIT")

    primary = int(metrics.get("primary_count") or 0)
    if primary != size and size > 0:
        reasons.append(f"Chỉ {primary}/{size} node trong PRIMARY component")
        status = _worse(status, "CRIT")

    for n in metrics.get("nodes") or []:
        state = n.get("wsrep_local_state")
        if state is not None and int(state) != pt.wsrep_local_state_ok:
            reasons.append(
                f"Node {n.get('ip')} wsrep_local_state={state} (≠ {pt.wsrep_local_state_ok} Synced)"
            )
            status = _worse(status, "CRIT")
        q = float(n.get("queue_avg") or 0)
        s = _bound(q, pt.queue_avg.warn, pt.queue_avg.crit)
        if s != "OK":
            reasons.append(f"Node {n.get('ip')} queue_avg={q:.1f}")
        status = _worse(status, s)

    fc = float(metrics.get("flow_control_paused_pct") or 0)
    s = _bound(fc, pt.flow_control_paused_pct.warn, pt.flow_control_paused_pct.crit)
    if s != "OK":
        reasons.append(f"Flow-control paused {fc:.1f}%")
    status = _worse(status, s)

    suggestion = _pxc_suggestion(status, metrics)
    return {"status": status, "reasons": reasons, "suggestion": suggestion}


def _pxc_suggestion(status: Status, metrics: dict) -> str | None:
    if status == "OK":
        return None
    hints: list[str] = []
    fc = float(metrics.get("flow_control_paused_pct") or 0)
    if fc >= 20:
        hints.append("Flow-control cao — có node chậm, kiểm tra IO/CPU node yếu nhất hoặc tăng wsrep_slave_threads")
    for n in metrics.get("nodes") or []:
        if n.get("wsrep_local_state") is not None and int(n["wsrep_local_state"]) != 4:
            hints.append(f"Node {n.get('ip')} chưa Synced — check log, SST/IST progress")
    if int(metrics.get("primary_count") or 0) < int(metrics.get("size") or 0):
        hints.append("Có node rớt PRIMARY — risk split-brain, cần intervene gấp")
    return "; ".join(hints) if hints else "Theo dõi cluster sát"


def assess_redis(metrics: dict, t: Thresholds) -> dict:
    """metrics: {nodes: [{ip, role, master_link_status}], cluster_state, slots_ok, used_memory_pct, ops_per_sec, evicted_keys_rate}."""
    rt = t.redis
    reasons: list[str] = []
    status: Status = "OK"

    cs = metrics.get("cluster_state")
    if cs and cs != rt.cluster_state_ok:
        reasons.append(f"cluster_state={cs} (≠ ok)")
        status = _worse(status, "CRIT")

    slots = int(metrics.get("slots_ok") or 0)
    if slots and slots < 16384:
        reasons.append(f"Slots OK {slots}/16384 — có slot chưa cover")
        status = _worse(status, "CRIT")

    mem = float(metrics.get("used_memory_pct") or 0)
    s = _bound(mem, rt.used_memory_pct.warn, rt.used_memory_pct.crit)
    if s != "OK":
        reasons.append(f"Used memory {mem:.1f}%")
    status = _worse(status, s)

    ev = float(metrics.get("evicted_keys_rate") or 0)
    s = _bound(ev, rt.evicted_keys_rate.warn, rt.evicted_keys_rate.crit)
    if s != "OK":
        reasons.append(f"Eviction rate {ev:.1f} keys/s")
    status = _worse(status, s)

    for n in metrics.get("nodes") or []:
        if n.get("role") == "slave":
            link = n.get("master_link_status")
            if link and link != rt.master_link_ok:
                reasons.append(f"Slave {n.get('ip')} master_link={link}")
                status = _worse(status, "CRIT")

    suggestion = _redis_suggestion(status, metrics)
    return {"status": status, "reasons": reasons, "suggestion": suggestion}


def _redis_suggestion(status: Status, metrics: dict) -> str | None:
    if status == "OK":
        return None
    hints: list[str] = []
    mem = float(metrics.get("used_memory_pct") or 0)
    if mem >= 90:
        hints.append("Memory sát ngưỡng — tăng maxmemory hoặc scale-out shard, review TTL/eviction policy")
    if float(metrics.get("evicted_keys_rate") or 0) >= 1000:
        hints.append("Eviction rate cao — workload vượt capacity, cần scale RAM hoặc tối ưu key size")
    if metrics.get("cluster_state") and metrics["cluster_state"] != "ok":
        hints.append("Cluster không OK — chạy CLUSTER INFO, fix slot assignment / node failure")
    return "; ".join(hints) if hints else "Theo dõi load và memory growth"


def assess(metrics: dict, tech: str, thresholds: Thresholds) -> dict:
    """Dispatcher theo tech: linux | pxc | redis."""
    tech = tech.lower()
    if tech == "linux":
        return assess_linux(metrics, thresholds)
    if tech == "pxc":
        return assess_pxc(metrics, thresholds)
    if tech == "redis":
        return assess_redis(metrics, thresholds)
    return {
        "status": "OK",
        "reasons": [],
        "suggestion": f"Tech '{tech}' chưa được hỗ trợ đánh giá",
    }
