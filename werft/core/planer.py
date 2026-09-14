from __future__ import annotations

"""Planer: vorgelagerte Aufbereitung einer Bestellung VOR dem Summarizer
(Neustart-Spec-Ergaenzung, Betreiber-Auftrag 2026-07-27, Tisch-Go 13:10).

Betreiber identifizierte zwei Baustellen, beide live am Forex-Simple-Prompt-Test
belegt (siehe project_forex_simple_prompt_test_2026-07-27): (1) das Aufteilen
einer Spezifikation in "verdaubare Haeppchen" -- der Mechanismus dafuer
(strategie.zerlege_bestellung -> plan_pruefen -> plan_freigeben ->
naechster_teil_freigeben) EXISTIERTE bereits, war aber nirgends automatisch
angebunden: jede Bestellung lief als EIN atomarer Task, egal wie komplex.
(2) eine "vorgelagerte Aufbereitung" (Planungssache) fehlte komplett -- es
gab keinen Schritt, der die ROHE Spezifikation VOR dem Coden auf innere
Widersprueche prueft. Live-Beleg: die Forex-Spec v1.5 hatte einen Changelog
("Hebel-Kompromiss 4:1 statt 5:1"), der Regelwerk-Body weiter unten war aber
unveraendert aus V1.4 uebernommen ("5:1") -- der Coder hat den veralteten
Wert genommen, weil niemand vor ihm den Widerspruch aufgeloest hat.

Beide Checks deterministisch per Regex (Ground Truth statt Raten -- dieselbe
Leitlinie wie bei den coder.py-Erweiterungen desselben Tages: _STATUS_WERT_
MUSTER, _EXTERNES_WERKZEUG_MUSTER), kein zusaetzlicher LLM-Call fuer die
Erkennung selbst. Nur die eigentliche Zerlegung (falls ausgeloest) nutzt
weiterhin strategie.zerlege_bestellung()s bestehenden LLM-Aufruf, unveraendert.

Bewusst NICHT blockierend (v1-Entscheidung): bei erkannter Zerlegungs-
Notwendigkeit wird ein Vorschlag ueber die bestehende, bereits Mentor-gated
Kette erzeugt (zerlege_bestellung schreibt nur 'plan_entwurf', keine echte
Bestellung wird eingereicht ohne plan_freigeben() -- Betreiber-Vorgabe 07-14/07-15
"kein automatisches Durchrauschen ohne Zwischenpruefung"). Der urspruengliche
Task laeuft PARALLEL unveraendert weiter -- kein neuer Pause-Zustand, keine
Verhaltensaenderung am bestehenden Pfad. Ob spaeter automatisch pausiert
werden soll (sobald die Zerlegung freigegeben ist), ist eine separate,
spaetere Entscheidung."""
import re
import sqlite3
from pathlib import Path

from configloader import config
from memory import remember_system
import strategie

from runtime_paths import WERFT_DB_PATH as DB_PATH

_MARKER = "[PLANER-GEPRUEFT]"

_AENDERUNGS_HEADING_MUSTER = re.compile(
    r"^(?:#{1,4}\s*|\*{0,2})(Aenderungen|Änderungen|Changelog|Change[- ]?log)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_ENTSCHIEDEN_ZEILE_MUSTER = re.compile(
    r"^[-*]\s*\*{0,2}.{0,60}(ENTSCHIEDEN|korrigiert|statt \d).{0,300}$",
    re.IGNORECASE | re.MULTILINE,
)
# Bewusst NUR "R<Zahl>"-Praefixe (z.B. "### R1") -- NICHT generische
# nummerierte Abschnitts-Ueberschriften ("## 1. Zweck", "## 2. Ziel"), die
# jedes strukturierte Dokument hat und keine "digestible Regel-Haeppchen"
# sind. Fund beim ersten Testlauf (live, vor Deploy): die generische Variante
# zaehlte 17 Treffer allein aus normaler Kapitel-Gliederung -- falsches
# Signal fuer Zerlegungs-Bedarf.
_NUMMERIERTE_REGEL_MUSTER = re.compile(r"^#{1,4}\s*(R\d+)\b", re.MULTILINE)


