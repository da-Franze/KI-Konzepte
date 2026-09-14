"""Zentrale, portable Pfade fuer den separierten Werft-Arbeitsordner."""
import os
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
ROOT_DIR = CORE_DIR.parent
RUNTIME_DIR = Path(os.environ.get("WERFT_RUNTIME_DIR", ROOT_DIR / "runtime"))
WERFT_DB_PATH = Path(os.environ.get("WERFT_DB_PATH", RUNTIME_DIR / "werft.db"))
SPEC_DB_PATH = Path(os.environ.get("WERFT_SPEC_DB_PATH", RUNTIME_DIR / "spec_generator.db"))
PRODUCT_ARCHIVE_DIR = Path(
    os.environ.get("WERFT_PRODUCT_ARCHIVE_DIR", RUNTIME_DIR / "product_archive")
)
GENERATED_DIR = Path(
    os.environ.get("WERFT_GENERATED_DIR", CORE_DIR / "generiert")
)
PERSONA_DIR = Path(
    os.environ.get("WERFT_PERSONA_DIR", ROOT_DIR / "config" / "personas")
)
CONFIG_INI_PATH = Path(
    os.environ.get("WERFT_CONFIG_INI", ROOT_DIR / "config" / "werft.ini")
)
WERFT_INBOX_DIR = Path(
    os.environ.get("WERFT_INBOX_DIR", ROOT_DIR.parent / "transfer" / "werft" / "inbox")
)
WERFT_OUTBOX_DIR = Path(
    os.environ.get("WERFT_OUTBOX_DIR", ROOT_DIR.parent / "transfer" / "werft" / "outbox")
)
