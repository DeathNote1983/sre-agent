"""Tests cho apply_extra_hosts (ghi host alias vào /etc/hosts)."""
from __future__ import annotations

from src.extra_hosts import apply_extra_hosts

ENTRY = "49.213.117.10 dashboard.zalopay.vn"


def test_appends_entry(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    apply_extra_hosts(str(hosts), {"EXTRA_HOSTS": ENTRY})
    assert ENTRY in hosts.read_text()


def test_idempotent(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    env = {"EXTRA_HOSTS": ENTRY}
    apply_extra_hosts(str(hosts), env)
    apply_extra_hosts(str(hosts), env)
    assert hosts.read_text().count(ENTRY) == 1


def test_multiple_entries(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    apply_extra_hosts(
        str(hosts), {"EXTRA_HOSTS": "10.0.0.1 a.example.com; 10.0.0.2 b.example.com"}
    )
    content = hosts.read_text()
    assert "10.0.0.1 a.example.com" in content
    assert "10.0.0.2 b.example.com" in content


def test_noop_when_unset(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    apply_extra_hosts(str(hosts), {})
    assert hosts.read_text() == "127.0.0.1 localhost\n"
