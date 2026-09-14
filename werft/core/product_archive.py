from __future__ import annotations

"""Versioniertes Produktarchiv fuer verifizierte Werft-Auslieferungen."""
import ast
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from code_crawler import map_module
from runtime_paths import PRODUCT_ARCHIVE_DIR, WERFT_DB_PATH


def archive_product(
    product_name: str,
    source_path: str | Path,
    specification: str,
    bestellung_id: str | None = None,
) -> dict:
    """Archiviert Code, Projektmap und Hash atomar genug fuer Wiederverwendung."""
    source = Path(source_path)
    code = source.read_text(encoding="utf-8")
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + code_hash[:12]
    target_dir = PRODUCT_ARCHIVE_DIR / product_name / version
    target_dir.mkdir(parents=True, exist_ok=False)
    code_target = target_dir / source.name
    map_target = target_dir / "project_map.json"
    shutil.copy2(source, code_target)
    project_map = map_module(source, source.parent)
    project_map["product"] = {
        "name": product_name,
        "version": version,
        "scope": "product_archive",
        "code_hash": code_hash,
        "bestellung_id": bestellung_id,
    }
    map_target.write_text(json.dumps(project_map, ensure_ascii=False, indent=2), encoding="utf-8")

    con = sqlite3.connect(WERFT_DB_PATH)
    try:
        con.execute(
            "INSERT INTO product_versions "
            "(product_name, version, bestellung_id, specification, code_hash, "
            "code_path, project_map_path, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'verified')",
            (product_name, version, bestellung_id, specification, code_hash,
             str(code_target), str(map_target)),
        )
        con.commit()
    finally:
        con.close()
    return {
        "product_name": product_name,
        "version": version,
        "code_hash": code_hash,
        "code_path": str(code_target),
        "project_map_path": str(map_target),
        "vdb_scope": "product_archive",
        "vdb_payload": {
            "scope": "product_archive",
            "product_name": product_name,
            "version": version,
            "code_hash": code_hash,
            "project_map_path": str(map_target),
            "bestellung_id": bestellung_id,
            "status": "verified",
        },
    }
