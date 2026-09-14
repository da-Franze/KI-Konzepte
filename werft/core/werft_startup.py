"""Werft-Startup-Sweep: einmalige Bereinigung bei jedem master_loop-Start.

Betreiber-Auftrag (2026-07-20): "müsste es vor jedem Start einen Lauf geben, der
alles Unnötige/Überholte automatisch bereinigt? Dann würden auch Vorfälle wie
ein Stromausfall im vollen Lauf abgefangen." Vorbild: ein aelteres Vorgaengersystem
hatte genau das (claude_watchdog.py, 30-min Crontab -- doppelter Master killen,
DB-Lock erkennen, verwaiste Zuweisungen fixen). Für die neue Werft fehlte das
Äquivalent bisher komplett -- nach dem Stromausfall am 19.07. musste eine
separate "Arzt"-Instanz von Hand genau das prüfen (NUL-Byte-Reparatur,
Integrity-Check, Prozess-Dopplung). Und der Funktions-Registry-Gift-Fund vom
20.07. (router_agent.py vergiftete sich wiederholt an seinem eigenen alten,
fehlerhaften VDB-Registry-Eintrag) hätte ein Startup-Check theoretisch
automatisch finden können, statt manuell entdeckt werden zu müssen.

Läuft EINMALIG bei jedem main()-Start, nicht pro Zyklus. Rein strukturelle,
deterministische Aufräum-Aktionen -- additiv/reversibel (kein Datenverlust,
nur Status-Bereinigung + Prozess-Deduplizierung), daher bewusst OHNE
Freigabe-Gate (gleiche Risiko-Kategorie wie die autonomen Coder-DNA-Regeln,
siehe ERWEITERUNG_AUTONOME_LERNSCHLEIFE.md Abschnitt 0).
"""
import os
import sqlite3
from pathlib import Path

try:
    import psutil
except ImportError:  # Hostdiagnostik ist optional; der Werftkern bleibt ladbar.
    psutil = None

from runtime_paths import WERFT_DB_PATH as DB_PATH
_WERFT_ORDNER = Path(__file__).parent


def werft_code_hash() -> str:
    """Fund 2026-07-21 (Betreiber-Auftrag, Punkt 1 einer 5-teiligen Automatisierungs-
    Liste): Python cached importierte Module -- Aenderungen an coder.py/analyst.py/
    debugger.py/etc. wirken im LAUFENDEN master_loop-Prozess erst nach einem
    manuellen Neustart. Heute mehrfach live erlebt (u.a. fast vergessen, was zu
    sinnlosem Warten auf einen nie wirksamen Fix gefuehrt haette). Diese Funktion
    liefert einen kombinierten mtime-Fingerabdruck aller Top-Level-.py-Dateien
    dieses Ordners (NICHT generiert/ -- das aendert sich staendig durch normale
    Coder-Arbeit, ist kein Werft-Infrastruktur-Code). master_loop.main() vergleicht
    das bei jedem Zyklus gegen den Stand beim eigenen Start; bei Abweichung beendet
    sich der Prozess sauber -- ein AEUSSERER Wrapper (start_werft.sh) startet ihn
    dann automatisch mit frischem Code neu. Bewusste Trennung: Erkennung hier
    (reine Diagnose, kein Risiko), Neustart im Wrapper (kein Python-eigenes
    os.execv()-Gefriemel in einem "-c"-gestarteten Prozess)."""
    dateien = sorted(p for p in _WERFT_ORDNER.glob("*.py"))
    fingerabdruck = "|".join(f"{p.name}:{p.stat().st_mtime_ns}" for p in dateien)
    return fingerabdruck