def _widerspruchs_hinweis(text: str) -> str | None:
    """Findet einen Changelog-/Entscheidungs-Block nahe dem Anfang des Dokuments
    und extrahiert die konkreten Korrektur-Zeilen woertlich -- deterministisch,
    kein LLM-Raten. Gibt None zurueck wenn kein solcher Block gefunden wird."""
    heading = _AENDERUNGS_HEADING_MUSTER.search(text)
    if not heading:
        return None
    block_ende = text.find("\n---", heading.end())
    block = text[heading.end():block_ende if block_ende != -1 else heading.end() + 2000]
    treffer = [m.group(0).strip() for m in _ENTSCHIEDEN_ZEILE_MUSTER.finditer(block)]
    if not treffer:
        return None
    return (
        "PLANER: Nachtraegliche Korrekturen/Entscheidungen aus dem Changelog "
        "dieses Dokuments (HABEN VORRANG vor evtl. widersprechenden Angaben "
        "weiter unten im selben Dokument, falls die dort einen AELTEREN Stand "
        "zeigen -- NICHT den aelteren Wert verwenden):\n" + "\n".join(treffer)
    )


def _anzahl_regelabschnitte(text: str) -> int:
    """Zaehlt klar nummerierte Regel-/Abschnitts-Ueberschriften (z.B. '### R1',
    '## 3.'). Ground-Truth-Heuristik statt Bauchgefuehl: ab konfigurierbarer
    Schwelle ist eine Bestellung ein Kandidat fuer Zerlegung in Teil-Bestellungen."""
    return len({m.group(1) for m in _NUMMERIERTE_REGEL_MUSTER.finditer(text)})


def run_once() -> dict:
    """Bearbeitet EINEN status='audit'-Task, der noch nicht vom Planer geprueft
    wurde (Marker in knowledge_links verhindert Mehrfachbearbeitung/wiederholte
    LLM-Aufrufe bei jedem Zyklus). Laeuft in master_loop.zyklus() VOR dem
    Summarizer, damit dessen Lastenheft bereits die Planer-Anreicherung sieht."""
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT task_id, knowledge_links FROM task_pipeline "
            "WHERE status='audit' AND knowledge_links NOT LIKE ? LIMIT 1",
            (f"%{_MARKER}%",),
        ).fetchone()
        if not row:
            return {"bearbeitet": False}
        task_id, knowledge_links = row
        text = knowledge_links

        ergebnis = {
            "bearbeitet": True, "task_id": task_id,
            "widerspruch_gefunden": False, "zerlegung_vorgeschlagen": False,
        }
        zusatz = ""

        hinweis = _widerspruchs_hinweis(text)
        if hinweis:
            zusatz += "\n\n" + hinweis
            ergebnis["widerspruch_gefunden"] = True
            remember_system(
                owner="planer", target=task_id,
                content=f"PLANER: Widerspruchs-Hinweis erkannt und als Ground Truth angehaengt: {hinweis[:300]}",
                tags="planer,widerspruchs_check",
            )

        n_regeln = _anzahl_regelabschnitte(text)
        schwelle = int(config.get("planer.zerlegung_schwelle", 5))
        if n_regeln >= schwelle:
            ergebnis["zerlegung_vorgeschlagen"] = True
            bestellung_id = task_id.replace("task_bestellung_", "")
            bereits_zerlegt = con.execute(
                "SELECT 1 FROM strategie_planung WHERE ueber_bestellung=?", (bestellung_id,),
            ).fetchone()
            if not bereits_zerlegt:
                spec_row = con.execute(
                    "SELECT spec_inhalt FROM bestellungen WHERE bestellung_id=?", (bestellung_id,),
                ).fetchone()
                spec_fuer_zerlegung = spec_row[0] if spec_row and spec_row[0] else text
                strategie.zerlege_bestellung(bestellung_id, spec_fuer_zerlegung)
                remember_system(
                    owner="planer", target=task_id,
                    content=(
                        f"MENTOR-VORSCHLAG: {n_regeln} nummerierte Regel-Abschnitte erkannt "
                        f"(Schwelle {schwelle}) -- Zerlegungs-Entwurf via strategie.zerlege_bestellung() "
                        f"angelegt (status=plan_entwurf, noch NICHT freigegeben). Pruefen mit "
                        f"strategie.plan_pruefen('{bestellung_id}'), freigeben mit "
                        f"strategie.plan_freigeben('{bestellung_id}'). Urspruenglicher Task laeuft "
                        f"unveraendert PARALLEL weiter (nicht blockierend, bewusste v1-Entscheidung)."
                    ),
                    tags="planer,zerlegung_vorschlag,mentor_eskalation",
                )
                con.execute(
                    "INSERT INTO mentor_actions_log (action_type, beschreibung, status) VALUES (?, ?, 'pending_review')",
                    ("zerlegung_vorschlag", f"{bestellung_id}: {n_regeln} Regel-Abschnitte, Planer-Vorschlag bereit zur Pruefung"),
                )

        con.execute(
            "UPDATE task_pipeline SET knowledge_links=? WHERE task_id=?",
            (text + zusatz + f"\n{_MARKER}", task_id),
        )
        con.commit()
        return ergebnis
    finally:
        con.close()


if __name__ == "__main__":
    print(run_once())
