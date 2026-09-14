#!/usr/bin/env python3
"""Diagnose-Werkzeug: laesst die Werft (Analyst-Rolle, gleiches Modell/Routing
wie analyst.py) fremden, unbekannten Code lesend untersuchen -- Betreiber-Auftrag
2026-08-04 ("interessanter Test waere, wenn sich die Werft ein altes,
fremdes Projekt mal ansieht und debuggt").

Bewusst KEIN Bestellung/task_pipeline-Weg: das bestehende Schema ist auf
Code-LIEFERUNG ausgelegt (ziel_ordner, kanonischer_name, auslieferung_schritt
kopiert generierten Code) -- fuer reine Diagnose ohne Repair passt das nicht.
Stattdessen ein eigenstaendiges Werft-Werkzeug, das dieselben Bausteine wie
der echte Analyst wiederverwendet (ask_model, host_fuer_rolle("analyst"),
_KLASSE_MUSTER/_METHODEN_MUSTER/_DICT_ZUGRIFF_MUSTER aus coder.py) statt eine
zweite, abweichende Analyse-Logik zu erfinden.

Zwei Schritte pro Datei (Betreiber-Prinzip 2026-08-04: "Systemmap gehoert zu den
ersten Tasks bei unbekanntem Code"):
1. Struktur-Map (Klassen/Methoden/Dict-Zugriffe/Imports) -- deterministisch,
   kein LLM, keine Kontext-Budget-Grenze (nur Regex).
2. LLM-Diagnose: Struktur-Map + voller Code -> die 3 gravierendsten Bugs.
   Nur fuer Dateien, die komplett in num_ctx passen (siehe MAX_ZEICHEN) --
   fuer die grossen UI-Dateien (>1000 Zeilen) braucht es die noch offene
   Patch-/Chunk-Architektur, hier bewusst nicht mitgeloest.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from coder import _KLASSE_MUSTER, _METHODEN_MUSTER, _DICT_ZUGRIFF_MUSTER
from configloader import config
from ollama_client import ask_model

RESULT_DIR = Path(__file__).parent / "diagnose_ergebnisse"
RESULT_DIR.mkdir(exist_ok=True)

# Grobe Heuristik wie in coder.py::_geschaetzte_prompt_tokens -- 3 statt 3.5
# Zeichen/Token, konservativ. Bei num_ctx=16384 und ~1500 Token Rest-Budget
# fuer die Antwort bleiben ca. 14000 Token / ~3.5 Zeichen = ~49000 Zeichen
# Spielraum fuer Code+Strukturmap+Prompt-Text zusammen -- MAX_ZEICHEN deutlich
# darunter, um Sicherheitsmarge zu behalten.
_MAX_ZEICHEN_FUER_VOLLTEXT_DIAGNOSE = 30000

_DIAGNOSE_PROMPT = """Du bist ein erfahrener Code-Reviewer. Die folgende Python-Datei stammt aus \
einem fremden, dir unbekannten Projekt -- du hast sie nie zuvor gesehen.

Struktur-Map (deterministisch extrahiert, Ground Truth):
{struktur_map}

Vollstaendiger Code:
```python
{code}
```

