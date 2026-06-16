"""Tích hợp AgentBase Memory cho agent SRE.

- Short-term: lưu mỗi lượt hội thoại (user/assistant) thành event → sống qua restart.
- Long-term: recall semantic facts (memory records) liên quan để chèn vào context.

Graceful-degrade: nếu MEMORY_ID không cấu hình hoặc Memory service lỗi, dùng cache
in-memory (theo từng (actor, session)) để bot vẫn giữ context trong vòng đời tiến trình.
Trên AgentBase Runtime, creds (GREENNODE_CLIENT_*) được inject sẵn nên MemoryClient tự
xác thực; local thì đọc .greennode.json.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _make_client() -> Any:
    """Tạo MemoryClient thật (import lazily để test inject client giả được)."""
    from greennode_agentbase.memory import MemoryClient

    return MemoryClient()


class AgentMemory:
    def __init__(
        self,
        memory_id: str | None,
        strategy_id: str | None = None,
        client: Any | None = None,
    ):
        self.memory_id = memory_id
        self.strategy_id = strategy_id
        self.enabled = bool(memory_id)
        self._client = client if client is not None else (_make_client() if self.enabled else None)
        # fallback cache theo (actor, session) khi memory tắt/lỗi
        self._cache: dict[tuple[str, str], list[dict[str, str]]] = {}

    async def load_history(
        self, actor: str, session: str, limit: int = 12
    ) -> list[dict[str, str]]:
        """Trả lịch sử hội thoại (chronological) [{'role','content'}]."""
        if self.enabled:
            try:
                res = await self._client.list_events_async(
                    id=self.memory_id, actorId=actor, sessionId=session, page=1, size=limit
                )
                items = getattr(res, "list_data", None) or getattr(res, "listData", None) or []
                turns: list[dict[str, str]] = []
                for ev in reversed(list(items)):  # API trả newest-first → đảo lại
                    p = getattr(ev, "payload", None)
                    role = getattr(p, "role", None) if p else None
                    msg = getattr(p, "message", None) if p else None
                    if role in ("user", "assistant") and msg:
                        turns.append({"role": role, "content": msg})
                return turns
            except Exception:
                logger.warning("memory load_history lỗi, dùng cache", exc_info=True)
        return list(self._cache.get((actor, session), []))[-limit:]

    async def append_turn(self, actor: str, session: str, role: str, content: str) -> None:
        """Ghi 1 lượt vào memory (+ cache fallback)."""
        if not content:
            return
        cache = self._cache.setdefault((actor, session), [])
        cache.append({"role": role, "content": content})
        if len(cache) > 40:
            del cache[:-40]
        if self.enabled:
            try:
                from greennode_agentbase.memory.models import EventCreateRequest, EventPayload

                req = EventCreateRequest(
                    payload=EventPayload(type="conversational", role=role, message=content)
                )
                await self._client.create_event_async(
                    id=self.memory_id, actorId=actor, sessionId=session, request=req
                )
            except Exception:
                logger.warning("memory append_turn lỗi", exc_info=True)

    async def recall(self, actor: str, query: str, limit: int = 5) -> list[str]:
        """Tìm semantic facts liên quan của user (namespace theo strategy + actor)."""
        if not (self.enabled and self.strategy_id and query):
            return []
        try:
            from greennode_agentbase.memory.models import MemoryRecordSearchRequest

            namespace = f"/strategies/{self.strategy_id}/actors/{actor}"
            res = await self._client.search_memory_records_async(
                id=self.memory_id,
                namespace=namespace,
                request=MemoryRecordSearchRequest(query=query, limit=max(limit, 5)),
            )
            recs = res if isinstance(res, list) else (getattr(res, "list_data", None) or [])
            return [m for m in (getattr(r, "memory", None) for r in recs) if m]
        except Exception:
            logger.warning("memory recall lỗi", exc_info=True)
            return []
