"""Fehlhistorie/Lern-Gedaechtnis (werft_memory, SQL) -- mit strukturellen Garantien
statt Konvention (Betreiber-Auftrag 2026-07-09, 5-Why-Analyse des alten Scope-Bugs).

Historischer Root Cause (verifiziert in der alten Datenbank/VDB eines Vorgaengerprojekts): FAIL-Eintraege
wurden mit memory_type='private' geschrieben, der Coder suchte aber scope='system'
-- Begruendungen waren vorhanden, aber fuer den Coder unsichtbar. "Jeder Versuch
war ein blinder Neuanfang." (Original-Zitat aus dem alten Lern-Gedaechtnis des Vorgaengerprojekts).

Zwei strukturelle Gegenmassnahmen, hart im Code statt nur als Text-Regel (v8.3 §6.4):
1. remember_system() erzwingt memory_type='system' fuer Fehlbegruendungen -- es gibt
   keinen Aufruf-Pfad, der versehentlich 'private' fuer Cross-Agent-Feedback waehlt.
2. remember_system() verifiziert nach dem Schreiben per eigenem SELECT, dass der
   Eintrag mit exakt der Abfrage auffindbar ist, die recall_system() nutzen wird
   (Ingest-Erfolg != Retrieval-Erfolg, eines Partnersystems M8-Lehre) -- wirft sonst einen Fehler,
   statt still eine unsichtbare Begruendung zu hinterlassen.
"""
import sqlite3
from pathlib import Path

from runtime_paths import WERFT_DB_PATH as DB_PATH

MIN_GRUND_LAENGE = 15  # eine Begruendung unter dieser Laenge gilt als leer/nichtssagend


class BegruendungZuKurzError(Exception):
    """Wird geworfen wenn eine Ablehnungs-Begruendung zu kurz ist, um dem Coder zu helfen."""


class RetrievalVerifikationFehlgeschlagen(Exception):
    """Wird geworfen wenn ein gerade geschriebener System-Memory-Eintrag sich nicht
    ueber den fuer ihn vorgesehenen Abfrageweg wiederfinden laesst."""


def remember_system(owner: str, target: str, content: str, tags: str = "") -> int:
    """Schreibt einen Cross-Agent-sichtbaren Lern-Eintrag (memory_type='system' PFLICHT).

    Fuer Ablehnungsgruende (Coder braucht das beim naechsten Versuch): target sollte
    der task_id oder Spezialisten-Name sein, gegen den spaeter gefiltert wird.
    """
    if len(content.strip()) < MIN_GRUND_LAENGE:
        raise BegruendungZuKurzError(
            f"Begruendung fuer '{target}' ist nur {len(content.strip())} Zeichen "
            f"(Minimum {MIN_GRUND_LAENGE}) -- das waere fuer den Coder nutzlos, "
            f"genau der historische Fehler. Analyst muss konkreter werden."
        )

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "INSERT INTO werft_memory (owner, memory_type, content, tags) "
            "VALUES (?, 'system', ?, ?)",
            (owner, f"[target:{target}] {content}", tags),
        )
        con.commit()
        entry_id = cur.lastrowid
    finally:
        con.close()

    # Strukturelle Verifikation: koennte recall_system() diesen Eintrag JETZT finden?
    gefunden = recall_system(target)
    if not any(f"[target:{target}]" in c for c in gefunden):
        raise RetrievalVerifikationFehlgeschlagen(
            f"Eintrag {entry_id} fuer '{target}' wurde geschrieben, ist aber ueber "
            f"recall_system('{target}') nicht auffindbar -- genau der alte Scope-Bug. "
            f"Nicht stillschweigend weitermachen, Ursache klaeren."
        )
    return entry_id