Aufgabe: Finde die 3 gravierendsten Bugs oder Robustheitsprobleme in diesem Code \
(echte Fehler -- Logikfehler, falsche Fehlerbehandlung, Race Conditions, \
Ressourcen-Lecks, Sicherheitsluecken -- keine Stil-/Formatierungsfragen). \
Fuer jeden Bug: Zeilennummer (falls erkennbar), kurze Beschreibung, konkretes \
Fehlerszenario (welcher Input/Zustand fuehrt zu welchem falschen Verhalten). \
Wenn du weniger als 3 echte Bugs findest, nenne nur die echten -- erfinde keine."""


def _struktur_map(code: str) -> str:
    klassen = _KLASSE_MUSTER.findall(code)
    methoden = [re.sub(r"\s+", " ", m).strip() for m in _METHODEN_MUSTER.findall(code)]
    dict_zugriffe: dict[str, set[str]] = {}
    for varname, key in _DICT_ZUGRIFF_MUSTER.findall(code):
        dict_zugriffe.setdefault(varname, set()).add(key)
    zeilen = [f"Klasse(n): {klassen or '(keine gefunden)'}", f"Anzahl Methoden: {len(methoden)}"]
    zeilen.append("Methoden:\n  " + "\n  ".join(methoden[:60]))
    if len(methoden) > 60:
        zeilen.append(f"  ... ({len(methoden) - 60} weitere)")
    if dict_zugriffe:
        vertraege = [f"{var}: {sorted(keys)}" for var, keys in list(dict_zugriffe.items())[:20]]
        zeilen.append("Dict-Zugriffe (Ausschnitt): " + "; ".join(vertraege))
    return "\n".join(zeilen)


def diagnostiziere_datei(pfad: Path) -> dict:
    code = pfad.read_text(encoding="utf-8", errors="ignore")
    struktur_map = _struktur_map(code)

    if len(code) > _MAX_ZEICHEN_FUER_VOLLTEXT_DIAGNOSE:
        return {
            "datei": str(pfad), "struktur_map": struktur_map, "diagnose": None,
            "uebersprungen_grund": (
                f"{len(code)} Zeichen > {_MAX_ZEICHEN_FUER_VOLLTEXT_DIAGNOSE} Limit -- "
                "braucht die noch offene Patch-/Chunk-Architektur (Task 'Coder-Architektur "
                "Patch statt Voll-Datei'), hier bewusst nicht geloest."
            ),
        }

    prompt = _DIAGNOSE_PROMPT.format(struktur_map=struktur_map, code=code)
    modell = config.get("ollama.model.analyst", "qwen3:14b")
    # Fund 2026-08-04 (Mentor, library_ai_v2-Lauf abgebrochen): host_fuer_rolle
    # ("analyst") routet bewusst auf den CPU-Ollama-Dienst (Port 11435) -- richtig
    # fuer die normalen, kleinen Analyst-Compliance-Checks, aber fuer diese Diagnose
    # mit vollen Datei-Dumps (bis 30000 Zeichen) auf CPU zu langsam fuer den 300s-
    # Timeout. Bewusst GPU-Host direkt statt der Rollen-Routing-Konvention -- andere
    # Workload-Charakteristik als der eigentliche Analyst.
    gpu_host = config.get("ollama.host", "http://localhost:11434")
    start = time.perf_counter()
    try:
        antwort = ask_model(prompt, model=modell, temperature=0.0, host=gpu_host, num_predict=1500)
    except Exception as exc:
        return {
            "datei": str(pfad), "struktur_map": struktur_map, "diagnose": None,
            "uebersprungen_grund": f"LLM-Aufruf fehlgeschlagen: {exc}",
        }
    dauer = time.perf_counter() - start
    return {
        "datei": str(pfad), "struktur_map": struktur_map, "diagnose": antwort,
        "modell": modell, "dauer_s": round(dauer, 1),
    }


def diagnostiziere(dateien: list[str], titel: str) -> Path:
    ergebnisse = [diagnostiziere_datei(Path(f)) for f in dateien]

    md = [f"# Werft-Fremdcode-Diagnose: {titel}", "", f"Analyst-Modell: {config.get('ollama.model.analyst', 'qwen3:14b')}", ""]
    for e in ergebnisse:
        md.append(f"## {e['datei']}")
        md.append("")
        md.append("### Struktur-Map")
        md.append("```")
        md.append(e["struktur_map"])
        md.append("```")
        md.append("")
        if e.get("uebersprungen_grund"):
            md.append(f"**Uebersprungen:** {e['uebersprungen_grund']}")
        else:
            md.append(f"### Diagnose ({e.get('dauer_s', '?')}s)")
            md.append(e["diagnose"] or "(keine Antwort)")
        md.append("")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = RESULT_DIR / f"diagnose_{titel}_{ts}.md"
    out_path.write_text("\n".join(md), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: werft_diagnose_fremdcode.py <titel> <datei1> [datei2 ...]")
        sys.exit(1)
    pfad = diagnostiziere(sys.argv[2:], sys.argv[1])
    print(f"Ergebnis gespeichert: {pfad}")
