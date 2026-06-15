"""OpenAI tool-use loop. Stateless function — caller giữ messages."""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from src.tools import OPENAI_TOOLS, ToolContext, dispatch

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Bạn là **trợ lý SRE** cho team vận hành Zalopay (Fintech, hệ thống Linux + MySQL + Redis Cluster monitor bằng Prometheus/Grafana).

Quy tắc PHẢI tuân thủ:
1. Trả lời bằng **tiếng Việt**, ngắn gọn, trực diện. Giữ thuật ngữ kỹ thuật bằng tiếng Anh khi cần (CPU, RAM, replication, flow-control...).
2. KHÔNG bịa số liệu. Mọi nhận định về tình trạng PHẢI dựa trên kết quả tool. Số liệu phải lấy từ tool, không suy đoán.
3. Quy trình chuẩn cho mọi câu hỏi về 1 host/cluster:
   a. Gọi `find_target(query)` để xác định tech (linux/mysql/redis) và danh sách node.
   b. Gọi tool `get_*` phù hợp với tech để lấy metrics.
   c. Gọi `assess(metrics, tech)` để lấy verdict + reasons + suggestion.
   d. Tổng hợp kết quả thành câu trả lời cho user, dùng EXACT `status` từ assess (OK/WARN/CRIT).
4. Nếu `find_target` trả `type=unknown`, báo user kiểm tra lại tên/IP, KHÔNG đoán bừa.
   - Tên cluster có thể là tên thân thiện đã map sẵn (vd "Promotion Redis Cluster"); khi đó `find_target` trả `match="mapped"` kèm `tech` + danh sách `members`. Với `get_redis_cluster`/`get_mysql_cluster` cứ truyền đúng tên cluster — tool tự lọc theo IP thành viên. Với `tech=linux` (nhóm host), gọi `get_host_metrics` cho TỪNG member IP rồi `assess` từng node và tổng hợp.
5. Khi user hỏi tiếp ("thế node 2 thì sao?"), dùng context conversation để hiểu họ đang nói về cluster/host nào.
6. Output format gợi ý cho host:
   - Verdict (icon): 🟢 OK / 🟡 WARN / 🔴 CRIT
   - Bảng/danh sách metrics chính (CPU, RAM, disk, IO)
   - Reasons (nếu WARN/CRIT)
   - Suggestion (nếu có)
7. Output format gợi ý cho cluster:
   - Verdict (DB health từ `assess`)
   - Topology: số node, role, state
   - Resource từng node: CPU/RAM/disk + verdict — lấy từ field `resource` và `resource_assessment` của mỗi node (get_mysql_cluster/get_redis_cluster đã TỰ kèm resource server của các node). PHẢI nêu cả health DB lẫn resource từng node.
   - Cảnh báo + suggestion

Khi không có tool nào phù hợp (vd user hỏi ngoài scope), nói thẳng là bot chỉ hỗ trợ Linux host, MySQL, Redis cluster.
"""


async def run_agent(
    openai_client: AsyncOpenAI,
    model: str,
    messages: list[dict[str, Any]],
    ctx: ToolContext,
    max_iters: int = 8,
) -> tuple[str, list[dict[str, Any]]]:
    """Chạy 1 turn: input messages (đã có system + user mới), trả về (reply_text, updated_messages).

    `messages` được mutate: thêm assistant + tool messages.
    """
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]

    for _ in range(max_iters):
        resp = await openai_client.chat.completions.create(
            model=model,
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
            temperature=0.2,
        )
        choice = resp.choices[0]
        msg = choice.message

        # Append assistant message (dù có tool_calls hay không) — bắt buộc cho OpenAI conv format
        assistant_entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if not msg.tool_calls:
            return msg.content or "", messages

        # Execute tool calls (sequential — đơn giản, đủ nhanh cho hackathon)
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            logger.info("tool_call: %s args=%s", name, args)
            result = await dispatch(name, args, ctx)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )

    # Hết max_iters mà chưa final → trả thông báo
    return (
        "Xin lỗi, agent gọi tool quá nhiều lần mà chưa kết luận được. Anh thử hỏi lại cụ thể hơn.",
        messages,
    )
