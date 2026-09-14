"""Kanonische map-zentrierte Schnittstelle der separierten Werft."""
from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from code_crawler import (
    crawl_project,
    diff_project_maps,
    extract_node_source,
    _stub_findings,
    specification_refinement,
    specification_to_map,
    task_regions_from_diff,
)


class WerftMapPipeline:
    """Verbindet deterministische Kartenbildung mit dem Freigabeprozess."""

    def __init__(self, vdb_writer=None, runtime_db_path=None):
        """Optionaler Cross-Projekt-Lernspeicher fuer Kartenereignisse."""
        self.vdb_writer = vdb_writer
        self.runtime_db_path = runtime_db_path

    def _persistiere_kartenstand(
        self,
        projektname: str,
        zyklus: int,
        soll_karte: dict,
        ist_karte: dict,
        diff: dict,
        teilauftraege: list[dict],
    ) -> int:
        """Speichert Soll/Ist/Diff als auswertbare Runtime-Version."""
        if self.runtime_db_path is None:
            from runtime_paths import WERFT_DB_PATH
            db_path = WERFT_DB_PATH
        else:
            db_path = self.runtime_db_path
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS project_map_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    cycle INTEGER NOT NULL,
                    expected_map_id TEXT,
                    actual_map_id TEXT,
                    expected_map TEXT NOT NULL,
                    actual_map TEXT NOT NULL,
                    map_diff TEXT NOT NULL,
                    task_regions TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'beobachtet',
                    approval TEXT NOT NULL DEFAULT 'freigabe_noetig',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )""",
            )
            cursor = connection.execute(
                """INSERT INTO project_map_versions
                (project_name, cycle, expected_map_id, actual_map_id,
                 expected_map, actual_map, map_diff, task_regions, status, approval)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    projektname,
                    zyklus,
                    soll_karte.get("map_id"),
                    ist_karte.get("map_id"),
                    json.dumps(soll_karte, ensure_ascii=False, sort_keys=True),
                    json.dumps(ist_karte, ensure_ascii=False, sort_keys=True),
                    json.dumps(diff, ensure_ascii=False, sort_keys=True),
                    json.dumps(teilauftraege, ensure_ascii=False, sort_keys=True),
                    "bestaetigt" if diff["provenance"]["status"] == "bestaetigt" else "abweichung",
                    "keine_freigabe_noetig" if diff["provenance"]["status"] == "bestaetigt" else "freigabe_noetig",
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def crawl_ist(self, projektordner: str | Path) -> dict:
        return crawl_project(projektordner)

    def erstelle_soll(self, spezifikation: str) -> dict:
        return specification_to_map(spezifikation)

    def erstelle_soll_aus_happen(self, happen: list[dict | str]) -> dict:
        """Validiert Analysten-Happen und baut daraus eine gemeinsame Soll-Sicht.

        Der Happen-Text kommt vom groesseren Analystenmodell; die Pipeline
        selbst interpretiert ihn nicht semantisch, sondern verarbeitet nur
        freigegebene, nicht-leere Textbausteine in stabiler Reihenfolge.
        """
        texte: list[str] = []
        for nummer, happen_item in enumerate(happen, start=1):
            if isinstance(happen_item, str):
                text = happen_item.strip()
            elif isinstance(happen_item, dict):
                if happen_item.get("status", "freigegeben") != "freigegeben":
                    raise ValueError(f"Spezifikationshappen {nummer} ist nicht freigegeben")
                text = str(happen_item.get("text", "")).strip()
            else:
                raise TypeError(f"Spezifikationshappen {nummer} hat ein ungueltiges Format")
            if not text:
                raise ValueError(f"Spezifikationshappen {nummer} ist leer")
            texte.append(text)
        if not texte:
            raise ValueError("mindestens ein Spezifikationshappen ist erforderlich")
        return self.erstelle_soll("\n\n".join(texte))

    def vergleiche(self, soll_karte: dict, ist_karte: dict) -> dict:
        return diff_project_maps(soll_karte, ist_karte)

    @staticmethod
    def _protokolliere_kartenereignis(
        projektordner: Path,
        *,
        zyklus: int,
        status: str,
        vorher: dict,
        nachher: dict | None,
        dateien: list[str],
        grund: str | None = None,
    ) -> dict:
        """Schreibt ein unveraenderliches, lokales Kartenereignis als JSONL."""
        protokoll = projektordner / ".werft" / "karten_aenderungen.jsonl"
        protokoll.parent.mkdir(parents=True, exist_ok=True)
        ereignis = {
            "format": "werft-map-change-v1",
            "cycle": zyklus,
            "status": status,
            "files": sorted(dateien),
            "before_map_id": vorher.get("map_id"),
            "after_map_id": nachher.get("map_id") if nachher else None,
            "before_source_hash": vorher.get("source", {}).get("source_hash"),
            "after_source_hash": nachher.get("source", {}).get("source_hash") if nachher else None,
            "reason": grund,
        }
        ereignis_json = json.dumps(ereignis, ensure_ascii=False, sort_keys=True)
        ereignis["event_hash"] = hashlib.sha256(ereignis_json.encode("utf-8")).hexdigest()
        with protokoll.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(ereignis, ensure_ascii=False, sort_keys=True) + "\n")
        return ereignis

    def lerne_kartenereignis(self, ereignis: dict, projektname: str) -> str | None:
        """Speichert ein verifiziertes Kartenereignis als Cross-Projekt-Wissen."""
        if self.vdb_writer is None:
            return None
        text = (
            f"Werft-Kartenlerneintrag fuer Projekt {projektname}: "
            f"{ereignis['status']} in Zyklus {ereignis['cycle']}. "
            f"Dateien: {', '.join(ereignis['files'])}. "
            f"Grund: {ereignis.get('reason') or 'unbekannt'}. "
            f"Vorher-Karte {ereignis.get('before_map_id')}; "
            f"Nachher-Karte {ereignis.get('after_map_id') or 'rollback'}."
        )
        try:
            return self.vdb_writer(
                text,
                {
                    "owner": "werft",
                    "memory_type": "map_change",
                    "project": projektname,
                    "event_hash": ereignis["event_hash"],
                    "status": ereignis["status"],
                    "tags": "werft,kartenereignis,cross_project_learning",
                },
            )
        except Exception:
            return None

    def specification_refinement(
        self, soll_karte: dict, ist_karte: dict, diff: dict | None = None
    ) -> dict:
        return specification_refinement(soll_karte, ist_karte, diff)

    def zerlege_teilauftraege(self, diff: dict, max_nodes: int = 12) -> list[dict]:
        return task_regions_from_diff(diff, max_nodes=max_nodes)

    def pruefe_teilauftrag(self, teilauftrag: dict, code: str) -> dict:
        """Prüft nur den gelieferten Teil und mutiert keine Gesamtkarte."""
        errors: list[str] = []
        try:
            ast.parse(code, filename=teilauftrag.get("task_id", "teilauftrag.py"))
        except SyntaxError as exc:
            errors.append(f"syntax: {exc.msg} at line {exc.lineno}")

        with tempfile.TemporaryDirectory(prefix="werft-region-check-") as temporary:
            source_path = Path(temporary) / "region.py"
            source_path.write_text(code, encoding="utf-8")
            checked_map = crawl_project(temporary)

        relevant_ids = set(teilauftrag.get("node_ids", []))
        observed_ids = {
            node["id"]
            for module in checked_map["modules"]
            for node in module.get("nodes", [])
        }
        missing_nodes = []
        for relevant_id in sorted(relevant_ids):
            if relevant_id in observed_ids:
                continue
            suffix = relevant_id.split(":", 2)[-1]
            candidates = [
                observed_id for observed_id in observed_ids
                if observed_id.split(":", 2)[-1] == suffix
            ]
            if len(candidates) != 1:
                missing_nodes.append(relevant_id)
        if missing_nodes:
            errors.append("interfaces: missing nodes " + ", ".join(missing_nodes))
        if checked_map["source"]["undefined_name_count"]:
            errors.append("undefined_names: unresolved names found")
        if checked_map["source"]["stub_count"]:
            errors.append("stub_findings: placeholder implementation found")
        destructive_findings = [
            finding
            for module in checked_map["modules"]
            for finding in module.get("destructive_findings", [])
        ]
        if destructive_findings:
            errors.append("destructive_findings: unsafe operation found")

        return {
            "format": "werft-task-test-v1",
            "task_id": teilauftrag.get("task_id"),
            "status": "bestanden" if not errors else "fehlgeschlagen",
            "errors": errors,
            "map": checked_map,
            "checks": {
                "syntax": not any(error.startswith("syntax:") for error in errors),
                "undefined_names": checked_map["source"]["undefined_name_count"] == 0,
                "stub_findings": checked_map["source"]["stub_count"] == 0,
                "destructive_findings": not destructive_findings,
                "interfaces": not missing_nodes,
            },
        }

    def pruefe_knoten_optimierung(
        self,
        projektordner: str | Path,
        node_id: str,
        kandidat: str,
    ) -> dict:
        """Bewertet einen isolierten Funktionskandidaten ohne Projektmutation."""
        original = extract_node_source(projektordner, node_id)
        original_tree = ast.parse(original["source"])
        candidate_tree = ast.parse(kandidat, filename=node_id)
        # Fund 2026-09-14 (Sokrates, live verifiziert): ast.walk() zaehlte
        # verschachtelte Hilfsfunktionen faelschlich als zweite Top-Level-
        # Funktion mit. Nur die tatsaechliche Modulebene zaehlt.
        candidate_nodes = [
            node for node in candidate_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        errors = []
        warnings = []
        if len(candidate_nodes) != 1 or candidate_nodes[0].name != original["name"].rsplit(".", 1)[-1]:
            errors.append("contract: candidate must contain exactly the mapped function")
        original_function = next(
            node for node in ast.walk(original_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        candidate_function = candidate_nodes[0] if candidate_nodes else None
        if candidate_function is not None:
            if len(candidate_function.args.args) != len(original_function.args.args):
                errors.append("contract: function argument count changed")
            if not candidate_function.body or any(
                finding["kind"] in {"pass_only", "ellipsis_only", "not_implemented"}
                for finding in _stub_findings(candidate_function, node_id)
            ):
                errors.append("quality: candidate is a stub")

        def metric(function_node: ast.AST) -> tuple[int, int]:
            match_node = getattr(ast, "Match", ())
            branches = sum(
                isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, match_node))
                for node in ast.walk(function_node)
            )
            return len(list(ast.walk(function_node))), branches

        original_metric = metric(original_function)
        candidate_metric = metric(candidate_function) if candidate_function else None
        if candidate_metric and candidate_metric > original_metric:
            # Fund 2026-09-14 (Sokrates, mit Franz' expliziter Freigabe): ein
            # echter, funktional erweiternder Patch ist notwendigerweise
            # groesser -- das lehnte bisher JEDEN solchen Patch hart ab.
            # Warnung statt hartem Fehler, blockiert die Freigabe nicht mehr.
            warnings.append("optimization: candidate is structurally larger or more branched")
        return {
            "format": "werft-node-optimization-v1",
            "node_id": node_id,
            "status": "freigegeben" if not errors else "abgelehnt",
            "mutated_project": False,
            "errors": errors,
            "warnings": warnings,
            "original": {"line": original["line"], "metrics": original_metric},
            "candidate": {"metrics": candidate_metric} if candidate_metric else None,
        }

    def integriere(self, teilauftrag: dict, pruefergebnis: dict) -> dict:
        """Integriert freigegebene Dateien und verifiziert den neuen Kartenstand."""
        freigegeben = teilauftrag.get("freigabe") is True
        bestanden = pruefergebnis.get("status") == "bestanden"
        projektordner = teilauftrag.get("projektordner")
        vorgeschlagene_dateien = teilauftrag.get("dateien")
        kann_schreiben = bool(projektordner and isinstance(vorgeschlagene_dateien, dict))
        status = "integriert" if freigegeben and bestanden and kann_schreiben else "nicht_integriert"
        map_version_id = teilauftrag.get("map_version_id")
        if status != "integriert":
            if map_version_id is not None:
                self._aktualisiere_kartenstatus(map_version_id, status, freigegeben)
            return {
                "format": "werft-task-integration-v1",
                "task_id": teilauftrag.get("task_id"),
                "status": status,
                "reason": "test_and_approval_required",
                "map_update": None,
            }

        root = Path(projektordner).resolve()
        backup = Path(tempfile.mkdtemp(prefix="werft-integration-backup-"))
        changed: list[str] = []
        try:
            for relative_name, content in vorgeschlagene_dateien.items():
                relative_path = Path(relative_name)
                target = (root / relative_path).resolve()
                if relative_path.is_absolute() or ".." in relative_path.parts or root not in target.parents:
                    raise ValueError(f"ungueltiger Integrationspfad: {relative_name}")
                if target.exists() and target.is_symlink():
                    raise ValueError(f"Symlink-Ziel wird nicht integriert: {relative_name}")
                if target.exists():
                    saved = backup / relative_path
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content), encoding="utf-8")
                changed.append(relative_path.as_posix())

            nachher = self.crawl_ist(root)
            fehler = (
                nachher["parse_errors"]
                or nachher["source"]["undefined_name_count"]
                or nachher["source"]["stub_count"]
                or any(module.get("destructive_findings") for module in nachher["modules"])
            )
            if fehler:
                raise ValueError("Re-Crawl nach Integration enthaelt ungueltige oder unsichere Befunde")
            if map_version_id is not None:
                self._aktualisiere_kartenstatus(
                    map_version_id, status, freigegeben, actual_map=nachher
                )
        except Exception:
            for relative_name in changed:
                target = root / relative_name
                saved = backup / relative_name
                if saved.exists():
                    shutil.copy2(saved, target)
                elif target.exists():
                    target.unlink()
            if map_version_id is not None:
                self._aktualisiere_kartenstatus(map_version_id, "zurueckgerollt", freigegeben)
            raise
        finally:
            shutil.rmtree(backup, ignore_errors=True)

        return {
            "format": "werft-task-integration-v1",
            "task_id": teilauftrag.get("task_id"),
            "status": status,
            "reason": "test_and_approval_confirmed",
            "map_update": {
                "status": "freigegeben",
                "node_ids": teilauftrag.get("node_ids", []),
                "files": changed,
                "actual_map_id": nachher["map_id"],
            },
        }

    def _aktualisiere_kartenstatus(
        self, map_version_id: int, status: str, freigegeben: bool, actual_map: dict | None = None
    ) -> None:
        if self.runtime_db_path is None:
            from runtime_paths import WERFT_DB_PATH
            db_path = WERFT_DB_PATH
        else:
            db_path = self.runtime_db_path
        with sqlite3.connect(db_path) as connection:
            if actual_map is None:
                connection.execute(
                    "UPDATE project_map_versions SET approval=?, status=? WHERE id=?",
                    ("freigegeben" if freigegeben else "freigabe_noetig", status, map_version_id),
                )
            else:
                connection.execute(
                    "UPDATE project_map_versions SET approval=?, status=?, actual_map_id=?, actual_map=? WHERE id=?",
                    (
                        "freigegeben" if freigegeben else "freigabe_noetig",
                        status,
                        actual_map.get("map_id"),
                        json.dumps(actual_map, ensure_ascii=False, sort_keys=True),
                        map_version_id,
                    ),
                )
            connection.commit()

    def baue_produkt(
        self,
        spezifikation: str,
        projektordner: str | Path,
        reparatur: Callable[[dict], dict[str, str]],
        max_zyklen: int = 3,
    ) -> dict:
        """Fuehrt begrenzte Diagnose-, Reparatur- und Pruefzyklen aus."""
        if max_zyklen < 1:
            raise ValueError("max_zyklen muss mindestens 1 sein")

        root = Path(projektordner)
        soll = self.erstelle_soll(spezifikation)
        history: list[dict] = []
        for zyklus in range(1, max_zyklen + 1):
            ist = self.crawl_ist(root)
            diff = self.vergleiche(soll, ist)
            qualitaetsfehler = (
                len(ist["parse_errors"])
                or ist["source"]["undefined_name_count"]
                or ist["source"]["stub_count"]
            )
            if diff["provenance"]["status"] == "bestaetigt" and not qualitaetsfehler:
                return {
                    "format": "werft-product-build-v1",
                    "status": "erfuellt",
                    "cycles": history,
                    "final_map": ist,
                    "final_diff": diff,
                }

            context = {
                "cycle": zyklus,
                "specification": spezifikation,
                "soll_map": soll,
                "ist_map": ist,
                "diff": diff,
                "task_regions": self.zerlege_teilauftraege(diff),
            }
            proposed_files = reparatur(context)
            if not proposed_files:
                history.append({"cycle": zyklus, "status": "keine_reparatur"})
                break

            backup = Path(tempfile.mkdtemp(prefix="werft-product-backup-"))
            changed_paths: list[Path] = []
            try:
                for relative_name, content in proposed_files.items():
                    relative_path = Path(relative_name)
                    if relative_path.is_absolute() or ".." in relative_path.parts:
                        raise ValueError(f"ungueltiger Produktpfad: {relative_name}")
                    target = root / relative_path
                    if target.exists():
                        backup_target = backup / relative_path
                        backup_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup_target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    changed_paths.append(relative_path)

                after = self.crawl_ist(root)
                after_diff = self.vergleiche(soll, after)
                invalid = (
                    len(after["parse_errors"])
                    or after["source"]["undefined_name_count"]
                    or after["source"]["stub_count"]
                )
                before_gap = len(diff["nodes"]["missing"]) + len(diff["edges"]["missing"])
                after_gap = len(after_diff["nodes"]["missing"]) + len(after_diff["edges"]["missing"])
                if invalid or after_gap > before_gap:
                    raise ValueError("Reparatur verschlechtert den geprueften Produktstand")
                change_event = self._protokolliere_kartenereignis(
                    root,
                    zyklus=zyklus,
                    status="uebernommen",
                    vorher=ist,
                    nachher=after,
                    dateien=[str(path) for path in changed_paths],
                    grund="Werft-Testbox bestanden",
                )
                change_event["vdb_point_id"] = self.lerne_kartenereignis(
                    change_event, root.name
                )
                history.append({
                    "cycle": zyklus,
                    "status": "reparatur_uebernommen",
                    "files": [str(path) for path in changed_paths],
                    "diff": after_diff,
                    "map_change": change_event,
                })
            except Exception as exc:
                for relative_path in changed_paths:
                    target = root / relative_path
                    saved = backup / relative_path
                    if saved.exists():
                        shutil.copy2(saved, target)
                    elif target.exists():
                        target.unlink()
                rollback_event = self._protokolliere_kartenereignis(
                    root,
                    zyklus=zyklus,
                    status="zurueckgerollt",
                    vorher=ist,
                    nachher=None,
                    dateien=[str(path) for path in changed_paths],
                    grund=str(exc),
                )
                rollback_event["vdb_point_id"] = self.lerne_kartenereignis(
                    rollback_event, root.name
                )
                history.append({
                    "cycle": zyklus,
                    "status": "zurueckgerollt",
                    "error": str(exc),
                    "files": [str(path) for path in changed_paths],
                    "map_change": rollback_event,
                })
                break
            finally:
                shutil.rmtree(backup, ignore_errors=True)

        final_map = self.crawl_ist(root)
        return {
            "format": "werft-product-build-v1",
            "status": "offen",
            "cycles": history,
            "final_map": final_map,
            "final_diff": self.vergleiche(soll, final_map),
        }

    def baue_produkt_autonom(
        self,
        spezifikation: str,
        projektordner: str | Path,
        max_zyklen: int = 3,
        uebernahme_freigegeben: bool = False,
        reparatur_adapter=None,
    ) -> dict:
        """Erzeugt einen LLM-Vorschlag oder baut ihn nach Werftentscheidung ein.

        LLM-Ausgaben werden standardmaessig nur als Vorschlag zurueckgegeben.
        Erst die Werft darf mit ``uebernahme_freigegeben=True`` den begrenzten
        Reparaturzyklus starten.
        """
        from autonomous_product_builder import AutonomousRepairAdapter

        def reparatur(context: dict) -> dict[str, str]:
            context = {**context, "project_root": str(projektordner)}
            return (reparatur_adapter or AutonomousRepairAdapter())(context)

        if not uebernahme_freigegeben:
            soll = self.erstelle_soll(spezifikation)
            ist = self.crawl_ist(projektordner)
            diff = self.vergleiche(soll, ist)
            context = {
                "cycle": 1,
                "project_root": str(projektordner),
                "specification": spezifikation,
                "soll_map": soll,
                "ist_map": ist,
                "diff": diff,
                "task_regions": self.zerlege_teilauftraege(diff),
            }
            return {
                "format": "werft-product-repair-proposal-v1",
                "status": "vorschlag_nicht_uebernommen",
                "proposal": reparatur(context),
                "diff": diff,
            }
        return self.baue_produkt(spezifikation, projektordner, reparatur, max_zyklen)

    def analysiere_projekt(
        self,
        spezifikation: str,
        projektordner: str | Path,
        max_nodes: int = 12,
    ) -> dict:
        """Führt den vollständigen deterministischen Soll/Ist-Lauf aus."""
        soll_karte = self.erstelle_soll(spezifikation)
        ist_karte = self.crawl_ist(projektordner)
        diff = self.vergleiche(soll_karte, ist_karte)
        task_regions = self.zerlege_teilauftraege(diff, max_nodes=max_nodes)
        map_version_id = self._persistiere_kartenstand(
            Path(projektordner).name,
            self._naechster_zyklus(Path(projektordner).name),
            soll_karte,
            ist_karte,
            diff,
            task_regions,
        )
        for task_region in task_regions:
            task_region["map_version_id"] = map_version_id
        return {
            "format": "werft-project-analysis-v1",
            "soll_map": soll_karte,
            "ist_map": ist_karte,
            "diff": diff,
            "refinement": self.specification_refinement(soll_karte, ist_karte, diff),
            "task_regions": task_regions,
            "map_version_id": map_version_id,
        }

    def _naechster_zyklus(self, projektname: str) -> int:
        if self.runtime_db_path is None:
            from runtime_paths import WERFT_DB_PATH
            db_path = WERFT_DB_PATH
        else:
            db_path = self.runtime_db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS project_map_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    cycle INTEGER NOT NULL,
                    expected_map_id TEXT,
                    actual_map_id TEXT,
                    expected_map TEXT NOT NULL,
                    actual_map TEXT NOT NULL,
                    map_diff TEXT NOT NULL,
                    task_regions TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'beobachtet',
                    approval TEXT NOT NULL DEFAULT 'freigabe_noetig',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(cycle), 0) + 1 FROM project_map_versions WHERE project_name=?",
                (projektname,),
            ).fetchone()
        return int(row[0])


def analysiere_projekt(
    specification_text: str, project_root: str | Path, max_nodes: int = 12
) -> dict:
    """Funktionsadapter für Skripte und externe Integrationen."""
    return WerftMapPipeline().analysiere_projekt(
        specification_text, project_root, max_nodes=max_nodes
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specification", type=Path)
    parser.add_argument("project_root", type=Path)
    args = parser.parse_args()
    result = analysiere_projekt(args.specification.read_text(encoding="utf-8"), args.project_root)
    print(json.dumps({
        "diff_status": result["diff"]["provenance"]["status"],
        "task_regions": len(result["task_regions"]),
        "parse_errors": len(result["ist_map"]["parse_errors"]),
    }, ensure_ascii=False, indent=2))
