"""Summarizer: Gap-Beschreibung -> konkretes Lastenheft (Neustart-Spec Abschnitt 7.2).

Ergaenzt um eines Partnersystems verifizierte Lehren (project_denker_struktureinsichten_2026-07-04):
- Lehre #2: Zahlen/Formeln/exakte Relationen aus der Gap-Beschreibung woertlich
  erhalten, nicht destillieren (wichtigster Punkt seiner Ernte).
- Lehre #4: explizites Auswahlkriterium fuer Feature-Auswahl statt naiver
  Erst-/Salienz-Auswahl.
"""
import json
import re
import sqlite3
from pathlib import Path

from configloader import config
from json_utils import extrahiere_json_objekt
from memory import remember_system, BegruendungZuKurzError
from ollama_client import ask_model, host_fuer_rolle
from planer import _anzahl_regelabschnitte
from vdb_client import vdb
from role_dna import load_role_dna

from runtime_paths import WERFT_DB_PATH as DB_PATH

# Fund 2026-07-15 (beispielserie_teil6, Betreiber-Frage "braucht der Summarizer
# auch ein 4-Augen-Prinzip?"): Der Summarizer erfand einen konkreten Datenbank-
# Pfad (~/library_ai/data/content_filter.db) UND ein konkretes Blockier-Beispiel
# mit Schweregraden -- beides stand nirgendwo in der echten Bestellung, sondern
# kam aus dem "AEHNLICHE FRUEHERE FAELLE"-VDB-Kontext (Konzept-Mining-Eintrag
# ueber den alten library_ai-Code). Der Analyst pruefte danach korrekt GEGEN das
# kontaminierte SPEC_JSON und lehnte den (eigentlich richtigen) Code ab -- eine
# Erfindung des Summarizers wurde so zur scheinbar berechtigten Anforderung.
_PFAD_MUSTER = re.compile(r"[~./][\w./-]{3,}")
_ZITAT_MUSTER = re.compile(r"[\"']([^\"']{3,40})[\"']")


def _unbelegte_details(required_features: list[str], quelltext: str) -> list[str]:
    """Deterministischer Traceability-Check (kein LLM-Zweitgutachten -- dieselbe
    Lehre wie ueberall heute: pruefbare Checks vor Prompt-Text). Findet Pfad-
    artige oder woertlich zitierte Details in required_features, die NICHT im
    Quelltext (der echten Mission) vorkommen -- typisches Muster erfundener
    Spezifika."""
    funde = []
    for feature in required_features:
        for muster in (_PFAD_MUSTER, _ZITAT_MUSTER):
            for treffer in muster.findall(feature):
                if treffer not in quelltext:
                    funde.append(f"{feature!r} enthaelt unbelegtes Detail {treffer!r}")
    return funde

PROMPT_TEMPLATE = """{dna}

Du bist der Summarizer. Verwandle die folgende Luecken-Beschreibung in ein
konkretes technisches Lastenheft.

LUECKE: {gap_description}

AEHNLICHE FRUEHERE FAELLE (aus der Wissens-Schicht, zur Orientierung, nicht kopieren):
{kontext}

Antworte NUR mit einem JSON-Objekt in genau dieser Form:
{{
  "goal": "<ein Satz: was soll dieses Modul leisten>",
  "tech_stack": "<z.B. Python, sqlite3, requests>",
  "required_features": ["<Feature 1>", "<Feature 2>", "..."]
}}

Regeln:
- Wenn die Luecken-Beschreibung zu vage ist um konkrete Features abzuleiten:
  schreibe das explizit als "required_features": ["UNKLAR: <was fehlt>"]
  statt generische Platzhalter-Features zu erfinden (Non-Invention-Policy).
- Mindestens 2, hoechstens {feature_cap} Features.
- ZAHLEN, FORMELN UND EXAKTE RELATIONEN aus der Luecken-Beschreibung woertlich
  uebernehmen, nicht umformulieren oder runden (Verlust bei Destillation vermeiden).
- Waehle Features nach diesem Kriterium: was ist fuer die Kernfunktion des Moduls
  unverzichtbar (nicht: was steht zuerst/am auffaelligsten in der Beschreibung).
"""

