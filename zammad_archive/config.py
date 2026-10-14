"""Load config.yaml, merge with defaults, resolve secrets from the environment."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class ConfigError(Exception):
    pass


DEFAULTS: Dict[str, Any] = {
    "zammad": {
        "url": None,
        "token_env": "ZAMMAD_TOKEN",
        "verify_tls": True,  # true, false, or a path to a CA bundle
        "timeout": 60,
        "per_page": 100,
        "fetch_tags": True,
    },
    "queries": [],
    "export": {
        "archive_dir": "./data/archive",
        "incremental_days": 7,
        "delay_seconds": 0.0,
    },
    "privacy": {
        "secret_env": "ZAMMAD_ARCHIVE_SECRET",
        "ticket_fields": [
            "id", "number", "title", "state", "priority", "group", "owner",
            "customer", "organization", "created_at", "updated_at", "close_at", "tags",
        ],
        "article_fields": [
            "id", "created_at", "sender", "type", "internal", "from", "to", "cc",
            "subject", "body", "attachments",
        ],
        "skip_internal_articles": False,
        "skip_system_articles": True,
        "skip_article_types": [],
        "internal_domains": [],
        "agent_display": "localpart",  # login | localpart | pseudonym
        "pseudonymise_customers": True,
        "replace_name_parts": True,
        "name_part_min_length": 4,
        "cross_ticket_names": True,  # replace people known from other tickets too (hashed registry)
        "name_part_ignore": [
            "support", "team", "service", "info", "admin", "office", "helpdesk",
            "noreply", "no-reply", "kundenservice", "mail", "system", "zammad",
            "abteilung", "gmbh", "group", "gruppe", "department", "notification",
            "server", "postmaster", "mailer", "daemon", "user", "kunde", "customer",
            "herr", "frau", "prof",
        ],
        "strip_quoted_replies": True,
        "quote_markers": [
            r"^-{2,}\s*(original message|ursprüngliche nachricht|original-nachricht|forwarded message|weitergeleitete nachricht)\s*-{2,}",
            r"^(am|on)\s.{4,200}(schrieb|wrote)\s*.*:\s*$",
            r"^_{10,}\s*$",
        ],
        "cut_signatures": True,
        "signature_markers": [
            r"^--\s*$",
            r"^(mit )?(freundlichen|besten|herzlichen|lieben|vielen) gr(ü|ue)(ß|ss)en\b",
            r"^(viele|beste|liebe|freundliche|schöne) gr(ü|ue)(ß|ss)e\b",
            r"^gr(ü|ue)(ß|ss)e\b",
            r"^(best|kind|warm)( regards| wishes)\b",
            r"^regards\b",
            r"^cheers\b",
            r"^lg,?\s*$",
            r"^mfg,?\s*$",
        ],
        "signature_keep_lines": 1,
        "scrub": {
            "emails": "pseudonym",  # pseudonym | redact | keep
            "phones": True,
            "ibans": True,
            "customer_domains": True,  # kunde.de in "intranet.kunde.de" -> Domain-xxxxxxxx
            "public_domains": [
                "gmail.com", "googlemail.com", "gmx.de", "gmx.net", "web.de", "t-online.de",
                "outlook.com", "hotmail.com", "hotmail.de", "live.com", "yahoo.com", "yahoo.de",
                "icloud.com", "me.com", "mailbox.org", "posteo.de", "aol.com", "freenet.de",
                "proton.me", "protonmail.com",
            ],
            "custom_patterns": [],  # [{name, pattern, replacement, ignore_case}]
        },
        "max_body_chars": 20000,
    },
    "attachments": {
        "enabled": True,
        "max_size_mb": 10,
        "allowed_extensions": [
            "txt", "log", "csv", "json", "xml", "yaml", "yml", "conf", "cfg", "ini",
            "pdf", "png", "jpg", "jpeg",
        ],
        "skip_inline": True,
        "extract_text": False,
        "tika_url": "http://127.0.0.1:9998",
        "extract_max_chars": 50000,
    },
    "meilisearch": {
        "url": "http://127.0.0.1:7700",
        "key_env": "MEILI_MASTER_KEY",
        "index": "tickets",
        "web_config_js": "./web/config.js",
        "public_meili_path": "/meili",
        "public_files_path": "/files",
    },
    "custom_middleware": None,
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {path} (copy config.example.yaml)")
    with p.open(encoding="utf-8") as fh:
        user_cfg = yaml.safe_load(fh) or {}
    if not isinstance(user_cfg, dict):
        raise ConfigError("config file must contain a YAML mapping")
    cfg = _deep_merge(DEFAULTS, user_cfg)
    cfg["_config_dir"] = str(p.resolve().parent)
    # set by compose.yaml: inside the container Meilisearch is reachable by service name
    if os.environ.get("MEILI_URL"):
        cfg["meilisearch"]["url"] = os.environ["MEILI_URL"]
    _validate(cfg)
    return cfg


def _validate(cfg: Dict[str, Any]) -> None:
    if cfg["privacy"]["agent_display"] not in ("login", "localpart", "pseudonym"):
        raise ConfigError("privacy.agent_display must be login, localpart or pseudonym")
    if cfg["privacy"]["scrub"]["emails"] not in ("pseudonym", "redact", "keep"):
        raise ConfigError("privacy.scrub.emails must be pseudonym, redact or keep")
    names = set()
    for q in cfg["queries"]:
        if not isinstance(q, dict) or not q.get("name") or not q.get("query"):
            raise ConfigError("every entry in queries needs 'name' and 'query'")
        if q["name"] in names:
            raise ConfigError(f"duplicate query name: {q['name']}")
        names.add(q["name"])


def resolve_path(cfg: Dict[str, Any], value: str) -> Path:
    """Relative paths in the config are relative to the config file."""
    p = Path(os.path.expanduser(value))
    return p if p.is_absolute() else Path(cfg["_config_dir"]) / p


def env_secret(name: Optional[str], required: bool, what: str) -> Optional[str]:
    value = os.environ.get(name or "")
    if required and not value:
        raise ConfigError(f"{what}: environment variable {name} is not set")
    return value or None


def require_zammad(cfg: Dict[str, Any]) -> None:
    if not cfg["zammad"].get("url"):
        raise ConfigError("zammad.url is not set")
