from __future__ import annotations

"""Debugger: 5. Kernrolle (ERWEITERUNG_5_ROLLE_DEBUGGER.md).

Prueft die PIPELINE selbst, nicht einzelne Gaps -- bewusst NICHT im kritischen
Pfad (Ausnahme: Vor-Merge-Statik, die haengt sich VOR die Analyst-Taufe).
Unabhaengig von Analyst/Summarizer/Coder/Router, damit das 04-11-Muster
(dieselbe Rolle attestiert sich selbst denselben Fehler, wirkungslos) nicht
wieder auftreten kann.
"""
import re
import sqlite3


def _code_pfad_aus_knowledge_links(knowledge_links: str) -> str | None:
    """Extrahiert CODE_PFAD robust -- Fund 2026-08-05 (Mentor, Crash-Loop
    ueber tausende Zyklen live erlebt): das alte Muster
    (knowledge_links.rsplit("CODE_PFAD:", 1)[-1].strip()) nahm ALLES bis zum
    Stringende als Pfad, inkl. jedem Text, der SPAETER an knowledge_links
    angehaengt wird (z.B. eine nachtraegliche MENTOR-NOTIZ beim Archivieren).
    Ergebnis: ein "Pfad" von mehreren tausend Zeichen -> OSError [Errno 36]
    File name too long, blockierte debugger/analyst dauerhaft (WHERE
    status='code_ready' traf denselben Task jeden Zyklus erneut). Fix: nur
    das direkt auf "CODE_PFAD:" folgende Pfad-Token nehmen (endet bei
    Leerzeichen/Zeilenumbruch/Pipe), letztes Vorkommen gewinnt (Retries
    haengen weitere CODE_PFAD: an -- der juengste Code-Stand zaehlt)."""
    treffer = re.findall(r"CODE_PFAD:([^\s|]+)", knowledge_links or "")
    return treffer[-1] if treffer else None
import subprocess
import sys
from pathlib import Path

from configloader import config
from coder import _METHODEN_NAME_MUSTER
from memory import remember_system, BegruendungZuKurzError
import tisch_client

from runtime_paths import WERFT_DB_PATH as DB_PATH

STUB_PATTERNS = [
    re.compile(r"^\s*pass\s*$", re.MULTILINE),
    re.compile(r"return\s+None\s*$", re.MULTILINE),
    re.compile(r"#\s*TODO", re.IGNORECASE),
    re.compile(r"raise\s+NotImplementedError"),
]

# Fund 2026-07-19 (Mentor, Stresstest nach Stromausfall): router_agent.py (230+
# Zeilen, "final geloest" gemeldet) UND kurator_lifecycle.py enthielten beide
# funktionierenden, laengeren Code, der aber semantisch eine Attrappe war --
# der Coder gestand das sogar woertlich im Kommentar/Docstring ein ("Simulierte
# Weiterleitung an Ziel-Agenten... In der Realitaet wuerde hier eine
# Kommunikation stattfinden", "DummySpecialist", "Dummy-Instanz erzeugen (hier
# als Beispiel)"). STUB_PATTERNS + die <8-Zeilen-Schwelle greifen hier NIE,
# weil genug "echter" Code drumherum steht -- die Heuristik ist fuer triviale
# leere Stubs gebaut, nicht fuer grosse, ueberzeugend aussehende Attrappen.
# Diese Muster sind ein SEPARATES, staerkeres Signal (der Coder gesteht die
# Simulation selbst ein) und werden deshalb OHNE die Zeilen-Schwelle geprueft.
SIMULATIONS_PATTERNS = [
    re.compile(r"[Ss]imuliert[e]?\s+(Antwort|Weiterleitung|Ergebnis|Aufruf|Bestätigung)"),
    # Fund 2026-07-31 (Mentor, task_bestellung_specgen_54d8eba45d): Coder-DNA-Boilerplate
    # enthaelt standardmaessig die Verneinung "Keine Dummy-Implementierungen -- echte Logik
    # wird ausgefuehrt" (PROGRAMMIERER-DNA-Docstring). Ungeschuetzt matchte das bei JEDER
    # Lieferung als vermeintliches Selbst-Gestaendnis und eskalierte nach 5 Wiederholungen
    # faelschlich an den Tisch/Mentor. Negative Lookbehinds nehmen NUR die Verneinung aus --
    # "DummySpecialist", "Dummy-Instanz erzeugen" (das eigentliche 2026-07-19-Fundmuster)
    # bleiben weiterhin erkannt.
    re.compile(r"(?<!Keine )(?<!keine )(?<!Kein )(?<!kein )\bDummy\w*\b"),
    re.compile(r"[Pp]latzhalter-?(Antwort|Instanz|Ergebnis|Objekt)"),
    re.compile(r"[Ii]n der Realität würde"),
]

# Fund 2026-07-27 (Mentor, Six-Sigma-Root-Cause-Analyse auf Betreiber-Auftrag,
# Tisch 22:42/22:53): wissens_generator.py (in einem Zielprojekt) hatte "think": false
# VERSCHACHTELT in "options" statt auf oberster Ebene des Ollama-API-Requests
# -- Ollama ignoriert das dann stillschweigend, qwen3 & Co. gehen in Thinking-
# Mode, verbrauchen num_predict komplett fuers Nachdenken, "response" bleibt
# leer oder bricht mitten im Satz ab. War aus einem VOELLIG ANDEREN Modul
# (doe_worker.py) bereits als Bug bekannt und in CLAUDE.md dokumentiert, aber
# nie als Werft-Guard eingebaut -- der Bug ueberlebte deshalb eine Code-
# Portierung unbemerkt. Live-verifiziert (curl-Vergleich: verschachtelt -> voellig
# leere Antwort, oberste Ebene -> Antwort kommt). Deterministisch statt LLM-
# Urteil, wie STUB_PATTERNS/SIMULATIONS_PATTERNS -- "think" ist ein Ollama-
# spezifischer Top-Level-Request-Parameter, kein legitimer options-Schluessel.
OLLAMA_THINK_MISPLACED_PATTERN = re.compile(
    r'"options"\s*:\s*\{[^{}]*["\']think["\']', re.MULTILINE
)

# Fund 2026-07-30 (Betreiber, Feuertaufe-Phase-3-Nachlese, Tisch 14:40): der
# selbstgebaute Nachbau hatte ein Ollama-Modell hardcodiert ("llama3"), das
# in der lokalen Ollama-Instanz gar nicht existiert (nur "llama3.2:3b") --
# live reproduziert (curl gegen /api/generate -> "model 'llama3' not found").
# Rein deterministisch pruefbar: Modellname aus dem Code extrahieren, gegen
# die tatsaechlich installierten Modelle (/api/tags) abgleichen.
OLLAMA_MODEL_MUSTER = re.compile(r'["\']model["\']\s*:\s*["\']([^"\']+)["\']')


def _verfuegbare_ollama_modelle() -> set[str] | None:
    """Fragt /api/tags ab. Gibt None zurueck (statt leerem Set) wenn Ollama
    nicht erreichbar war -- ein leeres Set wuerde JEDES gefundene Modell
    faelschlich als nicht-existent blockieren, nur weil Ollama gerade kurz
    nicht antwortete."""
    import requests
    host = config.get("ollama.host", "http://localhost:11434")
    try:
        r = requests.get(
            f"{host.rstrip('/')}/api/tags",
            timeout=config.get("timeouts.debugger_host_sec"),
        )
        r.raise_for_status()
        return {m["name"] for m in r.json().get("models", [])}
    except Exception:
        return None


