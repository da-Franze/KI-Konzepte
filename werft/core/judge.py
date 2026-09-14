"""LLM-as-Judge (WERFT_NEUSTART_SPEZIFIKATION.md Abschnitt 13).

Bewertet die Wichtigkeit JEDES offenen Gaps auf einer Skala 1-10, IMMER mit
Pflicht-Begruendung ("warum") -- eine Bewertung ohne Begruendung ist eine
narrative Entscheidung ohne Beleg (8D-Drift-Report, WERFT_V8_3_SPEZIFIKATION.md
Abschnitt 6.4). Das Ergebnis schreibt system_gaps.severity, sodass der
Coordinator (Abschnitt 6) danach priorisiert -- hoechste Wichtigkeit zuerst.
"""
import json
import sqlite3
from pathlib import Path

from configloader import config
from json_utils import extrahiere_json_objekt
from ollama_client import ask_model

from runtime_paths import WERFT_DB_PATH as DB_PATH

PROMPT_TEMPLATE = """Bewerte die Wichtigkeit dieses offenen Deltas (Luecke zwischen Soll und Ist)
auf einer Skala 1-10. 10 = blockiert alles Weitere / kritischer Kernbaustein,
1 = kosmetisch / kann beliebig warten.

DELTA: {beschreibung}

Antworte NUR als JSON: {{"wichtigkeit": <1-10>, "warum": "<konkrete Begruendung,
mindestens 1 Satz -- keine Floskel wie 'ist wichtig'>"}}
"""


def bewerte(gap_key: str, description: str) -> dict:
    antwort = ask_model(
        PROMPT_TEMPLATE.format(beschreibung=description),
        model=config.get("ollama.model.judge", "qwen3:14b"),
        temperature=0.1,
    )
    verdikt = extrahiere_json_objekt(antwort)
    warum = (verdikt.get("warum") or "").strip()
    if len(warum) < 10:
        # Pflicht-Begruendung nicht erfuellt -- keine Bewertung ohne Beleg eintragen
        # (Non-Invention-Policy, analog memory.py MIN_GRUND_LAENGE).
        raise ValueError(f"Judge lieferte keine brauchbare Begruendung fuer {gap_key}: {antwort!r}")
    wichtigkeit = int(verdikt.get("wichtigkeit", 0))
    wichtigkeit = max(1, min(10, wichtigkeit))
    return {"wichtigkeit": wichtigkeit, "warum": warum}


def judge_schritt() -> int:
    """Bewertet jeden offenen Gap, der noch keinen judge_log-Eintrag hat.
    Schreibt judge_log UND aktualisiert system_gaps.severity, damit der
    Coordinator nach Wichtigkeit priorisiert. Gibt Anzahl bewerteter Gaps zurueck."""
    con = sqlite3.connect(DB_PATH)
    try:
        gaps = con.execute(
            "SELECT gap_key, description FROM system_gaps "
            "WHERE resolved=0 AND gap_key NOT IN (SELECT gap_key FROM judge_log)"
        ).fetchall()
        bewertet = 0
        for gap_key, description in gaps:
            try:
                verdikt = bewerte(gap_key, description or gap_key)
            except (ValueError, json.JSONDecodeError) as e:
                print(f"Judge uebersprungen fuer {gap_key}: {e}")
                continue
            con.execute(
                "INSERT INTO judge_log (gap_key, wichtigkeit, warum) VALUES (?, ?, ?)",
                (gap_key, verdikt["wichtigkeit"], verdikt["warum"]),
            )
            con.execute(
                "UPDATE system_gaps SET severity=? WHERE gap_key=?",
                (verdikt["wichtigkeit"], gap_key),
            )
            bewertet += 1
        con.commit()
        return bewertet
    finally:
        con.close()


if __name__ == "__main__":
    n = judge_schritt()
    print(f"{n} Gap(s) bewertet" if n else "Keine unbewerteten offenen Gaps")
