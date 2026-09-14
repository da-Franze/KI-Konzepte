"""ConfigLoader: liest und cached system_config aus werft.db.

Zero-Hardcoding-Prinzip (G-4 / WERFT_NEUSTART_SPEZIFIKATION.md Abschnitt 2):
Modellnamen, Hosts, Schwellwerte werden NIE im Code hinterlegt, sondern immer
ueber diese Klasse aus system_config gelesen.
"""
import configparser
import sqlite3
from pathlib import Path
from typing import Optional

from runtime_paths import CONFIG_INI_PATH, WERFT_DB_PATH as DB_PATH

_TYPE_CAST = {
    "int": int,
    "float": float,
    "bool": lambda v: str(v).lower() in ("1", "true", "yes"),
    "string": str,
}


def _cast_ini_value(value: str) -> object:
    lowered = value.lower()
    if lowered in ("true", "false", "yes", "no"):
        return lowered in ("true", "yes")
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


class ConfigLoader:
    """Liest system_config und cached die Werte fuer die Laufzeit des Prozesses."""

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._cache: dict[str, object] = {}
        self._loaded = False

    def _load(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._db_path)
        try:
            try:
                rows = con.execute(
                    "SELECT config_key, config_value, config_type FROM system_config"
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
                rows = []
        finally:
            con.close()
        for key, value, ctype in rows:
            caster = _TYPE_CAST.get(ctype or "string", str)
            try:
                self._cache[key] = caster(value)
            except (TypeError, ValueError):
                self._cache[key] = value
        self._load_ini()
        self._loaded = True

    def _load_ini(self) -> None:
        if not CONFIG_INI_PATH.exists():
            return
        parser = configparser.ConfigParser()
        parser.read(CONFIG_INI_PATH, encoding="utf-8")
        for section in parser.sections():
            for option, value in parser.items(section):
                key = f"{section}.{option}"
                existing = self._cache.get(key)
                if isinstance(existing, bool):
                    self._cache[key] = str(value).lower() in ("1", "true", "yes")
                elif isinstance(existing, int):
                    self._cache[key] = int(value)
                elif isinstance(existing, float):
                    self._cache[key] = float(value)
                else:
                    self._cache[key] = _cast_ini_value(value)

    def get(self, key: str, default: Optional[object] = None) -> object:
        if not self._loaded:
            self._load()
        return self._cache.get(key, default)

    def reload(self) -> None:
        self._loaded = False
        self._cache.clear()
        self._load()

    def set_runtime_overrides(self, values: dict[str, object]) -> None:
        """Setzt prozesslokale Werte, ohne die Runtime-Datenbank zu verändern."""
        if not self._loaded:
            self._load()
        self._cache.update(values)


config = ConfigLoader()

if __name__ == "__main__":
    print("ollama.host =", config.get("ollama.host"))
    print("ollama.model.coder =", config.get("ollama.model.coder"))
    print("pipeline.max_retry_spec_failed =", config.get("pipeline.max_retry_spec_failed"))