def _kille_doppelte_master_loop_prozesse() -> list[int]:
    """Fund 2026-07-19 (Arzt-Befund nach Stromausfall): zwei parallele
    master_loop.py-Prozesse liefen gleichzeitig (klassisches "Doppelter-Master"-
    Muster aus der alten CLAUDE.md-Fehlerliste). Diese Funktion killt alle
    ANDEREN Prozesse mit demselben Kommandozeilen-Muster -- der aktuell
    startende Prozess (self) beansprucht die alleinige Rolle, da er per
    Definition der neuere ist.

    Fund 2026-07-20 (eigener Testlauf VOR dem Live-Einsatz -- deshalb isoliert
    getestet statt direkt scharf geschaltet): ein simples "master_loop" +
    "import main" als Teilstring im GESAMTEN Kommandozeilen-Text ist zu
    ungenau -- jedes Skript, das diese Woerter zufaellig im Quelltext erwaehnt
    (z.B. ein Test- oder Inspektionsskript wie dieses hier), matcht dann
    faelschlich mit. Der echte Daemon-Aufruf ist IMMER exakt
    "-c" gefolgt vom kurzen String "from master_loop import main; main(...)".
    Praezise pruefen: Kommandozeile hat genau 3 Teile (Interpreter, "-c",
    Argument), das Argument ist KURZ (<150 Zeichen, ein "-c"-Aufruf mit
    eingebettetem laengerem Skript kann das nie sein) UND beginnt mit dem
    exakten Muster."""
    eigene_pid = os.getpid()
    if psutil is None:
        return []
    gekillt = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.info["pid"] == eigene_pid:
                continue
            cmdline = proc.info["cmdline"] or []
            if len(cmdline) < 3 or cmdline[-2] != "-c":
                continue
            arg = cmdline[-1]
            if len(arg) < 150 and arg.strip().startswith("from master_loop import main"):
                proc.kill()
                gekillt.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return gekillt


def _pruefe_db_integrity() -> dict:
    """Prüft werft.db + alle Projekt-DBs, auf die aktuell eine Bestellung
    zeigt (ziel_ordner/*.db). Meldet nur, greift nicht destruktiv ein --
    ein 'not ok'-Ergebnis braucht einen Mentor, kein automatisches Reparieren
    (Datenintegrität ist die eine Kategorie, bei der Autonomie zu riskant ist)."""
    ergebnisse = {}
    con = sqlite3.connect(DB_PATH)
    try:
        ergebnisse[str(DB_PATH)] = con.execute("PRAGMA integrity_check;").fetchone()[0]
        ziel_ordner_liste = {r[0] for r in con.execute(
            "SELECT DISTINCT ziel_ordner FROM bestellungen WHERE ziel_ordner IS NOT NULL"
        ).fetchall()}
    finally:
        con.close()

    for ordner in ziel_ordner_liste:
        for db_datei in Path(ordner).glob("*.db"):
            try:
                proj_con = sqlite3.connect(db_datei)
                ergebnisse[str(db_datei)] = proj_con.execute("PRAGMA integrity_check;").fetchone()[0]
                proj_con.close()
            except Exception as e:
                ergebnisse[str(db_datei)] = f"Fehler: {e}"
    return ergebnisse


def _bereinige_verwaiste_registry_punkte() -> list[str]:
    """Fund 2026-07-20 (router_agent.py, wiederholte Selbst-Vergiftung): ein
    funktions_registry_punkte-Eintrag zeigt auf eine FRÜHERE Version desselben
    Ziels -- solange der zugehörige Task NICHT 'merged' ist, ist der Eintrag
    per Definition veraltet (ein 'merged'-Task hätte ihn ohnehin schon durch
    einen frischen ersetzt, siehe analyst._registriere_funktions_registry()).
    Löscht solche verwaisten Punkte aus Qdrant UND der lokalen Tabelle, damit
    der Coder bei der nächsten Regenerierung nicht wieder sein eigenes altes,
    fehlerhaftes Muster als 'ähnliche frühere Lösung' vorgesetzt bekommt."""
    from vdb_client import vdb

    con = sqlite3.connect(DB_PATH)
    bereinigt = []
    try:
        rows = con.execute("SELECT target_name, point_id FROM funktions_registry_punkte").fetchall()
        for target_name, point_id in rows:
            task_id = f"task_{target_name}"
            status_row = con.execute(
                "SELECT status FROM task_pipeline WHERE task_id=?", (task_id,)
            ).fetchone()
            if status_row is None:
                continue
            status = status_row[0]
            if status != "merged":
                try:
                    vdb.delete(point_id)
                except Exception:
                    pass
                con.execute(
                    "DELETE FROM funktions_registry_punkte WHERE target_name=?", (target_name,)
                )
                bereinigt.append(target_name)
        con.commit()
    finally:
        con.close()
    return bereinigt


