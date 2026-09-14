from __future__ import annotations

"""Robuste JSON-Extraktion aus LLM-Antworten.

Fund 2026-07-17: 5 Werft-Rollen (analyst.py, judge.py, summarizer.py,
strategie.py, rnd_lab_stratege.py) extrahierten JSON aus LLM-Antworten mit
`antwort[antwort.find("{"):antwort.rfind("}") + 1]` -- bricht sobald die
Antwort mehr als einen `{...}`/`[...]`-Block enthaelt (z.B. Erklaertext mit
einem Beispiel-JSON vor dem eigentlichen Verdikt) oder unausgeglichene
Klammern in einem Textfeld (z.B. "grund": "bei x { y }") hat: `rfind` greift
dann die falsche schliessende Klammer, `json.loads` wirft JSONDecodeError.
Live reproduziert (beispielserie_teil2_router_agent-Korrekturrunde): derselbe
Parse-Fehler bei jedem Zyklus, master_loop blieb dank Fehlertoleranz am Leben,
aber der Task war ohne diesen Fix dauerhaft blockiert.

Nutzt `json.JSONDecoder().raw_decode()` ab der ersten oeffnenden Klammer --
das ist Pythons eigener Parser, der genau EIN valides JSON-Objekt liest und
den Rest (Erklaertext, weitere Bloecke) ignoriert, statt naiv bis zur
LETZTEN schliessenden Klammer im gesamten Text zu greifen. Dasselbe Muster
wie bereits im ki-hub-Projekt bewaehrt (agent.py._extract_text_tool_call()).

Fund 2026-07-23 (Mentor, live reproduziert an tisch_archivierer_stufe1 --
Analyst blieb ueber mehrere Master-Loop-Zyklen hinweg mit identischem
JSONDecodeError haengen, blockierte dadurch die Analyst-Rolle GLOBAL fuer
alle Bestellungen, nicht nur die betroffene): der Prompt in analyst.py
warnt das Modell bereits explizit (seit 2026-07-17), im "grund"-Text keine
unescapten doppelten Anfuehrungszeichen zu verwenden -- qwen3:14b haelt
sich trotz temperature=0.0 nicht zuverlaessig daran (live: zitierte
`Path(__file__).parent / "dreier_tisch.db"` woertlich mit doppelten
Anfuehrungszeichen im String-Wert). Reine Prompt-Instruktion ist bei diesem
rein mechanischen Formatierungsfehler kein verlaesslicher Schutz -- deshalb
zusaetzlich ein struktureller Reparatur-Fallback: schlaegt raw_decode fehl,
wird angenommen, dass das letzte String-Feld (typischerweise "grund"/
"begruendung") unescapte '"' enthaelt, die bis zum literalen Objektende
laufen -- diese werden nachtraeglich escaped und der Parse-Versuch
wiederholt. Deckt genau das ab, was Prompt-Befolgung nicht garantieren
konnte, ohne das Modell "zu ueberreden" -- ein bekannter, mechanischer
Fehler wird strukturell abgefangen statt erneut nur per Anweisung erhofft.
"""
import json
import re

# Fund 2026-07-23: greift NUR wenn raw_decode() bereits gescheitert ist.
# Erwartet die typische Analyst/Judge/Summarizer-Form {"...": ..., "<letztes
# Feld>": "<text mit unescapten \" >"} -- das letzte String-Feld laeuft bis
# zum literalen Objektende ("}` als letzte nicht-Whitespace-Zeichen).
_LETZTES_STRING_FELD = re.compile(r'("(?:[^"\\]|\\.)*"\s*:\s*")(.*)(")\s*\}\s*$', re.DOTALL)


def _repariere_unescapte_anfuehrungszeichen(text: str) -> str | None:
    """Escaped nachtraeglich '"' im letzten String-Wert eines mutmasslichen
    JSON-Objekts. Gibt None zurueck wenn das Muster nicht passt (kein
    Reparaturversuch moeglich) -- ruft NIE json.loads selbst auf, das bleibt
    Aufgabe der aufrufenden Funktion."""
    match = _LETZTES_STRING_FELD.search(text)
    if not match:
        return None
    praefix, roh_wert, suffix = match.group(1), match.group(2), match.group(3)
    repariert_wert = roh_wert.replace('\\"', '"').replace('"', '\\"')
    return text[: match.start()] + praefix + repariert_wert + suffix + "}"


def extrahiere_json_objekt(text: str) -> dict:
    """Liest das erste valide JSON-Objekt ({...}) aus einem Text.

    Raises:
        ValueError: keine oeffnende geschweifte Klammer im Text gefunden.
        json.JSONDecodeError: ab der ersten Klammer steht kein valides JSON,
            auch nach dem Reparaturversuch nicht.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("Kein '{' im Text gefunden -- keine JSON-Antwort erkennbar.")
    try:
        return json.JSONDecoder().raw_decode(text, start)[0]
    except json.JSONDecodeError:
        repariert = _repariere_unescapte_anfuehrungszeichen(text[start:])
        if repariert is None:
            raise
        return json.JSONDecoder().raw_decode(repariert, 0)[0]


def extrahiere_json_liste(text: str) -> list:
    """Liest die erste valide JSON-Liste ([...]) aus einem Text.

    Raises:
        ValueError: keine oeffnende eckige Klammer im Text gefunden.
        json.JSONDecodeError: ab der ersten Klammer steht kein valides JSON.
    """
    start = text.find("[")
    if start == -1:
        raise ValueError("Kein '[' im Text gefunden -- keine JSON-Antwort erkennbar.")
    return json.JSONDecoder().raw_decode(text, start)[0]