# Fund 2026-07-27 (Forex-Re-Test, project_forex_retest_planer_2026-07-27): der
# bisher FEST verdrahtete Cap von 6 warf bei 9 klar unterscheidbaren Regeln
# (R1-R9) eine davon (R7, den Hebel) aus dem Lastenheft -- der Coder fiel ohne
# eigenen required_features-Eintrag auf den rohen Spec-Text zurueck, wo eine
# aeltere, falsche Zahl mehrfach stand und den einzelnen Korrektur-Hinweis
# ueberstimmte. Cap jetzt dynamisch aus der Zahl klar nummerierter Regel-
# Abschnitte (dieselbe Ground-Truth-Heuristik wie planer.py._anzahl_regel-
# abschnitte, ein Widerspruch/eine Ueberschrift ist kein Feature) -- nie unter
# den bisherigen Default 6 (Betreiber-Go 2026-07-27, Tisch 22:23 "Feature-Cap
# dynamisch"), nach oben mit 15 gedeckelt (mehr gehoert in eine Zerlegung
# durch den Planer, nicht in ein noch groesseres Lastenheft).
def _dynamischer_feature_cap(gap_description: str) -> int:
    n_regeln = _anzahl_regelabschnitte(gap_description)
    return max(6, min(n_regeln, 15))


ROLE_DNA = load_role_dna("summarizer")


def _kontext_aus_vdb(gap_description: str) -> str:
    treffer = vdb.search(gap_description, top_k=3)
    if not treffer:
        return "(keine relevanten frueheren Faelle gefunden)"
    return "\n".join(f"- {t['text'][:200]}" for t in treffer)


def run_once() -> bool:
    """Holt einen Task mit status='audit', erzeugt ein Lastenheft, setzt status='spec'.
    Gibt True zurueck wenn ein Task bearbeitet wurde, sonst False (keine Arbeit)."""
    con = sqlite3.connect(DB_PATH)
    try:
        print("SUMMARIZER stage: DB query", flush=True)
        row = con.execute(
            "SELECT task_id, knowledge_links FROM task_pipeline WHERE status='audit' LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        task_id, knowledge_links = row

        print("SUMMARIZER stage: VDB search START", flush=True)
        kontext = _kontext_aus_vdb(knowledge_links or "")
        print("SUMMARIZER stage: VDB search DONE", flush=True)
        feature_cap = _dynamischer_feature_cap(knowledge_links or "")
        prompt = PROMPT_TEMPLATE.format(
            dna=ROLE_DNA, gap_description=knowledge_links or "", kontext=kontext,
            feature_cap=feature_cap,
        )
        print(f"SUMMARIZER prompt length: {len(prompt)}", flush=True)
        print("SUMMARIZER stage: model call START", flush=True)
        antwort = ask_model(
            prompt,
            model=config.get("ollama.model.summarizer", "qwen3:14b"),
            temperature=0.2,
            host=host_fuer_rolle("summarizer"),
        )
        print("SUMMARIZER stage: model call DONE", flush=True)
        # Non-Invention-Policy: JSON muss geliefert werden, kein Nachbessern/Erfinden
        spec_json = extrahiere_json_objekt(antwort)
        print("SUMMARIZER stage: JSON parsed", flush=True)

        # 4-Augen-Prinzip fuer den Summarizer (Betreiber-Vorschlag 2026-07-15):
        # unbelegte Pfade/Zitate NIE stillschweigend ins Lastenheft durchreichen --
        # der Analyst wuerde spaeter GEGEN eine Erfindung pruefen, nicht gegen die
        # echte Bestellung.
        quelltext = knowledge_links or ""
        funde = _unbelegte_details(spec_json.get("required_features", []), quelltext)
        if funde:
            spec_json["required_features"] = [
                f for f in spec_json.get("required_features", [])
                if not any(f in fund for fund in funde)
            ]
            beschreibung = (
                f"Summarizer-Erfindungs-Check: {len(funde)} unbelegte(s) Detail(s) "
                f"aus dem Lastenheft entfernt: {funde}"
            )
            try:
                remember_system(owner="summarizer_check", target=task_id.replace("task_", ""),
                                 content=beschreibung, tags="erfindungs_check")
            except BegruendungZuKurzError:
                pass

        con.execute(
            "UPDATE task_pipeline SET status='spec', knowledge_links=? WHERE task_id=?",
            (knowledge_links + " | SPEC_JSON:" + json.dumps(spec_json, ensure_ascii=False), task_id),
        )
        con.commit()
        print("SUMMARIZER stage: DB update DONE", flush=True)
        return True
    finally:
        con.close()


if __name__ == "__main__":
    bearbeitet = run_once()
    print("Task bearbeitet" if bearbeitet else "Keine offenen audit-Tasks")
