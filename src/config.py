"""Load + validate config YAML và env vars."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError


class WarnCrit(BaseModel):
    warn: float
    crit: float


class CpuThresholds(WarnCrit):
    sustained_min: int = 15


class LinuxThresholds(BaseModel):
    cpu_pct: CpuThresholds
    ram_pct: WarnCrit
    disk_used_pct: WarnCrit
    disk_io_util: WarnCrit
    load_per_cpu: WarnCrit


class PxcThresholds(BaseModel):
    wsrep_local_state_ok: int = 4
    cluster_size_min: int = 3
    queue_avg: WarnCrit
    flow_control_paused_pct: WarnCrit


class RedisThresholds(BaseModel):
    used_memory_pct: WarnCrit
    evicted_keys_rate: WarnCrit
    master_link_ok: str = "up"
    cluster_state_ok: str = "ok"


class Thresholds(BaseModel):
    linux: LinuxThresholds
    pxc: PxcThresholds
    redis: RedisThresholds


class Whitelist(BaseModel):
    users: list[int] = Field(default_factory=list)

    def allows(self, user_id: int) -> bool:
        return user_id in self.users


class AppSettings(BaseModel):
    grafana_url: str
    grafana_token: str
    grafana_ds_uid: str
    openai_api_key: str
    openai_model: str = "gpt-4o"
    telegram_bot_token: str
    log_level: str = "INFO"
    session_idle_minutes: int = 30
    config_dir: Path = Path("./config")
    thresholds: Thresholds
    whitelist: Whitelist


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing env var: {name}")
    return val


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} root must be a mapping")
    return data


def load_settings(config_dir: Path | None = None) -> AppSettings:
    """Load env vars + YAML files vào AppSettings."""
    cfg_dir = config_dir or Path(os.environ.get("CONFIG_DIR", "./config"))

    try:
        thresholds = Thresholds(**_load_yaml(cfg_dir / "thresholds.yaml"))
        whitelist = Whitelist(**_load_yaml(cfg_dir / "whitelist.yaml"))
    except ValidationError as exc:
        raise RuntimeError(f"Invalid config: {exc}") from exc

    return AppSettings(
        grafana_url=_require("GRAFANA_URL").rstrip("/"),
        grafana_token=_require("GRAFANA_TOKEN"),
        grafana_ds_uid=_require("GRAFANA_DS_UID"),
        openai_api_key=_require("OPENAI_API_KEY"),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        session_idle_minutes=int(os.environ.get("SESSION_IDLE_MINUTES", "30")),
        config_dir=cfg_dir,
        thresholds=thresholds,
        whitelist=whitelist,
    )
