"""Load settings.json and resolve secrets from the environment.

This module is the only place secrets enter the process. They are read from env vars (named in
settings.json via ``*_env`` keys), never from the JSON body, and never logged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Configuration or secret-resolution failure with an actionable message."""


def load_dotenv(path: str | Path = ".env") -> int:
    """Populate ``os.environ`` from a ``.env`` file (KEY=VALUE per line). Zero-dependency.

    Replaces ``export VAR=...`` for local + Docker runs. Already-set environment variables win (so a
    real ``export`` or a container ``-e`` still overrides the file), and values are never logged —
    secrets enter the process here and nowhere else. Lines that are blank or start with ``#`` are
    skipped; surrounding quotes and a leading ``export `` are stripped. Missing file is a no-op.
    Returns the number of keys set.
    """
    p = Path(path)
    if not p.exists():
        return 0
    n = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            n += 1
    return n


def load_settings(path: str | Path = "config/settings.json") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(
            f"settings file not found: {p}\n"
            "Copy config/settings.example.json to config/settings.json and fill it in."
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"settings file is not valid JSON: {p} ({exc})") from exc


def resolve_secret(env_name: str) -> str:
    val = os.environ.get(env_name)
    if not val:
        raise ConfigError(
            f"environment variable {env_name} is not set. "
            "Secrets are read from the environment, never from settings.json."
        )
    return val


def account_password(account: dict[str, Any]) -> str:
    env_name = account.get("password_env")
    if not env_name:
        raise ConfigError(f"account {account.get('id', '?')!r} is missing 'password_env'")
    return resolve_secret(env_name)


def claude_api_key(settings: dict[str, Any]) -> str:
    return resolve_secret(settings["llm"]["api_key_env"])


def _base_dir(settings_path: str | Path) -> Path:
    # settings live at <root>/config/settings.json -> root is parents[1]
    p = Path(settings_path).resolve()
    return p.parents[1] if p.parent.name == "config" else p.parent


def paths(settings: dict[str, Any], settings_path: str | Path) -> dict[str, Path]:
    """Resolve configured paths to absolute, creating the writable dirs."""
    base = _base_dir(settings_path)
    cfg = settings.get("paths", {})
    out = {
        "corpus_dir": base / cfg.get("corpus_dir", "corpus"),
        "out_dir": base / cfg.get("out_dir", "out"),
        "playbook": base / cfg.get("playbook", "config/triage_playbook.md"),
        "audit_log": base / cfg.get("audit_log", "out/audit.jsonl"),
    }
    out["corpus_dir"].mkdir(parents=True, exist_ok=True)
    out["out_dir"].mkdir(parents=True, exist_ok=True)
    return out