def recall_system(target: str, limit: int = 10) -> list[str]:
    """Liest Cross-Agent-sichtbare Lern-Eintraege fuer ein Ziel (task_id/Spezialist).

    Nur memory_type='system' -- das ist das gesamte Sichtbarkeits-Modell (siehe G-11).
    """
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT content FROM werft_memory "
            "WHERE memory_type='system' AND content LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (f"%[target:{target}]%", limit),
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def automatische_fehlversuche(target: str, limit: int = 20) -> int:
    """Zaehlt automatische Ablehnungen (owner IN ('analyst','coder')) fuer das
    Looper-Limit (max_retry_spec_failed) -- schliesst Mentor-Review-Eintraege
    (owner='mentor_review') bewusst aus. Fund 2026-07-15: ein Zaehler ueber ALLE
    System-Eintraege verwechselte manuelle Qualitaets-Reviews (nach bereits
    bestandener Taufe) mit echten automatischen Fehlschlaegen und archivierte
    einen Task faelschlich, obwohl er nur einer zusaetzlichen Pruefung unterzogen
    wurde, kein wiederholter automatischer Fehlschlag war.

    Fund 2026-07-19 (RouterAgent-Funktionstest, altes CLAUDE.md-Prinzip "max_retries
    gilt fuer ALLE Gate-Fehler" wiederentdeckt): coder.py bekam einen eigenen
    Ablehnungs-Pfad (owner='coder', bei syntaktisch kaputtem Code nach erschoepftem
    Retry) -- ohne ihn hier mitzuzaehlen haette dieser Fehlermodus das
    Looper-Limit umgangen und unbegrenzt requeuen koennen, exakt der Bug-Typ,
    den max_retry_spec_failed verhindern soll.

    Fund 2026-07-17 (Recycle-Pfad-Testlauf, beispielserie_teil2_router_agent): der
    Zaehler war eine ewige Lebenszeit-Summe ohne Reset-Punkt -- eine bereits
    erfolgreich ausgelieferte Bestellung, die spaeter ueber
    strategie.fordere_korrektur() fuer eine NEUE Korrekturrunde wieder geoeffnet
    wird, erbt dabei den vollen historischen Fehlversuch-Zaehler aus dem
    URSPRUENGLICHEN Bau. Bei 6 alten + 2 neuen Ablehnungen wurde so nach nur 2
    Versuchen in der neuen Runde faelschlich archiviert (max_retry=8 erreicht).
    Fix: zaehlt jetzt nur Analyst-Ablehnungen NACH dem letzten
    fordere_korrektur()-Aufruf fuer dasselbe Ziel (erkennbar an
    owner='mentor_review', tags='fordere_korrektur') -- jede Korrekturrunde
    bekommt so ihr eigenes, volles Retry-Budget. Ohne einen solchen Marker
    (erster Bauversuch) bleibt das Verhalten unveraendert (volle Historie)."""
    con = sqlite3.connect(DB_PATH)
    try:
        reset_marke = con.execute(
            "SELECT MAX(created_at) FROM werft_memory WHERE memory_type='system' "
            "AND owner='mentor_review' AND tags LIKE '%fordere_korrektur%' AND content LIKE ?",
            (f"%[target:{target}]%",),
        ).fetchone()[0]
        if reset_marke:
            rows = con.execute(
                "SELECT COUNT(*) FROM werft_memory WHERE memory_type='system' "
                "AND owner IN ('analyst','coder') AND content LIKE ? AND created_at > ?",
                (f"%[target:{target}]%", reset_marke),
            ).fetchone()
        else:
            rows = con.execute(
                "SELECT COUNT(*) FROM werft_memory WHERE memory_type='system' "
                "AND owner IN ('analyst','coder') AND content LIKE ? ",
                (f"%[target:{target}]%",),
            ).fetchone()
    finally:
        con.close()
    return min(rows[0], limit)


def _recall_system_mit_tags(target: str, limit: int = 10) -> list[tuple[str, str]]:
    """Wie recall_system(), aber inkl. tags-Spalte -- fuer fehlhistorie_text(),
    um 'bereits geloest' von 'noch offen' zu trennen (siehe dort)."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT content, tags FROM werft_memory "
            "WHERE memory_type='system' AND content LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (f"%[target:{target}]%", limit),
        ).fetchall()
    finally:
        con.close()
    return [(r[0], r[1] or "") for r in rows]


def fehlhistorie_text(target: str, limit: int = 10) -> str:
    """Formatiert die Fehlhistorie fuer den Coder-Prompt ({fehlhistorie}-Platzhalter,
    Neustart-Spec Abschnitt 7.3). Leerer String wenn keine frueheren Versuche.

    Fund 2026-07-20 (router_agent.py/theke_antwort.py, Budget-Spirale): bei
    KORREKTUR-Runden (bestehender_code + volle Fehlhistorie im selben Prompt) fraass
    eine lange Fehlhistorie (limit=10, jeder neue Fehlschlag ein weiterer Eintrag)
    das num_predict-Budget auf, bis nicht mal mehr die VOLLSTAENDIGE Datei-Antwort
    hineinpasste -- jeder Fehlschlag machte den naechsten wahrscheinlicher (Budget
    sinkt, Antwort wird abgeschnitten, neuer Fehlschlag-Eintrag, Budget sinkt weiter).
    coder.py uebergibt bei Korrektur-Runden jetzt ein kleineres limit -- die
    NEUESTEN Punkte sind ohnehin die relevantesten, aeltere sind meist bereits
    ueberholt oder Duplikate derselben Ursache.

    Fund 2026-07-15 (beispielserie_teil2_router_agent, Klassenname-Regression): bei
    mehreren gleichzeitig offenen Fehlhistorie-Punkten hat der Coder einen
    bereits behobenen Punkt bei der Korrektur eines ANDEREN Punkts versehentlich
    zurueckgedreht -- kein Datenverlust, sondern ein Gewichtungsproblem des
    Modells unter vielen undifferenzierten Hinweisen. Strukturelle Gegenmassnahme:
    Eintraege mit Tag 'bereits_geloest' werden getrennt und EXPLIZIT als
    "nicht mehr aendern" markiert, statt in derselben Liste wie offene Kritik
    zu stehen -- macht dem Coder den Unterschied maschinenlesbar klar, nicht nur
    implizit aus dem Text erschliessbar."""
    eintraege = _recall_system_mit_tags(target, limit=limit)
    if not eintraege:
        return ""
    geloest = [c for c, t in eintraege if "bereits_geloest" in t]
    offen = [c for c, t in eintraege if "bereits_geloest" not in t]

    text = ""
    if offen:
        punkte = "\n".join(f"- {e.split('] ', 1)[-1]}" for e in offen)
        text += f"NOCH ZU BEHEBEN (nicht wiederholen!):\n{punkte}\n"
    if geloest:
        punkte = "\n".join(f"- {e.split('] ', 1)[-1]}" for e in geloest)
        text += f"\nBEREITS KORREKT GELOEST -- NICHT MEHR AENDERN, auch nicht als Nebeneffekt einer anderen Korrektur:\n{punkte}\n"
    return text