# Fund 2026-07-29 (Betreiber, Vorfall in einem Zielprojekt): STUB_PATTERNS/
# SIMULATIONS_PATTERNS/OLLAMA_THINK_MISPLACED_PATTERN pruefen generische
# Code-Qualitaet, aber nichts prueft gegen PROJEKTSPEZIFISCHE Vorgaben (z.B.
# "dieses Zielprojekt darf einen bestimmten, dort verbotenen Code-Bereich
# nicht enthalten"). Ich (Mentor) habe genau das in einer Bestellung selbst
# verletzt, unbemerkt bis Betreiber es live im Gespraech ueber das Zielprojekt
# bemerkte. `projekt_verbotene_muster` ist die strukturelle Schranke dagegen
# -- pro ziel_ordner eine Liste verbotener Regex-Muster mit Begruendung,
# additiv erweiterbar wie coder_dna_regeln, aber NEGATIV (verbietet) statt
# positiv (empfiehlt).
def _sicherstellen_verbotene_muster_tabelle(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS projekt_verbotene_muster (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ziel_ordner TEXT NOT NULL,
            muster TEXT NOT NULL,
            grund TEXT NOT NULL,
            erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    # Beispiel-Eintrag (illustriert den Mechanismus, kein Pflicht-Verbot):
    # ein Zielprojekt kann hier beliebige domaenenspezifische Verbote
    # hinterlegen, z.B. ein noch nicht freigegebenes internes Fachvokabular.
    vorhanden = con.execute(
        "SELECT 1 FROM projekt_verbotene_muster WHERE ziel_ordner=? AND muster=?",
        ("/pfad/zum/zielprojekt", r"(?i)\bFIXME_LATER\b|_ist_platzhalter_frage"),
    ).fetchone()
    if not vorhanden:
        con.execute(
            "INSERT INTO projekt_verbotene_muster (ziel_ordner, muster, grund) VALUES (?, ?, ?)",
            (
                "/pfad/zum/zielprojekt",
                r"(?i)\bFIXME_LATER\b|_ist_platzhalter_frage",
                "Beispiel: dieses Zielprojekt verbietet unfertige Platzhalter-Marker "
                "im ausgelieferten Code, unabhaengig davon, ob generische STUB-"
                "Erkennung sie ohnehin schon findet.",
            ),
        )


def _verbotene_muster_treffer(con: sqlite3.Connection, task_id: str, code: str) -> list[tuple[str, str]]:
    """Findet den ziel_ordner der Bestellung zu diesem Task (Prefix-Match auf
    task_bestellung_<bestellung_id>) und prueft den Code gegen alle dort
    hinterlegten verbotenen Muster. Gibt Liste von (muster, grund) zurueck."""
    ziel_rows = con.execute(
        "SELECT bestellung_id, ziel_ordner FROM bestellungen WHERE ziel_ordner IS NOT NULL"
    ).fetchall()
    ziel_ordner = None
    for bestellung_id, zo in ziel_rows:
        if task_id.startswith(f"task_bestellung_{bestellung_id}"):
            ziel_ordner = (zo or "").rstrip("/")
            break
    if not ziel_ordner:
        return []
    regeln = con.execute(
        "SELECT muster, grund FROM projekt_verbotene_muster WHERE ziel_ordner=?",
        (ziel_ordner,),
    ).fetchall()
    treffer = []
    for muster, grund in regeln:
        if re.search(muster, code):
            treffer.append((muster, grund))
    return treffer


def _blockiere_oder_eskaliere(con: sqlite3.Connection, task_id: str, quelle: str,
                               befund_key: str, beschreibung: str) -> None:
    """Gemeinsame Block/Eskalations-Logik fuer vor_merge_statik() und
    ausfuehrungs_check() -- war vor 2026-07-15 in vor_merge_statik() dupliziert,
    jetzt einmal fuer beide Pruefarten. Siehe Kommentare in der urspruenglichen
    Version zu den zwei live gefundenen Bugs (Fehlhistorie-Ruecklauf, SQLite-Lock,
    Eskalation ohne Konsument)."""
    wiederholungen = _melde_befund(con, quelle=quelle, befund_key=befund_key, beschreibung=beschreibung)
    con.commit()  # eigene Transaktion abschliessen VOR remember_system() (separate Connection)
    schwelle = int(config.get("debugger.eskalations_schwelle", 3))
    target_name = task_id.replace("task_", "")

    if wiederholungen >= schwelle:
        # Fund 2026-07-30 (live in Produktion, ~23:40, waehrend specgen_54d8eba45d
        # lief): meine urspruengliche Annahme "Task verlaesst danach den
        # code_ready-Scanbereich, kein Spam-Risiko" war FALSCH -- irgendein
        # Retry-Mechanismus setzt den Task offenbar wieder auf code_ready
        # zurueck, vor_merge_statik() findet ihn erneut, wiederholungen zaehlt
        # weiter hoch (3, 4, ...), und OHNE Guard haette JEDE dieser
        # Wiederholungen eine neue Tisch-Nachricht ausgeloest -- exakt das
        # Spam-Muster, das ich fuer formular_vorschlag schon vermieden hatte,
        # hier aber uebersehen. Guard: nur benachrichtigen wenn der Task VOR
        # diesem Aufruf noch NICHT auf review_pending stand.
        war_bereits_review_pending = con.execute(
            "SELECT 1 FROM task_pipeline WHERE task_id=? AND status='review_pending'",
            (task_id,),
        ).fetchone() is not None
        con.execute("UPDATE task_pipeline SET status='review_pending' WHERE task_id=?", (task_id,))
        if not war_bereits_review_pending:
            tisch_client.benachrichtigen(
                text=(
                    f"{task_id} nach {wiederholungen} Wiederholungen auf "
                    f"review_pending eskaliert ({quelle}): {beschreibung}"
                ),
                von="WERFT", thema="Task braucht Review",
            )
    else:
        try:
            remember_system(owner="debugger", target=target_name, content=beschreibung,
                             tags=f"{quelle},debugger_befund")
        except BegruendungZuKurzError:
            pass
        con.execute("UPDATE task_pipeline SET status='spec' WHERE task_id=?", (task_id,))


def vor_merge_statik() -> int:
    """2.1: Scannt jeden Task mit status='code_ready' auf Platzhalter-Code,
    BEVOR der Analyst ihn prueft. Fund -> Task zurueck auf 'spec' (Coder muss
    neu ran), Befund in debugger_findings. Gibt Anzahl geblockter Tasks zurueck."""
    con = sqlite3.connect(DB_PATH)
    try:
        _sicherstellen_verbotene_muster_tabelle(con)
        rows = con.execute(
            "SELECT task_id, knowledge_links FROM task_pipeline WHERE status='code_ready'"
        ).fetchall()
        geblockt = 0
        for task_id, knowledge_links in rows:
            code_pfad = _code_pfad_aus_knowledge_links(knowledge_links)
            if not code_pfad:
                continue
            datei = Path(__file__).parent / code_pfad
            if not datei.exists():
                continue
            code = datei.read_text(encoding="utf-8", errors="ignore")

            verbotene_treffer = _verbotene_muster_treffer(con, task_id, code)
            if verbotene_treffer:
                muster_liste = ", ".join(m for m, _ in verbotene_treffer)
                gruende = " | ".join(g for _, g in verbotene_treffer)
                beschreibung = (
                    f"{task_id}: projektspezifisch verbotenes Muster gefunden "
                    f"({muster_liste}). Grund: {gruende}"
                )
                _blockiere_oder_eskaliere(
                    con, task_id, quelle="vor_merge_statik",
                    befund_key=f"verbotenes_muster:{task_id}", beschreibung=beschreibung,
                )
                geblockt += 1
                continue

            # Nur pruefen wenn der Code ueberhaupt wie ein echter Ollama-Aufruf
            # aussieht -- verhindert Fehlalarme bei zufaelligen "model"-Schluesseln
            # in voellig unrelated Dicts.
            if "ollama" in code.lower() or "11434" in code or "/api/generate" in code or "/api/chat" in code:
                genannte_modelle = set(OLLAMA_MODEL_MUSTER.findall(code))
                if genannte_modelle:
                    verfuegbar = _verfuegbare_ollama_modelle()
                    if verfuegbar is not None:
                        verfuegbare_basisnamen = {m.split(":")[0] for m in verfuegbar}
                        fehlende = {
                            m for m in genannte_modelle
                            if m not in verfuegbar and m.split(":")[0] not in verfuegbare_basisnamen
                        }
                        if fehlende:
                            beschreibung = (
                                f"{task_id}: referenziert Ollama-Modell(e) {sorted(fehlende)}, "
                                f"die in der lokalen Ollama-Instanz nicht installiert sind "
                                f"(verfuegbar: {sorted(verfuegbar)}). Der Aufruf wuerde zur "
                                f"Laufzeit mit 'model not found' fehlschlagen."
                            )
                            _blockiere_oder_eskaliere(
                                con, task_id, quelle="vor_merge_statik",
                                befund_key=f"ollama_modell_fehlt:{task_id}", beschreibung=beschreibung,
                            )
                            geblockt += 1
                            continue

            treffer = [p.pattern for p in STUB_PATTERNS if p.search(code)]
            # Eine einzelne 'pass'-Zeile in einer Exception-Klasse ist kein Stub --
            # nur werten wenn UEBERHAUPT keine echte Logik (mehr Stub- als Code-Zeilen-Signal).
            substantielle_zeilen = [
                z for z in code.splitlines()
                if z.strip() and not z.strip().startswith("#") and z.strip() not in ("pass",)
            ]
            ollama_think_treffer = bool(OLLAMA_THINK_MISPLACED_PATTERN.search(code))
            if ollama_think_treffer:
                beschreibung = (
                    f"{task_id}: 'think' verschachtelt in 'options' bei einem Ollama-API-"
                    f"Aufruf gefunden -- Ollama ignoriert das stillschweigend, Reasoning-"
                    f"Modelle (qwen3 u.a.) gehen dadurch in Thinking-Mode und verbrauchen "
                    f"num_predict komplett ohne brauchbare Antwort. 'think' muss auf "
                    f"oberster Ebene des JSON-Requests stehen, nicht in 'options'."
                )
                _blockiere_oder_eskaliere(
                    con, task_id, quelle="vor_merge_statik",
                    befund_key=f"ollama_think_misplaced:{task_id}", beschreibung=beschreibung,
                )
                geblockt += 1
                bereits_gelernt = con.execute(
                    "SELECT 1 FROM coder_dna_regeln WHERE ausgeloest_von=?",
                    ("ollama_think_misplaced",),
                ).fetchone()
                if not bereits_gelernt:
                    regel_text = (
                        "OLLAMA-API-AUFRUFE: 'think' (Reasoning ein/aus) gehoert auf die "
                        "OBERSTE Ebene des JSON-Requests (z.B. {'model':..., 'think': False, "
                        "'options': {...}}), NIEMALS in 'options' -- dort wird es von Ollama "
                        "stillschweigend ignoriert, Reasoning-Modelle verbrauchen dann "
                        "num_predict komplett fuers Nachdenken statt fuer die Antwort "
                        "(autonom vom Debugger gelernt, live reproduziert 2026-07-27)."
                    )
                    con.execute(
                        "INSERT INTO coder_dna_regeln (regel_text, ausgeloest_von) VALUES (?, ?)",
                        (regel_text, "ollama_think_misplaced"),
                    )
                    con.execute(
                        "INSERT INTO mentor_actions_log (action_type, beschreibung, status) "
                        "VALUES (?, ?, 'auto_angewendet')",
                        ("dna_regel_autonom",
                         f"Debugger hat autonom eine neue Coder-DNA-Regel gelernt (Ollama "
                         f"think-Platzierung, ausgeloest durch {task_id}) und in "
                         f"coder_dna_regeln eingetragen -- wirkt ab dem naechsten "
                         f"Coder-Aufruf, keine Mentor-Freigabe noetig."),
                    )
                continue

            simulations_treffer = [p.pattern for p in SIMULATIONS_PATTERNS if p.search(code)]
            if simulations_treffer:
                beschreibung = (
                    f"{task_id}: Selbst-eingestandene Simulation/Attrappe gefunden "
                    f"({simulations_treffer}) -- unabhaengig von der Code-Laenge blockiert, "
                    f"da eine grosse ueberzeugende Attrappe genauso ein Platzhalter ist wie "
                    f"ein leerer Stub."
                )
                _blockiere_oder_eskaliere(
                    con, task_id, quelle="vor_merge_statik",
                    befund_key=f"simulation_code:{task_id}", beschreibung=beschreibung,
                )
                geblockt += 1
                # Fund 2026-07-20 (Betreiber: "wenn ein Mentor danebenstehen muss und sie immer
                # ermahnen, ist das keine Autonomie!"): ein Freigabe-Gate-VORSCHLAG (voriger
                # Stand) loest das Kernproblem nicht -- die Lehre erreicht die DNA erst, wenn
                # ein Mensch sie manuell abnickt. Hier bewusst OHNE Gate: die Regel ist rein
                # ADDITIV (verbietet nur etwas, ermoeglicht nie schlechteren Code) und beruht
                # auf deterministischer Regex-Erkennung, nicht auf einem LLM-Urteil -- also
                # kein Hallucinations-Risiko in der Erkennung selbst. Anders als bei
                # Schema-Aenderungen/neuen Rollen/Modellwechseln (die WEITERHIN ein Gate
                # brauchen, weil sie echte Fehlentscheidungen ermoeglichen) ist das Risiko
                # einer autonom hinzugefuegten Verbots-Regel strukturell begrenzt: schlimmstenfalls
                # zu vorsichtig, nie destruktiv. Pro NEUEM Muster (nicht pro Task) EINMAL in
                # coder_dna_regeln eintragen -- coder.py liest diese Tabelle bei JEDEM Prompt-Bau
                # (_vollstaendige_dna()) automatisch mit, keine weitere Aktion noetig.
                for muster in simulations_treffer:
                    bereits_gelernt = con.execute(
                        "SELECT 1 FROM coder_dna_regeln WHERE ausgeloest_von=?", (muster,),
                    ).fetchone()
                    if not bereits_gelernt:
                        regel_text = (
                            f"KEINE SELBST-EINGESTANDENEN SIMULATIONEN/ATTRAPPEN (autonom vom "
                            f"Debugger gelernt, Muster {muster!r}, ausgeloest durch {task_id}): "
                            f"Code, der eine Anforderung nur simuliert/als Dummy/Platzhalter erfuellt "
                            f"statt sie echt umzusetzen, ist keine Implementierung. Wenn eine echte "
                            f"Umsetzung im Rahmen der Aufgabe nicht moeglich ist, ein Fehler-Dict "
                            f"zurueckgeben statt einen erfundenen Erfolg vorzutaeuschen."
                        )
                        con.execute(
                            "INSERT INTO coder_dna_regeln (regel_text, ausgeloest_von) VALUES (?, ?)",
                            (regel_text, muster),
                        )
                        # Transparenz-Pflicht (Wahrheits-Kodex): autonome Aenderung wird geloggt,
                        # nicht versteckt -- aber status='auto_angewendet', KEIN pending_review,
                        # das waere wieder das alte Warten-auf-Mentor-Muster.
                        con.execute(
                            "INSERT INTO mentor_actions_log (action_type, beschreibung, status) "
                            "VALUES (?, ?, 'auto_angewendet')",
                            ("dna_regel_autonom",
                             f"Debugger hat autonom eine neue Coder-DNA-Regel gelernt (Muster "
                             f"{muster!r}, ausgeloest durch {task_id}) und in coder_dna_regeln "
                             f"eingetragen -- wirkt ab dem naechsten Coder-Aufruf, keine "
                             f"Mentor-Freigabe noetig (rein additive, deterministisch begruendete "
                             f"Regel)."),
                        )
            elif treffer and len(substantielle_zeilen) < 8:
                beschreibung = (
                    f"{task_id}: Platzhalter-Muster gefunden ({treffer}), "
                    f"nur {len(substantielle_zeilen)} substantielle Zeilen."
                )
                _blockiere_oder_eskaliere(
                    con, task_id, quelle="vor_merge_statik",
                    befund_key=f"stub_code:{task_id}", beschreibung=beschreibung,
                )
                geblockt += 1
        con.commit()
        return geblockt
    finally:
        con.close()


def _promote_call_graph_kanten(con: sqlite3.Connection, lookup_name: str) -> int:
    """Touchpoint 2, Teil A der call_graph_kanten-Infrastruktur (Betreiber-Auftrag
    ueber Mentor, 2026-08-26): bei bestandenem ausfuehrungs_check alle
    'unsicher'-Kanten dieser Datei (von coder.py::_aktualisiere_call_graph
    geschrieben) auf 'in_pruefung' hochstufen. Betrifft NUR Zeilen mit
    status='unsicher' -- eine bereits von analyst.py auf 'ist' gesetzte Kante
    wird dadurch strukturell nie zurueckgestuft (WHERE-Klausel schliesst sie
    aus), unabhaengig davon wie oft der Ausfuehrungs-Check erneut laeuft.
    Gibt die Anzahl hochgestufter Kanten zurueck.

    Fund 2026-08-26 (Betreiber-Nachfrage ueber Mentor, vor dem ersten Live-
    Neustart): try/except sqlite3.Error wie ressourcen_status.py::
    protokolliere_check() -- diese reine Beobachtungs-Aktualisierung darf
    NIE den umgebenden ausfuehrungs_check()-Schleifendurchlauf abbrechen und
    damit die Verarbeitung der UEBRIGEN Tasks in diesem Zyklus verhindern."""
    try:
        cur = con.execute(
            "UPDATE call_graph_kanten SET status='in_pruefung', aktualisiert_am=datetime('now') "
            "WHERE von_datei=? AND status='unsicher'",
            (lookup_name,),
        )
        return cur.rowcount
    except sqlite3.Error:
        return 0


def _melde_unerreichte_private_methoden(con: sqlite3.Connection, lookup_name: str, code: str) -> list[str]:
    """Touchpoint 2, Teil B: private Methoden (fuehrender Unterstrich, keine
    Dunder) einer Datei, die in call_graph_kanten an KEINER Stelle als
    nach_methode auftauchen -- also innerhalb der Datei nie ueber self.X()
    aufgerufen werden. Schreibt dafuer einen NEGATIV-Befund in die bestehende
    debugger_findings-Tabelle (ueber _melde_befund, gleicher Mechanismus wie
    alle anderen Debugger-Funde -- KEINE neue call_graph_kanten-Zeile, es gibt
    ja keine positive Kante zu melden).

    Bewusst nur ein HINWEIS, kein Urteil: eine Methode kann z.B. dynamisch
    per getattr() aufgerufen werden, das sieht dieser rein statische Check
    nicht. Oeffentliche Methoden (kein fuehrender Unterstrich) werden NICHT
    geprueft -- die sind laut 'beantworte'-Vertrag von aussen (Router)
    aufrufbar, ein fehlender interner Aufruf ist dort erwartungsgemaess und
    kein Befund. Gibt die Liste der gemeldeten Methodennamen zurueck.

    Fund 2026-08-26 (Betreiber-Nachfrage ueber Mentor): try/except sqlite3.Error
    um die Datenbankzugriffe, aus demselben Grund wie bei
    _promote_call_graph_kanten() -- diese Hinweis-Meldung darf den
    Ausfuehrungs-Check nie zum Absturz bringen."""
    alle_methoden = set(_METHODEN_NAME_MUSTER.findall(code))
    private_methoden = {m for m in alle_methoden if m.startswith("_") and not m.startswith("__")}
    if not private_methoden:
        return []

    try:
        bekannte_ziele = set(
            row[0] for row in con.execute(
                "SELECT DISTINCT nach_methode FROM call_graph_kanten WHERE nach_datei=?",
                (lookup_name,),
            ).fetchall()
        )
        unerreicht = sorted(private_methoden - bekannte_ziele)
        for methode in unerreicht:
            _melde_befund(
                con, quelle="ausfuehrungs_check",
                befund_key=f"kein_aufrufer_gefunden:{lookup_name}:{methode}",
                beschreibung=(
                    f"{lookup_name}: private Methode '{methode}' wird an keiner "
                    f"bekannten Stelle innerhalb der Datei aufgerufen (Hinweis, "
                    f"kein Urteil -- moeglicher toter Code, koennte aber auch "
                    f"dynamisch aufgerufen werden, z.B. per getattr())."
                ),
            )
    except sqlite3.Error:
        return []
    return unerreicht


def ausfuehrungs_check() -> int:
    """2.1b (Betreiber-Nachfrage 2026-07-15, "ist Crash-Testen nicht Aufgabe des
    Debuggers?"): Vor-Merge-Statik ist rein statisch (Regex) und haette die
    beiden echten Bugs in beispielserie_teil3 (nicht existierende Methode,
    fehlender @contextlib.contextmanager) NIE gefunden -- Code, der bei der
    ersten Zeile crasht, kann trotzdem als 'kein Stub' durchgehen. Weder
    Debugger (statisch) noch Analyst (liest nur, fuehrt nie aus) haetten das
    gefangen. Altes System hatte dafuer einen eigenen 'Simulator' (PIPELINE_
    ABLAUF.md, Status code_ready: 'Fuehrt gen_*.py aus, prueft auf ImportError/
    SyntaxError/Exception'). Hier als Erweiterung von Vor-Merge-Statik gebaut
    (gleiche Pipeline-Stelle, gleicher struktureller statt inhaltlicher
    Charakter) statt einer 7. Rolle.

    Fuehrt das Modul in einem ISOLIERTEN Subprozess aus (Timeout, kein Schaden
    an diesem Prozess bei Hang/Crash) -- Import, plus Instanziierung der
    Hauptklasse OHNE Argumente, falls aus strategie_planung bekannt (sonst nur
    Import-Check, wie beim alten Simulator). Gibt Anzahl geblockter Tasks zurueck."""
    timeout_s = int(config.get("debugger.ausfuehrungs_timeout_sekunden", 15))
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT task_id, knowledge_links FROM task_pipeline WHERE status='code_ready'"
        ).fetchall()
        geblockt = 0
        for task_id, knowledge_links in rows:
            code_pfad = _code_pfad_aus_knowledge_links(knowledge_links)
            if not code_pfad:
                continue
            interne_datei = Path(__file__).parent / code_pfad
            if not interne_datei.exists():
                continue

            # Fund 2026-07-15 (beispielserie_teil4): der geplante_klasse-Lookup
            # nutzte den INTERNEN Dateinamen (bestellung_<id>.py) als Schluessel,
            # aber strategie_planung.kanonischer_name ist der ZIEL-Name (z.B.
            # spezialist_interface.py) -- der Lookup griff dadurch nie.
            #
            # ZWEITER, SCHWERWIEGENDERER Fund 2026-07-15 (beispielserie_teil5,
            # 7+ falsche Blockaden in Folge): der urspruengliche "Fix" hat nicht nur
            # den LOOKUP-KEY korrigiert, sondern faelschlich auch die GETESTETE DATEI
            # auf den Ziel-Ordner umgestellt. Bei einer Bestellung, die schon EINMAL
            # ausgeliefert wurde und jetzt in Korrektur ist, liegt im Ziel-Ordner die
            # ALTE, bereits ausgelieferte Version -- der frische Coder-Kandidat liegt
            # nur in generiert/. Der Check testete dadurch wiederholt eine STALE Datei
            # und blockierte mehrfach echte, bereits korrigierte Versuche.
            # Korrekt: IMMER die frische interne Datei ausfuehren, NUR den
            # kanonischen Namen fuer den Lookup-Key verwenden.
            datei = interne_datei
            lookup_name = interne_datei.name
            ziel_ordner = None
            bestellung_id = task_id.replace("task_bestellung_", "") if task_id.startswith("task_bestellung_") else None
            spec_inhalt = None
            if bestellung_id:
                row = con.execute(
                    "SELECT kanonischer_name, ziel_ordner, spec_inhalt FROM bestellungen WHERE bestellung_id=?",
                    (bestellung_id,),
                ).fetchone()
                if row and row[0]:
                    lookup_name = row[0]
                if row and row[1]:
                    ziel_ordner = row[1]
                if row and row[2]:
                    spec_inhalt = row[2]

            # Fund 2026-07-21 (Mentor, Spec-Synthese-Testszenario): dieser Check
            # baut einen Python-`import`-Testharness und fuehrt ihn ueber den
            # Python-Interpreter aus -- fuer Bestellungen, deren Zielprodukt kein
            # Python-Modul ist (kanonischer_name endet nicht auf .py, z.B. ein
            # konsolidiertes Markdown-Spec-Dokument), ist das strukturell sinnlos
            # (kein gueltiges Python, "import x.md" schlaegt immer fehl) und wuerde
            # jede nicht-Python-Bestellung faelschlich blockieren. Nicht-Python-Ziele
            # ueberspringen den Ausfuehrungs-Check komplett -- die inhaltliche
            # Pruefung uebernimmt stattdessen der Analyst-Taufe-Schritt (LLM-Urteil
            # gegen die Spec, funktioniert unabhaengig vom Zieldateityp).
            if lookup_name and not lookup_name.endswith(".py"):
                continue

            modul_name = datei.stem
            geplante_klasse = con.execute(
                "SELECT geplante_klasse FROM strategie_planung WHERE kanonischer_name=? "
                "ORDER BY id DESC LIMIT 1",
                (lookup_name,),
            ).fetchone()
            # Fund 2026-07-15 (beispielserie_teil5, gpt-oss-Vergleich): der Coder
            # konsolidiert manchmal die geplante Fachklasse und den Bestellungs-
            # Wrapper in EINE Klasse (target_name_capitalized), statt beide getrennt
            # zu halten -- geplante_klasse existiert dann im Modul gar nicht. Blinder
            # Versuch nur mit geplante_klasse haette das faelschlich als Crash
            # gewertet. Fallback: beide moeglichen Namen probieren, erst dann werten.
            # WICHTIG: exakt dieselbe Berechnung wie coder.py (nur "task_" strippen,
            # NICHT "task_bestellung_" -- sonst stimmt der Name nicht mit der
            # tatsaechlich generierten Klasse ueberein, eigener Tippfehler eben
            # gefunden: "BeispielserieTeil5" statt "BestellungBeispielserieTeil5").
            target_name_capitalized = "".join(
                w.capitalize() for w in task_id.replace("task_", "").split("_")
            )
            # Fund 2026-07-15 (beispielserie_teil7): der Kandidat importiert oft
            # eine bereits gelieferte Abhaengigkeit (z.B. spezialist_interface.py,
            # Teil 4) unter ihrem kanonischen Namen -- die liegt im ZIEL-Ordner
            # (~/zielprojekt/), nicht in generiert/. Ohne den Ziel-Ordner im sys.path
            # schlaegt der Import fehl, obwohl der Code selbst korrekt ist.
            #
            # KRITISCHER Fund 2026-07-16 (Betreiber, "warum bleibt der Fehler nach
            # Korrektur bestehen?"): sys.path.insert(0, ...) fuegt IMMER vorne ein --
            # der ZWEITE insert(0, ziel_ordner) landete dadurch VOR dem ersten
            # (datei.parent), sodass bei einer Korrektur-Bestellung (Kandidat UND
            # eine gleichnamige, aeltere/kaputte Datei im ziel_ordner vorhanden)
            # IMMER die STALE Ziel-Ordner-Version importiert wurde, nie die frische
            # aus generiert/ -- exakt der Bug, den der Fund von Teil 5 (2026-07-15,
            # "immer die frische interne Datei ausfuehren") beheben sollte, hier aber
            # durch die insert-Reihenfolge unbemerkt wieder eingeschlichen war.
            # Korrekt: ziel_ordner ZUERST einfuegen, datei.parent DANACH -- damit
            # datei.parent (die frische Kandidaten-Datei) in sys.path an erster
            # Stelle steht und bei einem Namenskonflikt gewinnt.
            # Fund 2026-07-19 (Mentor, Mentor-Stresstest nach Stromausfall): reine
            # Import+Instanziierung-ohne-Argumente-Pruefung faengt Bugs, die erst beim
            # ECHTEN Aufruf mit echten Argumenten auftreten, NICHT -- live gefunden bei
            # pattern_analyse.py (falscher JSON-Feldname im Ollama-Aufruf, HTTP 200 statt
            # Fehler) und theke_komponente.py (Aufruf einer nicht existierenden Klassen-
            # methode). Beide bestanden den reinen Instanziierungs-Check anstandslos.
            # Wenn die Bestellung ein maschinenpruefbares Abnahmekriterium im spec_inhalt
            # hat (Markdown-Codeblock nach "Abnahmekriterium"), das ECHT ausfuehren --
            # das ist derselbe Test, den ein Mentor manuell machen wuerde, nur automatisch
            # vor jedem Merge. Ohne ein solches Kriterium: Fallback auf die alte, schwaechere
            # reine Instanziierungspruefung (besser als nichts).
            # Fund 2026-07-28 (Mentor, live entdeckt beim Testen des neuen
            # Integrations-Guards direkt darunter): diese Regex verlangte den
            # ```python-Codeblock ZWINGEND auf der Zeile DIREKT nach der
            # "Abnahmekriterium"-Ueberschrift -- die uebliche, auch von
            # spec_formular.py's STANDARD_VORLAGE vorgeschriebene Konvention
            # ("## Abnahmekriterium (Pflicht)\nAufruf:\n```python\n...") hat
            # aber eine Einleitungszeile dazwischen und wurde dadurch NIE
            # erkannt -- lief die ganze Session unbemerkt mit, weil der
            # schwaechere Fallback (reine Instanziierung) das kaschierte, bis
            # der neue Integrations-Guard (siehe unten) es sichtbar machte.
            # Jetzt toleriert: beliebiger Text (auch mehrzeilig) zwischen der
            # Ueberschrift und dem ersten ```python-Codeblock.
            # Fund 2026-08-29 (Mentor, specgen_8936d4fc7b, zweite Stelle mit
            # demselben Fehler wie der Zaehler weiter unten): "Abnahmekriterium"
            # ohne Zeilenanfang-Anker matchte auch einen EINGERUECKTEN Python-
            # Kommentar ("    # Abnahmekriterium-Check") innerhalb eines ALT/NEU-
            # Codebeispiels in der Spec-Prosa -- der nachfolgende ```python-Fund
            # war dann ein voellig falscher, zufaelliger Codeblock. Ein echtes
            # Markdown-Ueberschrift-'#' steht immer am Zeilenanfang (Spalte 0),
            # ein Kommentar innerhalb eines eingerueckten Codeblocks nie -- daher
            # reicht ein Zeilenanfang-Anker (^) hier aus, ohne Codebloecke separat
            # entfernen zu muessen.
            abnahme_match = re.search(
                r"^#+\s*Abnahmekriterium.*?```python\n(.*?)```",
                spec_inhalt or "", re.DOTALL | re.MULTILINE,
            )

            # Fund 2026-08-29 (Mentor, zweites bestaetigtes Vorkommen nach
            # specgen_7e0a2f7065 vom 2026-08-26, hier bei
            # beispiel_synthese_truncation_fix_20260805): bei MEHREREN
            # "## Abnahmekriterium"-Ueberschriften in derselben Spec (z.B. durch
            # versehentlich hineingerutschten Text aus einer anderen Bestellung)
            # ist obige Regex strukturell blind dafuer, WELCHE Ueberschrift zum
            # gefundenen Codeblock gehoert -- der laessige `.*?` ueberspringt
            # klaglos eine dazwischenliegende zweite Ueberschrift und liefert
            # dann den naechstbesten ```python-Block, der thematisch komplett
            # fremd sein kann. Statt weiter zu raten: bei >1 Vorkommen den Merge
            # blockieren und den Mentor eskalieren (dasselbe Verfahren wie beim
            # fehlenden Abnahmekriterium bei Abhaengigkeiten weiter unten),
            # statt weiterhin still das falsche Kriterium zu pruefen.
            # Fund 2026-08-29 (Mentor, specgen_8936d4fc7b): die Zaehlung traf
            # faelschlich auch auf Python-Kommentare INNERHALB von ```python-
            # Codebeispielen zu (z.B. "# Abnahmekriterium-Check" als Testkommentar
            # im ALT/NEU-Codebeispiel selbst) -- Selbstreferenz-Fehlmatch, gleiche
            # Fehlerklasse wie feedback_spec_ersetzungen_selbstreferenz_2026-08-26.
            # Fix: zuerst alle Codebloecke entfernen, NUR im verbleibenden Prosa-
            # Text nach echten Markdown-Ueberschriften suchen.
            spec_ohne_codebloecke = re.sub(
                r"```.*?```", "", spec_inhalt or "", flags=re.DOTALL
            )
            abnahmekriterium_ueberschriften = len(
                re.findall(r"^#+\s*Abnahmekriterium", spec_ohne_codebloecke, re.IGNORECASE | re.MULTILINE)
            )
            if abnahmekriterium_ueberschriften > 1:
                beschreibung = (
                    f"{task_id}: Spec enthaelt {abnahmekriterium_ueberschriften} "
                    f"'## Abnahmekriterium'-Ueberschriften -- die Extraktion kann nicht "
                    f"zuverlaessig bestimmen, welcher ```python-Codeblock zur relevanten "
                    f"Ueberschrift gehoert (Fund 2026-08-29, gleiche Fehlerklasse wie "
                    f"specgen_7e0a2f7065 vom 2026-08-26). Spec-Text muss bereinigt werden "
                    f"(vermutlich versehentlich hineingerutschter Text aus einer anderen "
                    f"Bestellung), bevor automatisch weitergebaut wird."
                )
                _blockiere_oder_eskaliere(
                    con, task_id, quelle="ausfuehrungs_check",
                    befund_key=f"mehrdeutiges_abnahmekriterium:{task_id}",
                    beschreibung=beschreibung,
                )
                geblockt += 1
                continue

            # Fund 2026-07-28 (Betreiber-Auftrag nach Six-Sigma-Analyse, Tisch 22:53/
            # 13:08): theke_komponente.py (07-19) orchestrierte VIER bereits
            # gelieferte Module (theke_konversation/rag/router/antwort), hatte
            # aber KEIN Abnahmekriterium -- der Check fiel auf die schwache reine
            # Instanziierungspruefung zurueck, die behandle_frage() nie aufrief
            # und den spaeteren Verschachtelungs-Bug strukturell nicht faengt
            # haette koennen. Root Cause: eine Bestellung mit ECHTEN
            # Abhaengigkeiten zu anderen Modulen (strategie_planung.
            # abhaengigkeiten nicht leer) braucht ZWINGEND einen echten
            # Integrationstest, nicht nur "importiert ohne Crash". Ohne
            # Abnahmekriterium ist das kein Fall fuer den schwaechen Fallback,
            # sondern ein Spezifikationsmangel, der den Mentor braucht (dasselbe
            # Muster wie bereits bei SIMULATIONS_PATTERNS: additiv, rein
            # deterministisch, kein LLM-Urteil noetig).
            abhaengigkeiten_row = con.execute(
                "SELECT abhaengigkeiten FROM strategie_planung WHERE kanonischer_name=? "
                "ORDER BY id DESC LIMIT 1",
                (lookup_name,),
            ).fetchone()
            hat_abhaengigkeiten = bool(
                abhaengigkeiten_row and abhaengigkeiten_row[0] and abhaengigkeiten_row[0] not in ("[]", "null", None)
            )
            if hat_abhaengigkeiten and not abnahme_match:
                beschreibung = (
                    f"{task_id}: haengt laut strategie_planung von anderen Modulen ab "
                    f"({abhaengigkeiten_row[0]}), hat aber KEIN maschinenpruefbares "
                    f"Abnahmekriterium in der Spec -- reine Instanziierungspruefung "
                    f"reicht hier nicht, weil sie das Zusammenspiel mit den "
                    f"Abhaengigkeiten nie testet (siehe theke_komponente.py-Fund "
                    f"2026-07-28). Spec braucht ein Abnahmekriterium, das die "
                    f"tatsaechliche Orchestrierung aufruft, bevor gemerged wird."
                )
                _blockiere_oder_eskaliere(
                    con, task_id, quelle="ausfuehrungs_check",
                    befund_key=f"fehlendes_integrations_abnahmekriterium:{task_id}",
                    beschreibung=beschreibung,
                )
                geblockt += 1
                continue

            if abnahme_match and lookup_name and lookup_name != datei.name:
                # Das Abnahmekriterium importiert typischerweise unter dem KANONISCHEN
                # Namen (z.B. "from pattern_analyse import PatternAnalyse") -- vor der
                # eigentlichen Auslieferung existiert die Datei aber nur unter ihrem
                # internen bestellung_<id>-Namen in generiert/. Fuer den Test hier eine
                # Kopie unter dem kanonischen Namen anlegen (gleiches Verzeichnis, nur
                # zum Testen, wird bei der echten Auslieferung ohnehin ueberschrieben).
                try:
                    (datei.parent / lookup_name).write_text(
                        datei.read_text(encoding="utf-8"), encoding="utf-8"
                    )
                except Exception:
                    pass

            # Fund 2026-08-25 (Mentor, live durch 5 falsche Eskalationen bei
            # beispiel_router_nulltreffer_fix_20260804 aufgedeckt): Code, der seinen
            # eigenen Ressourcen-Pfad ueber Path(__file__).parent berechnet (z.B.
            # RouterAgent's DB-Pfad "Path(__file__).parent / 'projekt.db'"), sah
            # bisher IMMER generiert/ als Basis -- unabhaengig davon, ob ziel_ordner
            # bekannt ist. In generiert/ existiert entweder GAR KEINE projekt.db
            # (sqlite3.connect legt dann klaglos eine neue LEERE Datei an) oder
            # eine veraltete/unvollstaendige Kopie von einem frueheren Testlauf --
            # in beiden Faellen sieht der Kandidat eine ANDERE Datenbank als er nach
            # der echten Auslieferung sehen wird. Ergebnis: ein inhaltlich korrekter
            # Fix wird faelschlich als kaputt gemeldet, weil der TEST die falsche
            # Ressource sieht, nicht weil der Code falsch ist (5 unnoetige
            # Korrekturrunden verursacht, live diagnostiziert). Fix: wenn
            # ziel_ordner bekannt ist, den Kandidaten testweise DORT unter seinem
            # kanonischen Namen ausfuehren (Path(__file__).parent zeigt dann auf
            # dieselbe Umgebung wie nach der echten Auslieferung) -- eine dort
            # eventuell schon ausgelieferte alte Version wird vorher gesichert und
            # NACH dem Test (auch bei Exceptions) wiederhergestellt, damit der
            # nachfolgende Differenztest weiterhin die echte alte Version sieht.
            test_ordner = str(datei.parent)
            ziel_pfad_fuer_test = None
            alte_sicherung = None
            modul_name_fuer_test = modul_name
            if ziel_ordner and lookup_name:
                kandidat_ziel_pfad = Path(ziel_ordner) / lookup_name
                try:
                    if kandidat_ziel_pfad.exists():
                        alte_sicherung = kandidat_ziel_pfad.read_text(encoding="utf-8")
                    kandidat_ziel_pfad.write_text(datei.read_text(encoding="utf-8"), encoding="utf-8")
                    ziel_pfad_fuer_test = kandidat_ziel_pfad
                    test_ordner = ziel_ordner
                    # Fund 2026-08-25 (Nachbesserung, live durch erneuten Fehlschlag
                    # trotz obigem Fix aufgedeckt): es reicht NICHT, die Kopie nach
                    # ziel_ordner zu schreiben -- wird das Modul weiter unter seinem
                    # generiert/-Namen importiert (modul_name = datei.stem), findet
                    # Python es ueber sys.path IMMER noch zuerst in generiert/ (dort
                    # existiert exakt dieselbe Datei unter demselben Namen), importiert
                    # also weiterhin die generiert/-Kopie -- __file__ zeigt dann trotz
                    # allem wieder auf generiert/, der eigentliche Zweck dieses Fixes
                    # greift nicht. Deshalb: sobald die ziel_ordner-Kopie existiert,
                    # unter IHREM Namen (lookup_name-Stem) importieren, nicht unter dem
                    # internen Bestellungs-Namen -- und generiert/ dann gar nicht erst
                    # in sys.path aufnehmen, damit kein gleichnamiger Treffer dort
                    # vorgezogen werden kann.
                    modul_name_fuer_test = Path(lookup_name).stem
                except Exception:
                    ziel_pfad_fuer_test = None
                    test_ordner = str(datei.parent)
                    modul_name_fuer_test = modul_name

            testcode = "import sys\n"
            testcode += f"sys.path.insert(0, {test_ordner!r})\n"
            if ziel_ordner and test_ordner != ziel_ordner:
                testcode += f"sys.path.insert(0, {ziel_ordner!r})\n"
            if ziel_pfad_fuer_test is None:
                testcode += f"sys.path.insert(0, {str(datei.parent)!r})\n"
            testcode += f"import {modul_name_fuer_test} as _m\n"
            # Fund 2026-07-28 (Mentor, unmittelbare Folge des Regex-Fixes direkt
            # darueber): sobald abnahme_match jetzt tatsaechlich greift, crasht
            # jedes Abnahmekriterium mit bar geschriebenem Klassennamen (z.B.
            # "ThekeAntwortGenerator()", wie es die eigene STANDARD_VORLAGE in
            # spec_formular.py selbst vorschreibt) mit NameError, weil bisher nur
            # der Modulname als "_m" importiert wurde, nicht seine Inhalte. Live
            # reproduziert (guardrail-Korrektur-Bestellung). "from ... import *"
            # ergaenzt, damit natuerlich geschriebene Abnahmekriterien (Klasse
            # direkt aufrufen, kein _m.-Praefix noetig) funktionieren.
            if abnahme_match:
                testcode += f"from {modul_name_fuer_test} import *\n"
            if abnahme_match:
                testcode += abnahme_match.group(1)
            elif geplante_klasse and geplante_klasse[0]:
                testcode += (
                    f"_k = getattr(_m, {geplante_klasse[0]!r}, None) or getattr(_m, {target_name_capitalized!r}, None)\n"
                    f"assert _k is not None, 'weder {geplante_klasse[0]} noch {target_name_capitalized} im Modul gefunden'\n"
                    f"_k()\n"
                )

            def _stelle_ziel_pfad_wieder_her() -> None:
                # Muss VOR dem Differenztest weiter unten laufen (der vergleicht
                # gegen die ECHTE alte Version) -- deshalb an jedem Ausstiegspunkt
                # aufgerufen, nicht nur am Ende der Funktion.
                if ziel_pfad_fuer_test is None:
                    return
                try:
                    if alte_sicherung is not None:
                        ziel_pfad_fuer_test.write_text(alte_sicherung, encoding="utf-8")
                    else:
                        ziel_pfad_fuer_test.unlink(missing_ok=True)
                except Exception:
                    pass

            # Fund 2026-08-29/30 (Mentor, live verursachter Vorfall): der
            # projekt.db-Datenverlust dieser Session entstand genau hier -- ein
            # Abnahmekriterium (specgen_61a1d7ffd1) importierte einen
            # __file__-abgeleiteten Pfad aus dem gepruesften Modul und rief
            # darauf .unlink() auf. Weil dieser Testlauf den Kandidaten absichtlich
            # in den ECHTEN ziel_ordner kopiert (Fund 2026-08-25 direkt darueber --
            # genau damit __file__-basierter Code dieselbe Umgebung sieht wie nach
            # echter Auslieferung), zeigte der Pfad auf die echte Produktions-DB,
            # nicht auf eine Kopie. ausfuehrungs_check() hatte bis dahin keinerlei
            # Schutz gegen destruktiven Code IM Abnahmekriterium selbst -- nur der
            # eine gefundene Fall wurde damals von Hand neutralisiert, die
            # strukturelle Luecke blieb offen. Fix: vor jeder Ausfuehrung das
            # Abnahmekriterium selbst auf bekannte destruktive Muster scannen
            # (Dateiloeschung, Tabellen-/Zeilen-Loeschung) -- bei Treffer NICHT
            # stillschweigend ausfuehren, sondern wie jeden anderen Blockade-Fund
            # behandeln (Eskalation nach Schwellenwert, siehe
            # _blockiere_oder_eskaliere). Bewusst nur bei abnahme_match (der Coder-
            # generierte Kandidatencode selbst wird bereits ueber andere Wege
            # geprueft) -- ein Abnahmekriterium hat gegenueber generiertem Code
            # zusaetzlich die Faehigkeit, ueber importierte __file__-Pfade auf
            # ECHTE Produktionsressourcen zuzugreifen, generierter Code allein
            # (ohne eigenes Abnahmekriterium) nicht in gleichem Mass.
            if abnahme_match:
                _DESTRUKTIVE_MUSTER = re.compile(
                    r"\.unlink\(|os\.remove\(|shutil\.rmtree\(|DROP\s+TABLE|DELETE\s+FROM",
                    re.IGNORECASE,
                )
                # Fund 2026-08-30 (Selbstkollision, wissens_charakterwahl-Korrektur):
                # ohne Kommentarzeilen auszuschliessen matcht der Scan auch reine
                # Erklaertexte, die das Muster nur als Doku/Begruendung NENNEN (z.B.
                # ein Kommentar, der beschreibt, welches Muster entfernt wurde) --
                # analog zur bereits gefixten Ueberschriften-Selbstkollision in
                # diesem File. Nur echten, nicht-auskommentierten Code scannen.
                _code_ohne_kommentare = re.sub(
                    r"^\s*#.*$", "", abnahme_match.group(1), flags=re.MULTILINE
                )
                _treffer = _DESTRUKTIVE_MUSTER.search(_code_ohne_kommentare)
                if _treffer:
                    _stelle_ziel_pfad_wieder_her()
                    beschreibung = (
                        f"{task_id}: Abnahmekriterium enthaelt ein destruktives Muster "
                        f"({_treffer.group(0)!r}) -- Ausfuehrung blockiert statt "
                        f"stillschweigend zugelassen (Fund 2026-08-29/30, "
                        f"projekt.db-Vorfall). Falls das Muster hier tatsaechlich sicher "
                        f"ist (z.B. eindeutig isolierter Scratch-Pfad statt eines "
                        f"__file__-abgeleiteten Produktionspfads), Mentor-Pruefung "
                        f"noetig, kein automatisches Freigeben."
                    )
                    _blockiere_oder_eskaliere(
                        con, task_id, quelle="ausfuehrungs_check",
                        befund_key=f"destruktives_abnahmekriterium:{task_id}",
                        beschreibung=beschreibung,
                    )
                    geblockt += 1
                    continue

            try:
                ergebnis = subprocess.run(
                    [sys.executable, "-c", testcode],
                    capture_output=True, text=True, timeout=timeout_s,
                )
            except subprocess.TimeoutExpired:
                _stelle_ziel_pfad_wieder_her()
                beschreibung = f"{task_id}: Ausfuehrung ueberschritt {timeout_s}s (moeglicher Hang)."
                _blockiere_oder_eskaliere(con, task_id, quelle="ausfuehrungs_check",
                                           befund_key=f"exec_timeout:{task_id}", beschreibung=beschreibung)
                geblockt += 1
                continue

            _stelle_ziel_pfad_wieder_her()

            if ergebnis.returncode != 0:
                # Fund 2026-07-15 (spezialist_interface.py, V8SpecialistInterface):
                # "Can't instantiate abstract class ... without an implementation" ist
                # das KORREKTE Python-Verhalten fuer eine absichtlich abstrakte
                # Basisklasse (ABC + @abstractmethod) -- kein Bug, sondern genau das,
                # was die Spezifikation verlangt. Ohne diese Ausnahme wuerde der
                # Check jede saubere ABC faelschlich als Crash blockieren.
                if "Can't instantiate abstract class" in ergebnis.stderr:
                    continue
                fehlertext = ergebnis.stderr.strip().splitlines()[-1] if ergebnis.stderr.strip() else "unbekannter Fehler"
                beschreibung = (
                    f"{task_id}: Ausfuehrung crasht ({fehlertext}). Voller Traceback: "
                    f"{ergebnis.stderr.strip()[-1500:]}"
                )
                _blockiere_oder_eskaliere(con, task_id, quelle="ausfuehrungs_check",
                                           befund_key=f"exec_crash:{task_id}", beschreibung=beschreibung)
                geblockt += 1
                continue

            # Fund 2026-07-29 (Betreiber/Arzt, live durchlebt): mein eigenes
            # Abnahmekriterium fuer eine Korrektur-Bestellung pruefte nur
            # "kommt ein nicht-leerer String zurueck" -- das haette auch der
            # ALTE, kaputte Code bestanden. Ein Abnahmekriterium, das nicht
            # zwischen altem (fehlerhaftem) und neuem Verhalten unterscheidet,
            # ist wertlos als Qualitaetsschranke, besteht aber diesen Check
            # bisher trotzdem, weil nur GEGEN den neuen Kandidaten getestet
            # wurde. Differenzieller Test: existiert bereits eine fruehere,
            # ausgelieferte Version im ziel_ordner (also eine Korrektur, kein
            # Erstbau), dasselbe Abnahmekriterium ZUSAETZLICH gegen DIESE alte
            # Version laufen lassen. Besteht die alte Version denselben Test
            # auch, ist das Kriterium zu schwach -- blockieren statt merged.
            if abnahme_match and ziel_ordner and lookup_name:
                alte_datei = Path(ziel_ordner) / lookup_name
                if alte_datei.exists() and alte_datei.resolve() != datei.resolve():
                    alt_modul_name = Path(lookup_name).stem
                    alt_testcode = "import sys\n"
                    alt_testcode += f"sys.path.insert(0, {ziel_ordner!r})\n"
                    alt_testcode += f"import {alt_modul_name} as _m\n"
                    alt_testcode += f"from {alt_modul_name} import *\n"
                    alt_testcode += abnahme_match.group(1)
                    try:
                        alt_ergebnis = subprocess.run(
                            [sys.executable, "-c", alt_testcode],
                            capture_output=True, text=True, timeout=timeout_s,
                            cwd=str(ziel_ordner),
                        )
                    except subprocess.TimeoutExpired:
                        alt_ergebnis = None
                    if alt_ergebnis is not None and alt_ergebnis.returncode == 0:
                        beschreibung = (
                            f"{task_id}: Abnahmekriterium besteht sowohl fuer den neuen "
                            f"Kandidaten ALS AUCH fuer die bereits ausgelieferte alte "
                            f"Version in {alte_datei} -- das Kriterium unterscheidet "
                            f"nicht zwischen altem (moeglicherweise fehlerhaftem) und "
                            f"neuem Verhalten und ist deshalb keine verlaessliche "
                            f"Qualitaetsschranke. Abnahmekriterium in der Spec muss "
                            f"praezisiert werden (z.B. konkreten Antwortinhalt statt "
                            f"nur Nicht-Leerheit pruefen)."
                        )
                        _blockiere_oder_eskaliere(
                            con, task_id, quelle="ausfuehrungs_check",
                            befund_key=f"schwaches_abnahmekriterium:{task_id}",
                            beschreibung=beschreibung,
                        )
                        geblockt += 1
                        continue

            # Touchpoint 2 (call_graph_kanten, Betreiber-Auftrag ueber Mentor,
            # 2026-08-26): an dieser Stelle im Code sind ALLE vorherigen
            # Fehlerpfade oben bereits per 'continue' verlassen worden --
            # wird dieser Punkt erreicht, hat der Kandidat den kompletten
            # Ausfuehrungs-Check bestanden (inkl. Differenztest gegen die
            # alte Version, falls vorhanden). Nur fuer echte Python-Ziele
            # sinnvoll (nicht-Python wurde weiter oben bereits uebersprungen).
            if lookup_name and lookup_name.endswith(".py"):
                _promote_call_graph_kanten(con, lookup_name)
                _melde_unerreichte_private_methoden(
                    con, lookup_name, datei.read_text(encoding="utf-8", errors="ignore")
                )
        con.commit()
        return geblockt
    finally:
        con.close()


def alters_waechter() -> list[dict]:
    """2.2: Findet Tasks, die laenger als debugger.stall_schwelle_minuten im
    selben Status ohne Fortschritt stecken (ausser merged/archiviert).

    Fund 2026-08-31 (2te_sok, Werft-Diagnose): der Terminalstatus-Ausschluss
    nutzte bisher 'archived' (englisch) statt 'archiviert' (deutsch, der
    tatsaechlich im gesamten Projekt verwendete Wert -- siehe z.B.
    bestellungen.status/task_pipeline.status ueberall sonst). Die beiden
    matchten nie, archivierte Tasks blieben dadurch dauerhaft in der
    Stall-Pruefung und wurden bei jedem Lauf erneut gemeldet (ein Fund hatte
    bereits 19.084 Wiederholungen ueber ~19 Tage). Reiner Tippfehler/Sprach-
    Inkonsistenz, keine Architekturaenderung."""
    schwelle = int(config.get("debugger.stall_schwelle_minuten", 60))
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT task_id, status, "
            "(julianday('now') - julianday(created_at)) * 1440 AS minuten "
            "FROM task_pipeline WHERE status NOT IN ('merged', 'archiviert') "
            "AND (julianday('now') - julianday(created_at)) * 1440 > ?",
            (schwelle,),
        ).fetchall()
        funde = [{"task_id": t, "status": s, "minuten": round(m, 1)} for t, s, m in rows]
        for f in funde:
            _melde_befund(
                con, quelle="alters_waechter", befund_key=f"stall:{f['task_id']}:{f['status']}",
                beschreibung=f"{f['task_id']} seit {f['minuten']} Min in Status '{f['status']}' "
                             f"(Schwelle: {schwelle} Min).",
            )
        con.commit()
        return funde
    finally:
        con.close()


