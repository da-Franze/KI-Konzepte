"""LLM-Adapter fuer den kontrollierten Bau einer Produktarbeitskopie."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from configloader import config
from json_utils import extrahiere_json_objekt
from ollama_client import ask_model


class AutonomousRepairAdapter:
    """Verbindet Analyst und Coder, ohne selbst Dateien zu schreiben."""

    def __init__(self, model_call: Callable[..., str] | None = None):
        self.model_call = model_call or ask_model

    def __call__(self, context: dict) -> dict[str, str]:
        diagnosis = self._analyse(context)
        if diagnosis.get("status") == "kein_reparaturbedarf":
            return {}
        return self._codieren(context, diagnosis)

    def _analyse(self, context: dict) -> dict:
        prompt = (
            "Du bist der Analyst einer Software-Werft. Bewerte nur den folgenden "
            "deterministischen Befund. Erfinde keine Fehler. Antworte ausschließlich "
            "als JSON: {\"status\": \"reparatur_noetig\" oder "
            "\"kein_reparaturbedarf\", \"befunde\": [string]}.\n\n"
            + json.dumps({
                "diff": context["diff"],
                "source_quality": context["ist_map"]["source"],
                "task_regions": context["task_regions"],
            }, ensure_ascii=False, indent=2)
        )
        result = self.model_call(
            prompt,
            model=config.get("ollama.model.analyst", "qwen3:14b"),
            temperature=0.0,
            host=config.get("ollama.host"),
            num_predict=int(config.get("ollama.num_predict.analyst", 900)),
        )
        parsed = extrahiere_json_objekt(result)
        if not isinstance(parsed, dict) or parsed.get("status") not in {
            "reparatur_noetig", "kein_reparaturbedarf"
        }:
            raise ValueError("Analyst lieferte kein gueltiges Reparatururteil")
        return parsed

    def _codieren(self, context: dict, diagnosis: dict) -> dict[str, str]:
        root = Path(context["project_root"])
        allowed_files = self._repair_scope(context)
        if not allowed_files:
            raise ValueError("kein begrenzter Reparaturbereich in der Projektkarte")
        expansion_candidates = self._expansion_candidates(context, allowed_files)
        files = {}
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root)
            if str(relative) not in allowed_files:
                continue
            files[str(relative)] = path.read_text(encoding="utf-8", errors="ignore")
        prompt = (
            "Du bist der Coder einer Software-Werft. Repariere nur belegte "
            "Befunde innerhalb der angegebenen Graphregion. Dateien außerhalb "
            "des Reparaturbereichs dürfen nicht geändert werden. "
            "Befunde. Falls eine verbundene Nachbardatei notwendig ist, darfst du "
            "eine begründete Erweiterung anfordern. Gib ausschließlich JSON im Format "
            "{\"files\": {...}, \"scope_expansion\": {\"paths\": [...], "
            "\"reason\": \"...\"}} "
            "zurück. Eine Erweiterung ist nur für genannte Kandidaten erlaubt. "
            "zurück. Unveränderte Dateien dürfen entfallen. Keine Markdown-Zäune.\n\n"
            f"Diagnose:\n{json.dumps(diagnosis, ensure_ascii=False, indent=2)}\n\n"
            f"Erlaubte Dateien:\n{json.dumps(sorted(allowed_files), ensure_ascii=False)}\n\n"
            f"Begründete Erweiterung möglich auf:\n{json.dumps(sorted(expansion_candidates), ensure_ascii=False)}\n\n"
            f"Ist-Code:\n{json.dumps(files, ensure_ascii=False)}"
        )
        result = self.model_call(
            prompt,
            model=config.get("ollama.model.coder", "qwen3:32b"),
            temperature=0.1,
            host=config.get("ollama.host"),
            num_predict=int(config.get("ollama.num_predict.coder", 6144)),
        )
        parsed = extrahiere_json_objekt(result)
        proposed = parsed.get("files") if isinstance(parsed, dict) else None
        if not isinstance(proposed, dict) or not all(
            isinstance(path, str) and isinstance(code, str)
            for path, code in proposed.items()
        ):
            raise ValueError("Coder lieferte keinen gueltigen Dateipatch")
        expansion = parsed.get("scope_expansion", {}) if isinstance(parsed, dict) else {}
        if expansion:
            paths = expansion.get("paths") if isinstance(expansion, dict) else None
            reason = expansion.get("reason", "").strip() if isinstance(expansion, dict) else ""
            if not isinstance(paths, list) or not reason or len(reason) < 20:
                raise ValueError("Regionserweiterung braucht Pfade und eine Begruendung")
            expansion_paths = set(paths)
            if not expansion_paths <= expansion_candidates:
                raise ValueError("Regionserweiterung verlaesst den Karten-Nachbarschaftsbereich")
            allowed_files |= expansion_paths
        unauthorized = set(proposed) - allowed_files
        if unauthorized:
            raise ValueError(
                "Coder-Patch verlaesst die Reparaturregion: "
                + ", ".join(sorted(unauthorized))
            )
        return proposed

    @staticmethod
    def _repair_scope(context: dict) -> set[str]:
        """Ermittelt Dateien nur aus Diff-Regionen oder lokalen Befunden."""
        allowed: set[str] = set()
        for region in context.get("task_regions", []):
            for node in region.get("nodes", []):
                node_id = node.get("id", "")
                parts = node_id.split(":", 2)
                if len(parts) >= 2 and parts[1].endswith(".py"):
                    allowed.add(parts[1])
        for module in context.get("ist_map", {}).get("modules", []):
            if module.get("parse_error") or module.get("stub_findings") or module.get("undefined_name_findings"):
                if module.get("path"):
                    allowed.add(module["path"])
        return allowed

    @staticmethod
    def _expansion_candidates(context: dict, allowed: set[str]) -> set[str]:
        """Erlaubt nur direkte Nachbarn der aktuellen Region im Call-Graph."""
        candidates: set[str] = set()
        for module in context.get("ist_map", {}).get("modules", []):
            module_path = module.get("path")
            if not module_path:
                continue
            for edge in module.get("edges", []):
                endpoints = [edge.get("source"), edge.get("target_id")]
                endpoint_files = {
                    part.split(":", 2)[1]
                    for part in endpoints
                    if isinstance(part, str) and len(part.split(":", 2)) >= 2
                }
                if endpoint_files & allowed:
                    candidates.add(module_path)
                    candidates.update(endpoint_files)
        return candidates - allowed