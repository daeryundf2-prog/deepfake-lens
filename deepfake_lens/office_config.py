"""The examiner's office identity printed in report headers (N17).

Before round 6 every forensic PDF, evidence statement (Markdown/PDF/JSON) and
web report carried one specific law firm's name, street address and phone
number by default. Nothing identifying is a default any more: the office
name and phone number come from ``--law-firm`` / ``--contact`` (CLI), the
request's ``law_firm`` / ``contact`` (web), or the operator's config file
``~/.deepfake-lens/config.json`` (``{"law_firm": "...", "contact": "..."}``;
``$DEEPFAKE_LENS_CONFIG`` names another file) — and are blank otherwise.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_ENV = "DEEPFAKE_LENS_CONFIG"
CONFIG_DIR_NAME = ".deepfake-lens"
CONFIG_FILE_NAME = "config.json"
# A generic unit name (not an identity) shown with the office name.
DEFAULT_CENTER = "디지털포렌식 감정센터"
# Longest value kept from the config file (a header line, not a document).
MAX_FIELD_CHARS = 200


def config_path() -> Path:
    """``$DEEPFAKE_LENS_CONFIG`` or ``~/.deepfake-lens/config.json``."""
    env = os.environ.get(CONFIG_ENV)
    return Path(env).expanduser() if env else Path.home() / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def load_office_config(path: Path | None = None) -> dict[str, str]:
    """``law_firm`` / ``contact`` from the config file; missing or unreadable → {}."""
    source = path or config_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("office config %s could not be read; report headers stay blank", source, exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("law_firm", "contact"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:MAX_FIELD_CHARS]
    return out


@dataclass(frozen=True)
class OfficeIdentity:
    law_firm: str = ""
    contact: str = ""
    center: str = DEFAULT_CENTER

    @property
    def header(self) -> str:
        """``"<law firm> <center>"`` (the center alone when no firm is configured)."""
        return f"{self.law_firm} {self.center}".strip()


def office_identity(law_firm: object = None, contact: object = None, *, center: object = None) -> OfficeIdentity:
    """Explicit values first (CLI flag / request field), then the config file, else blank."""
    config = load_office_config()

    def pick(explicit: object, key: str) -> str:
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        return config.get(key, "")

    return OfficeIdentity(
        law_firm=pick(law_firm, "law_firm"),
        contact=pick(contact, "contact"),
        center=center.strip() if isinstance(center, str) and center.strip() else DEFAULT_CENTER,
    )