def _melde_befund(con: sqlite3.Connection, quelle: str, befund_key: str, beschreibung: str) -> int:
    """2.3: Audit-Lebenszyklus -- gleicher befund_key erhoeht wiederholungen statt
    Duplikat anzulegen; ab debugger.eskalations_schwelle -> eskaliert_an='mentor'.

    Gibt die aktuelle Wiederholungszahl zurueck, damit Aufrufer (z.B.
    vor_merge_statik) selbst entscheiden koennen, ob weiter automatisch
    korrigiert oder eskaliert wird -- Fund 2026-07-15 (Betreiber-Nachfrage): OHNE
    diesen Rueckgabewert gab es KEINE Instanz, die den Debugger selbst
    regulierte, ein Task haette theoretisch unbegrenzt zwischen 'spec' und
    'code_ready' pendeln koennen. `eskaliert_an` wurde zudem nie gelesen --
    'Diagnose ohne Wirkung', genau das Muster aus dem 04-11-Fehlerkatalog,
    nur diesmal im Debugger selbst statt in dem, was er verhindern sollte.
    Fix hier: bei ERSTER Eskalation wird zusaetzlich ein mentor_actions_log-
    Eintrag angelegt -- derselbe Kanal, der bereits fuer Analyst/Coder/Debugger-
    Freigaben funktioniert, statt einen neuen Mechanismus zu erfinden."""
    schwelle = int(config.get("debugger.eskalations_schwelle", 3))
    row = con.execute(
        "SELECT id, wiederholungen, eskaliert_an FROM debugger_findings WHERE befund_key=? AND status!='verifiziert'",
        (befund_key,),
    ).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO debugger_findings (quelle, befund_key, beschreibung) VALUES (?, ?, ?)",
            (quelle, befund_key, beschreibung),
        )
        return 1
    finding_id, wiederholungen, war_bereits_eskaliert = row
    neu = wiederholungen + 1
    eskaliert_an = "mentor" if neu >= schwelle else None
    if eskaliert_an and not war_bereits_eskaliert:
        con.execute(
            "INSERT INTO mentor_actions_log (action_type, beschreibung, status) VALUES (?, ?, 'pending_review')",
            ("debugger_eskalation",
             f"Debugger-Befund '{befund_key}' ({quelle}) trat {neu}x auf ohne Aufloesung: "
             f"{beschreibung} -- automatische Korrektur gestoppt, wartet auf Mentor-Entscheidung."),
        )
    con.execute(
        "UPDATE debugger_findings SET wiederholungen=?, letzte_meldung=CURRENT_TIMESTAMP, "
        "eskaliert_an=COALESCE(eskaliert_an, ?) WHERE id=?",
        (neu, eskaliert_an, finding_id),
    )
    return neu


