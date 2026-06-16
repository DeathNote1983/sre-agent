"""Test AgentMemory với MemoryClient giả (không gọi mạng)."""
from __future__ import annotations

import pytest

from src.memory_store import AgentMemory


class _Payload:
    def __init__(self, role, message):
        self.role = role
        self.message = message


class _Event:
    def __init__(self, role, message):
        self.payload = _Payload(role, message)


class _ListResult:
    def __init__(self, items):
        self.list_data = items


class _Record:
    def __init__(self, memory, score=0.9):
        self.memory = memory
        self.score = score


class FakeClient:
    def __init__(self):
        self.events: list[dict] = []
        self.created: list[tuple] = []
        self.search_results: list[_Record] = []
        self.raise_on: set[str] = set()
        self.last_namespace: str | None = None

    async def list_events_async(self, id, actorId, sessionId, page, size):
        if "list" in self.raise_on:
            raise RuntimeError("boom")
        items = [_Event(e["role"], e["content"]) for e in reversed(self.events)][:size]
        return _ListResult(items)

    async def create_event_async(self, id, actorId, sessionId, request):
        if "create" in self.raise_on:
            raise RuntimeError("boom")
        p = request.payload
        self.created.append((actorId, sessionId, p.role, p.message))
        self.events.append({"role": p.role, "content": p.message})

    async def search_memory_records_async(self, id, namespace, request):
        if "search" in self.raise_on:
            raise RuntimeError("boom")
        self.last_namespace = namespace
        return list(self.search_results)


@pytest.mark.asyncio
async def test_append_and_load_history_chronological():
    fc = FakeClient()
    mem = AgentMemory(memory_id="mem-1", strategy_id="strat-1", client=fc)
    await mem.append_turn("u1", "s1", "user", "hello")
    await mem.append_turn("u1", "s1", "assistant", "hi there")
    hist = await mem.load_history("u1", "s1")
    assert hist == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    assert len(fc.created) == 2


@pytest.mark.asyncio
async def test_recall_uses_actor_namespace():
    fc = FakeClient()
    fc.search_results = [_Record("user prefers X"), _Record("host 10.x is master")]
    mem = AgentMemory(memory_id="mem-1", strategy_id="strat-1", client=fc)
    facts = await mem.recall("u1", "tình hình host")
    assert facts == ["user prefers X", "host 10.x is master"]
    assert fc.last_namespace == "/strategies/strat-1/actors/u1"


@pytest.mark.asyncio
async def test_disabled_uses_in_memory_cache():
    mem = AgentMemory(memory_id=None)  # disabled -> không tạo client
    assert mem.enabled is False
    await mem.append_turn("u1", "s1", "user", "hi")
    hist = await mem.load_history("u1", "s1")
    assert hist == [{"role": "user", "content": "hi"}]
    assert await mem.recall("u1", "q") == []  # recall no-op khi disabled


@pytest.mark.asyncio
async def test_load_history_falls_back_to_cache_on_error():
    fc = FakeClient()
    fc.raise_on = {"list"}  # list_events lỗi -> fallback cache
    mem = AgentMemory(memory_id="mem-1", strategy_id="strat-1", client=fc)
    await mem.append_turn("u1", "s1", "user", "hi")  # create ok -> cache có
    hist = await mem.load_history("u1", "s1")
    assert hist == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_recall_returns_empty_on_error():
    fc = FakeClient()
    fc.raise_on = {"search"}
    mem = AgentMemory(memory_id="mem-1", strategy_id="strat-1", client=fc)
    assert await mem.recall("u1", "q") == []
