# 🤖 SRE Agent

**Trợ lý SRE thông minh trên Telegram** — hỏi bằng ngôn ngữ tự nhiên, nhận phân tích tình trạng Linux host, MySQL cluster, và Redis cluster theo thời gian thực từ Prometheus/Grafana.

> Dự án phát triển trong khuôn khổ **Claw-a-thon Hackathon** — team vận hành ZaloPay.

---

## ✨ Tính năng chính

| Tính năng | Mô tả |
|-----------|--------|
| 🐧 **Linux Host Monitoring** | CPU, RAM, Disk usage, Disk I/O utilization, Load average |
| 🐬 **MySQL Cluster Health** | Up/down, connections saturation, replication (IO/SQL thread + lag), slow queries, QPS |
| 🔴 **Redis Cluster Health** | Cluster state, slot coverage, memory usage, eviction rate, master-link status |
| 🔍 **Auto-discovery** | Tự tìm host/cluster qua IP hoặc tên thân thiện (mapping config hoặc Prometheus labels) |
| ⚖️ **Rule-based Assessment** | Đánh giá OK / WARN / CRIT theo ngưỡng cấu hình, kèm lý do + suggestion cụ thể |
| 🧠 **Long-term Memory** | Ghi nhớ context hội thoại & facts về user/hệ thống qua GreenNode AgentBase Memory |
| 🔐 **Whitelist Access** | Chỉ user Telegram được phép trong whitelist mới dùng được bot |

---

## 🏗️ Kiến trúc tổng quan

```
┌──────────────┐     ┌──────────────────┐     ┌───────────────────┐
│   Telegram   │◄───►│   SRE Agent Bot  │────►│  LLM (OpenAI API) │
│   (User)     │     │  (python-tg-bot) │     │  (GreenNode AIP)  │
└──────────────┘     └────────┬─────────┘     └───────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │   Tool Dispatcher   │
                    │  (function calling) │
                    └─┬───┬───┬───┬───┬──┘
                      │   │   │   │   │
              ┌───────┘   │   │   │   └───────┐
              ▼           ▼   ▼   ▼           ▼
         find_target  get_host get_mysql get_redis  assess
              │           │   │   │           │
              └─────┬─────┘   │   └─────┬─────┘
                    ▼         ▼         ▼
              ┌──────────────────────────────┐
              │    Grafana Datasource Proxy   │
              │       (PromQL queries)        │
              └──────────────────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │    Prometheus       │
                    │  (node / mysqld /   │
                    │   redis exporter)   │
                    └────────────────────┘
```

---

## 📂 Cấu trúc dự án

```
sre-agent/
├── src/
│   ├── main.py              # Entrypoint: load config, start Telegram polling
│   ├── bot.py               # Telegram handlers, auth, conversation management
│   ├── agent.py             # OpenAI tool-use loop (multi-turn)
│   ├── config.py            # Load & validate config YAML + env vars (Pydantic)
│   ├── grafana_client.py    # Grafana datasource proxy API wrapper (PromQL)
│   ├── memory_store.py      # AgentBase Memory: short-term + long-term + fallback
│   ├── health.py            # HTTP health check server (port 8080) cho AgentBase Runtime
│   ├── extra_hosts.py       # Ghi host alias vào /etc/hosts khi container khởi động
│   └── tools/
│       ├── __init__.py      # Tool registry, schema OpenAI function calling, dispatcher
│       ├── discovery.py     # find_target: tìm host/cluster qua IP hoặc tên
│       ├── host.py          # get_host_metrics: CPU/RAM/disk/IO/load (node_exporter)
│       ├── mysql.py         # get_mysql_cluster: MySQL health (mysql_exporter)
│       ├── redis.py         # get_redis_cluster: Redis Cluster health (redis_exporter)
│       └── assess.py        # Rule-based assessment → OK/WARN/CRIT + reasons + suggestion
├── config/
│   ├── thresholds.yaml      # Ngưỡng WARN/CRIT cho Linux, MySQL, Redis
│   ├── clusters.yaml        # Mapping tên cluster thân thiện → tech + IP members
│   ├── datasources.yaml     # Mapping tech → Grafana datasource ID/UID
│   └── whitelist.yaml       # Danh sách Telegram user ID được phép dùng bot
├── tests/                   # Unit tests (pytest + pytest-asyncio)
├── Dockerfile               # Python 3.11-slim image
├── docker-compose.yml       # Compose service với config volume
├── pyproject.toml           # Project metadata + dependencies
├── .env.example             # Template biến môi trường
└── README.md
```

---

## 🚀 Bắt đầu nhanh

### Yêu cầu