def balance_waechter() -> list[dict]:
    """2.4: Vergleicht Punktezahlen registrierter VDB-Collections, meldet bei
    Verhaeltnis > 50:1 zwischen zwei als 'vergleichbar' konfigurierten Domaenen.
    Liest die Liste aus system_config (kein Hardcode, sonst Fehlalarm bei
    absichtlich unterschiedlich grossen Domaenen)."""
    from vdb_client import vdb  # lazy import, vermeidet Kreisimport bei Tests ohne VDB

    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT config_value FROM system_config WHERE config_key='debugger.balance.vergleichbare_collections'"
        ).fetchone()
        if rows is None:
            return []  # keine Konfiguration -> bewusst kein Fehlalarm
        collections = [c.strip() for c in rows[0].split(",") if c.strip()]
        counts = {c: vdb.punkte_count(c) for c in collections if hasattr(vdb, "punkte_count")}
        funde = []
        namen = list(counts)
        for i in range(len(namen)):
            for j in range(i + 1, len(namen)):
                a, b = namen[i], namen[j]
                klein, gross = sorted((counts[a], counts[b]))
                if klein == 0:
                    continue
                verhaeltnis = gross / klein
                if verhaeltnis > 50:
                    beschreibung = f"{a}={counts[a]} vs {b}={counts[b]} (Verhaeltnis {verhaeltnis:.0f}:1)"
                    _melde_befund(
                        con, quelle="balance_waechter", befund_key=f"monokultur:{a}:{b}",
                        beschreibung=beschreibung,
                    )
                    funde.append({"a": a, "b": b, "verhaeltnis": verhaeltnis})
        con.commit()
        return funde
    finally:
        con.close()


def zyklus() -> dict:
    """Ein Debugger-Durchlauf: alle Pruef-Funktionen. Laeuft parallel zum
    Coordinator-Zyklus, nicht in dessen kritischem Pfad (ausser Vor-Merge-Statik
    und ausfuehrungs_check, die zwischen code_ready und Taufe haengen)."""
    return {
        "vor_merge_statik_geblockt": vor_merge_statik(),
        "ausfuehrungs_check_geblockt": ausfuehrungs_check(),
        "alters_waechter_funde": len(alters_waechter()),
        "balance_waechter_funde": len(balance_waechter()),
    }


if __name__ == "__main__":
    print(zyklus())
