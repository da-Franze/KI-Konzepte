"""Laedt externe Rollen-DNA statt sie als Betriebslogik fest einzubauen."""
from __future__ import annotations

import configparser
from pathlib import Path

from runtime_paths import PERSONA_DIR


def load_role_dna(role: str, fallback: str = "") -> str:
    """Liest ``<PERSONA_DIR>/<role>.ini``; der Fallback ist Legacy-Kompatibilität."""
    path = PERSONA_DIR / f"{role}.ini"
    if not path.is_file():
        return fallback
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    dna = parser.get("persona", "dna", fallback="").strip()
    if not dna:
        raise ValueError(f"Persona-Datei enthaelt keine DNA: {path}")
    return dna