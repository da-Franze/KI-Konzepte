from __future__ import annotations

"""Mentor-Actions-Log-Pflege: automatische Bereinigung stale gewordener
Eskalationen (Betreiber-Auftrag 2026-07-27, Tisch 15:54: "bereinigen um nur die
wichtigen Sachen uebrig zu haben, ggf. auch automatisieren").

Befund vor dem Bau (live in werft.db geprueft, nicht geraten): 242 von 242
pending_review-Eintraegen waren nie approved/rejected worden -- exakt die in
STATUS.md genannte Erfolgskriterien-Luecke (Kriterium #2: "nur approved-
Eintraege vorhanden"). Stichprobe von 6 der 15 debugger_eskalation-Eintraege
(07-19 bis 07-23): ALLE zugehoerigen Bestellungen waren laengst 'geliefert'
-- das Problem hatte sich von selbst geloest, aber niemand hatte den
Log-Eintrag geschlossen. Das Eskalationssystem verliert dadurch seinen Zweck
(Alarm-Ermuedung): echte offene Faelle ertrinken zwischen Hunderten laengst
erledigten.

Zwei deterministische, konservative Regeln -- nur schliessen wenn wirklich
sicher, alles andere bleibt fuer echte Pruefung stehen:
  A) Eintrag referenziert einen task_bestellung_<id> -- ist die zugehoerige
     Bestellung inzwischen 'geliefert' oder der Task 'merged'/'archived',
     ist die Eskalation ueberholt (Problem hat sich von selbst geloest).
  B) Eintrag ist eine byte-identische Dublette einer wiederkehrenden
     rollen_fehler-Meldung -- nur die JUENGSTE Instanz bleibt offen
     (repraesentativ fuer "kommt noch vor"), aeltere Dubletten werden
     geschlossen.

Nicht destruktiv: es wird NUR status/approved_by/approved_at gesetzt, keine
Zeile geloescht -- jederzeit auf 'pending_review' zuruecksetzbar."""
import re
import sqlite3
from pathlib import Path

from configloader import config

from runtime_paths import WERFT_DB_PATH as DB_PATH

_BESTELLUNG_ID_MUSTER = re.compile(r"\btask_bestellung_(\w+?)(?::\w+)?\b")

_GRUND_RESOLVED = "Mentor (auto-bereinigt: Bestellung/Task inzwischen resolved)"
_GRUND_DUPLIKAT = "Mentor (auto-bereinigt: Duplikat, juengere Instanz bleibt offen)"


def _bestellung_id_aus_beschreibung(beschreibung: str) -> str | None:
    m = _BESTELLUNG_ID_MUSTER.search(beschreibung)
    return m.group(1) if m else None


def _ist_resolved(con: sqlite3.Connection, bestellung_id: str) -> bool:
    b_status = con.execute(
        "SELECT status FROM bestellungen WHERE bestellung_id=?", (bestellung_id,)
    ).fetchone()
    if b_status and b_status[0] == "geliefert":
        return True
    t_status = con.execute(
        "SELECT status FROM task_pipeline WHERE task_id=?",
        (f"task_bestellung_{bestellung_id}",),
    ).fetchone()
    return bool(t_status and t_status[0] in ("merged", "archived"))


def bereinigen_schritt() -> dict:
    """Einmal aufrufbar pro Zyklus (guenstig -- nur SQL, kein LLM-Call).
    Idempotent: bereits geschlossene Eintraege (status != 'pending_review')
    werden nicht erneut angefasst."""
    if not config.get("mentor.log_pflege_aktiv", True):
        return {"aktiv": False}

    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT id, beschreibung FROM mentor_actions_log WHERE status='pending_review'"
        ).fetchall()

        geschlossen_resolved = 0
        for eintrag_id, beschreibung in rows:
            bestellung_id = _bestellung_id_aus_beschreibung(beschreibung)
            if bestellung_id and _ist_resolved(con, bestellung_id):
                con.execute(
                    "UPDATE mentor_actions_log SET status='rejected', approved_by=?, "
                    "approved_at=CURRENT_TIMESTAMP WHERE id=?",
                    (_GRUND_RESOLVED, eintrag_id),
                )
                geschlossen_resolved += 1

        # Regel B erst NACH Regel A neu abfragen -- weniger Kandidaten, weniger Arbeit
        rest = con.execute(
            "SELECT id, beschreibung FROM mentor_actions_log WHERE status='pending_review' "
            "ORDER BY id DESC"
        ).fetchall()
        gesehen: set[str] = set()
        geschlossen_duplikat = 0
        for eintrag_id, beschreibung in rest:
            if beschreibung in gesehen:
                con.execute(
                    "UPDATE mentor_actions_log SET status='rejected', approved_by=?, "
                    "approved_at=CURRENT_TIMESTAMP WHERE id=?",
                    (_GRUND_DUPLIKAT, eintrag_id),
                )
                geschlossen_duplikat += 1
            else:
                gesehen.add(beschreibung)

        con.commit()
        verbleibend = con.execute(
            "SELECT COUNT(*) FROM mentor_actions_log WHERE status='pending_review'"
        ).fetchone()[0]
        return {
            "aktiv": True,
            "geprueft": len(rows),
            "geschlossen_resolved": geschlossen_resolved,
            "geschlossen_duplikat": geschlossen_duplikat,
            "verbleibend_offen": verbleibend,
        }
    finally:
        con.close()


if __name__ == "__main__":
    print(bereinigen_schritt())
