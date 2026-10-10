"""Load + validate runtime config from YAML and environment variables.

Reading order:
  1. YAML at $USOS_WATCHER_CONFIG (default: ./config.yaml).
  2. Secrets from environment variables (never from YAML).

See SPEC.md §7.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroupPreferenceCfg:
    tier1_keywords: List[str]
    tier2_keywords: List[str]
    on_no_match: str                    # only "email_and_exit" / "notify_and_exit"
    strategy: str = "keyword"           # keyword | group_number | any_available
    prefer_group: Optional[int] = None  # for group_number strategy


@dataclass(frozen=True)
class TargetCfg:
    registration_code: str
    course_name_match: str
    calendar_url: str
    group_preference: GroupPreferenceCfg


@dataclass(frozen=True)
class BehaviorCfg:
    refill_mode: bool
    dry_run: bool


@dataclass(frozen=True)
class EmailCfg:
    daily_heartbeat_time: str
    failure_threshold_login: int
    failure_threshold_http: int


@dataclass(frozen=True)
class LoggingCfg:
    level: str
    rotate_max_bytes: int
    rotate_backups: int


@dataclass(frozen=True)
class HttpCfg:
    request_timeout_sec: int
    retries: int
    jitter_max_sec: int
    user_agent: str


@dataclass(frozen=True)
class Secrets:
    usos_username: str
    usos_password: str
    telegram_bot_token: str
    telegram_chat_id: str
    smtp_host: str = ""
    smtp_port: int = 0
    smtp_username: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    mail_to: str = ""


@dataclass(frozen=True)
class Paths:
    config_path: Path
    state_path: Path
    log_dir: Path
    lock_path: Path
    cookies_path: Path


@dataclass(frozen=True)
class Config:
    targets: List[TargetCfg]
    behavior: BehaviorCfg
    email: EmailCfg
    logging: LoggingCfg
    http: HttpCfg
    secrets: Secrets
    paths: Paths

    @property
    def target(self) -> TargetCfg:
        """Backward-compat: returns the first target."""
        return self.targets[0]

    @property
    def group_preference(self) -> GroupPreferenceCfg:
        """Backward-compat: returns the first target's group preference."""
        return self.targets[0].group_preference

    @property
    def secret_values_for_scrubbing(self) -> List[str]:
        return [
            self.secrets.usos_password,
            self.secrets.smtp_password,
            self.secrets.telegram_bot_token,
        ]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

_REQUIRED_ENV = [
    "USOS_USERNAME",
    "USOS_PASSWORD",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]

_DEFAULT_CALENDAR_URL = (
    "https://usosweb.uw.edu.pl/kontroler.php"
    "?_action=dla_stud/rejestracja/kalendarz&lang=en"
)


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and (val is None or val == ""):
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"See .env.example."
        )
    return val or ""


def _resolve_paths() -> Paths:
    return Paths(
        config_path=Path(_env("USOS_WATCHER_CONFIG", "config.yaml")),
        state_path=Path(_env("USOS_WATCHER_STATE", "state.json")),
        log_dir=Path(_env("USOS_WATCHER_LOG_DIR", "logs")),
        lock_path=Path(_env("USOS_WATCHER_LOCK", "/tmp/usos-watcher.lock")),
        cookies_path=Path(_env("USOS_WATCHER_COOKIES", "cookies.json")),
    )


def _load_secrets() -> Secrets:
    for name in _REQUIRED_ENV:
        _env(name, required=True)
    return Secrets(
        usos_username=os.environ["USOS_USERNAME"],
        usos_password=os.environ["USOS_PASSWORD"],
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SMTP_PORT", "0") or "0"),
        smtp_username=os.environ.get("SMTP_USERNAME", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        mail_from=os.environ.get("MAIL_FROM", ""),
        mail_to=os.environ.get("MAIL_TO", ""),
    )


def _load_group_preference(raw: dict) -> GroupPreferenceCfg:
    on_no_match = raw.get("on_no_match", "email_and_exit")
    if on_no_match not in ("email_and_exit", "notify_and_exit"):
        raise RuntimeError(
            "group_preference.on_no_match must be 'email_and_exit' or "
            "'notify_and_exit' (see SPEC.md §15)."
        )
    strategy = raw.get("strategy", "keyword")
    if strategy not in ("keyword", "group_number", "any_available"):
        raise RuntimeError(
            f"group_preference.strategy must be keyword|group_number|any_available, got {strategy!r}"
        )
    return GroupPreferenceCfg(
        tier1_keywords=list(raw.get("tier1_keywords", [])),
        tier2_keywords=list(raw.get("tier2_keywords", [])),
        on_no_match=on_no_match,
        strategy=strategy,
        prefer_group=raw.get("prefer_group"),
    )


def _load_target(raw: dict, default_gp: Optional[GroupPreferenceCfg]) -> TargetCfg:
    gp_raw = raw.get("group_preference")
    if gp_raw:
        gp = _load_group_preference(gp_raw)
    elif default_gp is not None:
        gp = default_gp
    else:
        raise RuntimeError(
            "Each target needs a group_preference section, or a top-level "
            "group_preference must be defined."
        )
    return TargetCfg(
        registration_code=raw["registration_code"],
        course_name_match=raw["course_name_match"],
        calendar_url=raw.get("calendar_url", _DEFAULT_CALENDAR_URL),
        group_preference=gp,
    )


def load_config() -> Config:
    paths = _resolve_paths()

    if not paths.config_path.exists():
        raise RuntimeError(f"Config file not found: {paths.config_path}")

    with paths.config_path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)

    if not isinstance(raw, dict):
        raise RuntimeError("config.yaml must parse to a mapping at the top level")

    # Load top-level group_preference (optional, used as default for targets
    # that don't define their own).
    default_gp: Optional[GroupPreferenceCfg] = None
    if "group_preference" in raw:
        default_gp = _load_group_preference(raw["group_preference"])

    # Support both "target:" (single dict) and "targets:" (list).
    raw_targets = raw.get("targets") or raw.get("target")
    if raw_targets is None:
        raise RuntimeError("config.yaml must have a 'targets' or 'target' section")
    if isinstance(raw_targets, dict):
        raw_targets = [raw_targets]

    targets = [_load_target(t, default_gp) for t in raw_targets]

    behavior = BehaviorCfg(**raw["behavior"])
    email_cfg = EmailCfg(**raw["email"])
    logging_cfg = LoggingCfg(**raw["logging"])
    http_cfg = HttpCfg(**raw["http"])

    return Config(
        targets=targets,
        behavior=behavior,
        email=email_cfg,
        logging=logging_cfg,
        http=http_cfg,
        secrets=_load_secrets(),
        paths=paths,
    )