- **Python** ≥ 3.11
- **Grafana** có datasource proxy tới Prometheus (node_exporter, mysql_exporter, redis_exporter)
- **Telegram Bot Token** (tạo qua [@BotFather](https://t.me/BotFather))
- **LLM API Key** — GreenNode AI Platform (OpenAI-compatible) hoặc OpenAI API key

### 1. Clone & cài đặt

```bash
git clone <repository-url>
cd sre-agent

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

### 2. Cấu hình

Tạo file `.env` từ template:

```bash
cp .env.example .env
```

Chỉnh sửa `.env` với giá trị thật:

```env
# Grafana — cần ít nhất 1 trong 2 phương thức xác thực
GRAFANA_URL=https://your-grafana.example.com
GRAFANA_DS_UID=prometheus
GRAFANA_TOKEN=glsa_xxxxxxxxxxxx          # API key (Bearer)
# hoặc dùng session login:
# GRAFANA_USER=admin
# GRAFANA_PASSWORD=secret

# LLM
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1
LLM_MODEL=qwen/qwen3-5-27b

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...

# Optional
LOG_LEVEL=INFO
SESSION_IDLE_MINUTES=30
EXTRA_HOSTS=118.102.5.66 dev-dashboard.zalopay.vn
```

Chỉnh sửa các file trong `config/`:

| File | Mục đích |
|------|----------|
| `thresholds.yaml` | Ngưỡng WARN / CRIT cho từng loại metric |
| `clusters.yaml` | Mapping tên cluster thân thiện → tech + danh sách IP thành viên |
| `datasources.yaml` | Mapping tech (host/mysql/redis) → Grafana datasource ID |
| `whitelist.yaml` | Danh sách Telegram user ID được phép sử dụng bot |

### 3. Chạy

```bash
# Chạy trực tiếp
python -m src.main

# Hoặc dùng Docker
docker compose up --build -d
```

---

## 💬 Cách sử dụng

Mở Telegram, tìm bot và bắt đầu chat:

### Commands

| Command | Mô tả |
|---------|--------|
| `/start` | Khởi tạo bot, xem hướng dẫn |
| `/help` | Xem danh sách tính năng hỗ trợ |
| `/reset` | Xóa context hội thoại hiện tại |

### Ví dụ câu hỏi

```
Host 10.1.2.3 thế nào?
Dev Mysql Cluster còn ổn không?
Redis cache cluster tình trạng sao?
Thế node 2 thì sao?
```

### Quy trình xử lý của Agent

1. **`find_target`** — Xác định target là host hay cluster, tech gì (linux/mysql/redis)
2. **`get_host_metrics`** / **`get_mysql_cluster`** / **`get_redis_cluster`** — Lấy metrics từ Prometheus qua Grafana
3. **`assess`** — Đánh giá rule-based → `OK` 🟢 / `WARN` 🟡 / `CRIT` 🔴
4. **Tổng hợp** — LLM diễn giải kết quả thành câu trả lời tiếng Việt, dễ hiểu

> ⚠️ Agent KHÔNG bịa số liệu. Mọi nhận định đều dựa trên kết quả tool thực tế.

---

## 🧪 Chạy tests

```bash
pytest
```

Test suite bao gồm:
- `test_assess.py` — Rule-based assessment (Linux/MySQL/Redis)
- `test_clusters.py` — Cluster map resolve
- `test_datasources.py` — Datasource mapping
- `test_discovery.py` — Target discovery logic
- `test_grafana_client.py` — Grafana client (mocked HTTP)
- `test_memory_store.py` — Memory store fallback
- `test_redis.py` — Redis cluster metrics
- `test_extra_hosts.py` — Extra hosts /etc/hosts injection
- `test_combine.py` — Combined tool integration

---

## 🐳 Docker

```bash
# Build & chạy
docker compose up --build -d

# Xem logs
docker compose logs -f sre-agent

# Dừng
docker compose down
```

Container tự expose health check tại `GET :8080/health` để tương thích với **AgentBase Runtime**.

---

## 📊 Ngưỡng mặc định

### Linux Host

| Metric | WARN | CRIT |
|--------|------|------|
| CPU % | ≥ 75% | ≥ 90% |
| RAM % | ≥ 80% | ≥ 92% |
| Disk Used % | ≥ 80% | ≥ 90% |
| Disk I/O Util | ≥ 70% | ≥ 90% |
| Load / CPU | ≥ 1.5 | ≥ 3.0 |

### MySQL

| Metric | WARN | CRIT |
|--------|------|------|
| Connections % | ≥ 80% | ≥ 90% |
| Replication Lag | ≥ 30s | ≥ 300s |

### Redis

| Metric | WARN | CRIT |
|--------|------|------|
| Used Memory % | ≥ 80% | ≥ 90% |
| Evicted Keys Rate | ≥ 100/s | ≥ 1000/s |

Tùy chỉnh trong [`config/thresholds.yaml`](config/thresholds.yaml).

---

## 🔧 Tech Stack

| Thành phần | Công nghệ |
|------------|-----------|
| Language | Python 3.11 |
| Telegram SDK | [python-telegram-bot](https://python-telegram-bot.org/) 21.x |
| LLM Client | [openai](https://github.com/openai/openai-python) SDK (OpenAI-compatible) |
| HTTP Client | [httpx](https://www.python-httpx.org/) (async) |
| Config Validation | [Pydantic](https://docs.pydantic.dev/) v2 |
| Config Files | [PyYAML](https://pyyaml.org/) |
| Memory | [greennode-agentbase](https://pypi.org/project/greennode-agentbase/) (AgentBase Memory) |
| Testing | pytest + pytest-asyncio + respx |
| Container | Docker (python:3.11-slim) |

---

## 📜 License

Internal project — ZaloPay SRE Team.