def _bereinige_stale_findings() -> int:
    """Ein debugger_findings-Eintrag ('offen', z.B. 'stall:task:code_ready')
    ist stale, wenn der Task den beschriebenen Status LÄNGST verlassen hat
    (z.B. inzwischen 'merged' oder 'archived'). Automatisiert das manuelle
    Aufräumen, das heute mehrfach per Hand nötig war."""
    con = sqlite3.connect(DB_PATH)
    n = 0
    try:
        rows = con.execute(
            "SELECT id, befund_key FROM debugger_findings WHERE status='offen'"
        ).fetchall()
        for finding_id, befund_key in rows:
            teile = befund_key.split(":")
            if len(teile) < 3 or teile[0] not in ("stall", "exec_crash", "simulation_code", "stub_code"):
                continue
            task_id = teile[1]
            beschriebener_status = teile[2] if teile[0] == "stall" else None
            aktueller_status_row = con.execute(
                "SELECT status FROM task_pipeline WHERE task_id=?", (task_id,)
            ).fetchone()
            if aktueller_status_row is None:
                continue
            aktueller_status = aktueller_status_row[0]
            if aktueller_status == "merged" or (
                beschriebener_status and aktueller_status != beschriebener_status
            ):
                con.execute(
                    "UPDATE debugger_findings SET status='verifiziert' WHERE id=?", (finding_id,)
                )
                n += 1
        con.commit()
    finally:
        con.close()
    return n


def _pruefe_systemmap_konsistenz() -> list[str]:
    """Betreiber-Auftrag 2026-07-21: "Systemmap-Pflege... sollte bei jedem Start
    geprueft werden und aktualisiert." Die Systemmap (systemmap-Tabelle, seit
    heute automatisch bei jedem Merge in analyst.py::_aktualisiere_systemmap()
    gefuellt) kann trotzdem veralten -- z.B. wenn eine Datei nach dem Merge
    manuell/extern veraendert wurde, oder verschoben/geloescht wurde. Diese
    Funktion vergleicht bei jedem Werft-Start den dokumentierten Stand gegen
    die reale Datei (Wiederverwendung von coder.py::_schnittstelle_aus_datei(),
    kein zweiter Extraktions-Mechanismus). Meldet NUR Abweichungen (gleiche
    Vorsicht wie _pruefe_db_integrity() -- Code automatisch zu ueberschreiben
    waere eine echte Fehlentscheidungs-Moeglichkeit, kein reiner Aufraeum-Fall,
    siehe ERWEITERUNG_AUTONOME_LERNSCHLEIFE.md Abschnitt 0)."""
    from coder import _schnittstelle_aus_datei

    con = sqlite3.connect(DB_PATH)
    abweichungen = []
    try:
        rows = con.execute(
            """SELECT s.ziel_ordner, s.kanonischer_name, s.klasse,
                      s.methoden, s.dict_schluessel, s.bestellung_id, b.status
                 FROM systemmap AS s
                 LEFT JOIN bestellungen AS b ON b.bestellung_id = s.bestellung_id"""
        ).fetchall()
        for ziel_ordner, kanonischer_name, klasse_doku, methoden_doku, dict_doku, bestellung_id, bestellung_status in rows:
            if bestellung_id and bestellung_status in {None, "geliefert", "archiviert"}:
                continue
            pfad = Path(ziel_ordner) / kanonischer_name
            if not pfad.exists():
                abweichungen.append(f"{kanonischer_name}: Datei existiert nicht mehr unter {pfad}")
                continue
            aktuell = _schnittstelle_aus_datei(pfad)
            if klasse_doku and klasse_doku.strip("[]\"") not in aktuell:
                abweichungen.append(
                    f"{kanonischer_name}: dokumentierte Klasse {klasse_doku} nicht mehr im aktuellen "
                    f"Code gefunden -- vermutlich extern/manuell veraendert seit dem letzten Merge"
                )
    finally:
        con.close()
    return abweichungen


def sweep() -> dict:
    """Führt alle Prüfungen aus, gibt eine Zusammenfassung zurück.
    Wird von master_loop.main() EINMALIG vor Eintritt in die Zyklus-Schleife
    aufgerufen."""
    return {
        "doppelte_prozesse_gekillt": _kille_doppelte_master_loop_prozesse(),
        "db_integrity": _pruefe_db_integrity(),
        "verwaiste_registry_punkte_bereinigt": _bereinige_verwaiste_registry_punkte(),
        "stale_findings_bereinigt": _bereinige_stale_findings(),
        "systemmap_abweichungen": _pruefe_systemmap_konsistenz(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(sweep(), indent=2, ensure_ascii=False))
