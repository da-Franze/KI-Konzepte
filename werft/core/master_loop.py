"""Master-Loop: Coordinator (SQL-only) + Rollen-Dispatch (Neustart-Spec Abschnitt 6+8).

Coordinator-Schritt erzeugt Tasks aus offenen Gaps -- mit max_tasks_per_gap als
zweiter Sicherung gegen Task-Explosion (v8.3 Abschnitt 6.2: Redundanz-Pruefung lief
im alten System ueber eine Positiv-Liste von Status und vergass 'gap_review' ->
587 Tasks fuer einen Gap in 2h. Hier daher: Pruefung ueber ALLE Status ausser
'archived', nicht ueber eine Liste "relevanter" Status.
"""
import sqlite3
import sys
import time
from pathlib import Path

import analyst
import bestellungen
import coder
import debugger
import judge
import mentor_log_pflege
import planer
import router  # noqa: F401  (Betriebs-Rolle, hier nur importiert damit Fehler frueh auffallen)
import strategie
import summarizer
import werft_startup
import external_orders
from configloader import config
from memory import remember_system
from ollama_client import host_fuer_rolle
from ressourcen_status import RessourcenStatus

from runtime_paths import WERFT_DB_PATH as DB_PATH


def stratege_schritt() -> int:
    """Fund 2026-07-20 (Betreiber: "die Moeglichkeit [Modell bei wiederholtem Fehl-
    schlag automatisch zu hinterfragen] finde ich wichtig!"): strategie.
    bottleneck_scan() konnte das schon immer leisten (Pareto-Kosten-Schnitt ueber
    charakter_leistung), wurde aber NIE automatisch aufgerufen -- reine
    Diagnose-Faehigkeit ohne Ausloeser, exakt dieselbe Luecke wie bei den
    DNA-Regeln vorher. Anders als dort ABSICHTLICH mit Gate: ein echter
    R&D-Vergleichslauf kostet GPU-Zeit und mehrere Modellaufrufe -- kein reiner
    "kann nur besser werden"-Fall wie eine additive Verbots-Regel, sondern ein
    echter Ressourcen-Trade-off (deckt sich mit bottleneck_scan()s eigenem
    Docstring: "ob tatsaechlich ein Versuch gestartet wird, entscheidet der
    Mentor"). Diese Funktion automatisiert nur die ERKENNUNG (kostenlos, reine
    SQL-Aggregation), ratenbegrenzt auf 1x/Tag, und legt bei echten Kandidaten
    einen mentor_actions_log-Vorschlag an -- der eigentliche R&D-Lauf UND ein
    etwaiger Modellwechsel bleiben bewusst gated (coder_default_wechsel-Pfad,
    wie beim damaligen gpt-oss:20b-Wechsel). Gibt Anzahl neuer Vorschlaege zurueck."""
    intervall_h = float(config.get("stratege.bottleneck_scan_intervall_stunden", 24))
    con = sqlite3.connect(DB_PATH)
    try:
        letzter_lauf = con.execute(
            "SELECT config_value FROM system_config WHERE config_key='stratege.letzter_bottleneck_scan'"
        ).fetchone()
        if letzter_lauf:
            stunden_seit = con.execute(
                "SELECT (julianday('now') - julianday(?)) * 24", (letzter_lauf[0],),
            ).fetchone()[0]
            if stunden_seit is not None and stunden_seit < intervall_h:
                return 0

        con.execute(
            "INSERT INTO system_config (config_key, config_value, config_type, category) "
            "VALUES ('stratege.letzter_bottleneck_scan', datetime('now'), 'string', 'stratege') "
            "ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value"
        )
        con.commit()

        neu = 0
        for kandidat in strategie.bottleneck_scan():
            if not kandidat.get("verstaerkt"):
                continue  # nur echte Problemfaelle (cpk_status incapable/marginal), nicht jeden Vielnutzer
            schluessel = f"{kandidat['charakter_id']}:{kandidat['modell']}"
            bereits_vorgeschlagen = con.execute(
                "SELECT 1 FROM mentor_actions_log WHERE action_type='rnd_vorschlag' "
                "AND beschreibung LIKE ? AND status='pending_review'", (f"%{schluessel}%",),
            ).fetchone()
            if bereits_vorgeschlagen:
                continue
            con.execute(
                "INSERT INTO mentor_actions_log (action_type, beschreibung, status) VALUES (?, ?, 'pending_review')",
                ("rnd_vorschlag",
                 f"Bottleneck-Scan (automatisch, {kandidat['n_aufrufe']} Aufrufe, "
                 f"{kandidat['anteil_prozent']}% der kumulierten Kosten): {schluessel} zeigt "
                 f"cpk_status={kandidat['cpk_status']!r} -- Kandidat fuer einen echten R&D-"
                 f"Vergleichslauf (rnd_lab.vergleichslauf) gegen ein Alternativmodell. "
                 f"Start des Vergleichslaufs UND ein etwaiger Modellwechsel bleiben bewusst "
                 f"Mentor-Entscheidung (Ressourcenkosten, kein additiver Sicherheitsfall)."),
            )
            neu += 1
        con.commit()
        return neu
    finally:
        con.close()


def requeue_schritt() -> dict:
    """Setzt spec_failed-Tasks fuer einen neuen Coder-Versuch zurueck -- oder
    archiviert sie nach max_retry_spec_failed Versuchen (Looper-Praevention,
    Neustart-Spec §11: nach ~8-10 Wiederholungen ohne Fortschritt archivieren).

    Versuchszaehler = Anzahl der FAIL-Eintraege in werft_memory fuer das Ziel,
    NUR owner='analyst' (automatische Taufe-Ablehnungen). Fund 2026-07-15
    (beispielserie_teil2_router_agent, Betreiber-Auftrag Mentor-Review): ein generischer
    recall_system()-Zaehler ueber ALLE Eintraege verwechselt automatische
    Ablehnungen mit manuellen Mentor-Review-Korrekturen (owner='mentor_review',
    passiert NACH bereits bestandener Taufe) -- beide zusammen erschoepften das
    Retry-Budget vorzeitig, Task wurde faelschlich archiviert statt korrigiert.
    Mentor-Reviews sind eine andere Kategorie (zusaetzliche Qualitaetspruefung,
    kein automatischer Fehlschlag) und duerfen das Looper-Limit nicht mitzaehlen.
    """
    from memory import automatische_fehlversuche

    max_retry = int(config.get("pipeline.max_retry_spec_failed", 8))
    con = sqlite3.connect(DB_PATH)
    try:
        ergebnis = {"requeued": 0, "archiviert": 0}
        rows = con.execute(
            "SELECT task_id FROM task_pipeline WHERE status='spec_failed'"
        ).fetchall()
        for (task_id,) in rows:
            target = task_id.replace("task_", "")
            versuche = automatische_fehlversuche(target, limit=max_retry + 1)
            if versuche >= max_retry:
                con.execute(
                    "UPDATE task_pipeline SET status='archived' WHERE task_id=?", (task_id,)
                )
                ergebnis["archiviert"] += 1
            else:
                con.execute(
                    "UPDATE task_pipeline SET status='spec' WHERE task_id=?", (task_id,)
                )
                ergebnis["requeued"] += 1
        con.commit()
        return ergebnis
    finally:
        con.close()


def coordinator_schritt() -> int:
    """Erzeugt fuer jeden offenen, unresolved Gap ohne aktiven Task einen neuen Task.
    Gibt die Anzahl neu erzeugter Tasks zurueck."""
    max_pro_gap = int(config.get("pipeline.max_tasks_per_gap", 3))
    con = sqlite3.connect(DB_PATH)
    try:
        # ORDER BY severity DESC: Abschnitt 13, LLM-as-Judge-Wichtigkeit (judge.py
        # schreibt sie in severity) bestimmt die Bau-Reihenfolge, hoechste zuerst.
        gaps = con.execute(
            "SELECT gap_key, description, severity FROM system_gaps "
            "WHERE resolved=0 ORDER BY severity DESC"
        ).fetchall()
        erzeugt = 0
        for gap_key, description, severity in gaps:
            task_id = f"task_{gap_key}"
            # Ueber ALLE Status ausser 'archived' pruefen (v8.3 §6.2) -- keine
            # Positiv-Liste "relevanter" Status, die neue Status vergessen koennte.
            anzahl_aktiv = con.execute(
                "SELECT COUNT(*) FROM task_pipeline "
                "WHERE knowledge_links LIKE ? AND status != 'archived'",
                (f"%{gap_key}%",),
            ).fetchone()[0]
            if anzahl_aktiv >= max_pro_gap:
                continue
            exists = con.execute(
                "SELECT 1 FROM task_pipeline WHERE task_id=?", (task_id,)
            ).fetchone()
            if exists:
                continue
            con.execute(
                "INSERT INTO task_pipeline (task_id, status, assigned_agent, "
                "knowledge_links, priority) VALUES (?, 'audit', 'Coordinator', ?, ?)",
                (task_id, f"INTERNAL_GAP_FIX | target: {gap_key} | {description}", severity),
            )
            erzeugt += 1
        con.commit()
        return erzeugt
    finally:
        con.close()


def _sicher(name: str, fn) -> object:
    """Fuehrt einen Rollenschritt fehlertolerant aus.

    Fund 2026-07-17 (Recycle-Pfad-Testlauf): zyklus() hatte KEINERLEI
    Fehlerbehandlung -- ein einzelner transienter Fehler (hier: Ollama-
    Read-Timeout bei GPU-Kontention waehrend coder.run_once()) riss die
    GESAMTE Master-Loop ab und beendete den Prozess. Das verhindert echte
    unbeaufsichtigte Eigenstaendigkeit: eine Werft, die nur laeuft solange
    niemand einen Netzwerk-Hänger verursacht, ist nicht eigenstaendig.
    Faengt Exceptions pro Rolle ab, loggt sie nach mentor_actions_log
    (action_type='rollen_fehler', damit sie wie andere Mentor-Eskalationen
    sichtbar/aufraeumbar sind) statt den Prozess zu beenden. Absichtlich
    breites except: der naechste Zyklus soll IMMER weiterlaufen koennen,
    unabhaengig von der Fehlerursache."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- Absicht: kein Rollenfehler darf die Loop toeten
        con = sqlite3.connect(DB_PATH)
        try:
            con.execute(
                "INSERT INTO mentor_actions_log (action_type, beschreibung) VALUES (?, ?)",
                ("rollen_fehler", f"[{name}] {type(exc).__name__}: {exc}"),
            )
            con.commit()
        finally:
            con.close()
        print(f"FEHLER in Rolle '{name}' abgefangen, Loop laeuft weiter: {type(exc).__name__}: {exc}")
        return {"fehler": str(exc)}


_ressourcen_status = RessourcenStatus()


def _beobachte_modell_swap(rolle: str, config_key: str) -> None:
    """Betreiber-Auftrag 2026-07-31 ("Modul 1 live in master_loop einbauen und
    beobachten"): protokolliert VOR jedem Rollen-Aufruf, ob dessen konfiguriertes
    Modell schon geladen ist (kein Swap) oder nicht (Swap noetig). Reine
    Beobachtung -- greift nicht in die Reihenfolge ein (das waere Modul 3, siehe
    SPEC_RESSOURCEN_MANAGER.md: "erst nachdem beobachtet wurde, ob Swaps
    ueberhaupt haeufig genug vorkommen"). Fehler in der Protokollierung selbst
    duerfen nie den eigentlichen Rollen-Aufruf verhindern -- deshalb ein
    eigenes, sehr breites except (RessourcenStatus faengt intern schon ab,
    dies ist nur eine zusaetzliche Sicherheitsschicht falls sich das mal aendert).

    Modul 5 (2026-08-01): prueft ab jetzt gegen host_fuer_rolle(rolle), nicht
    mehr blind gegen den globalen ollama.host -- sonst wuerde diese Beobachtung
    seit der PU-Aufteilung (summarizer/analyst -> pu_2) staendig faelschlich
    "Swap noetig" gegen die falsche (nie genutzte) GPU-Instanz melden."""
    try:
        modell = config.get(config_key)
        if modell:
            _ressourcen_status.protokolliere_check(rolle, modell, host=host_fuer_rolle(rolle))
    except Exception:  # noqa: BLE001 -- Beobachtung darf den Zyklus nie stoeren
        pass


def mentor_autokorrektur_schritt() -> int:
    """Fund 2026-07-20 (Betreiber: "kannst du die Mentor-Entscheidung nicht autonom in
    die Werft bringen? Wenn etwas nicht passt, muss nachgearbeitet werden!"):
    'review_pending' (Debugger-Eskalation) und 'archived' (Retry-Budget erschoepft)
    sind bewusste Sicherheitsventile gegen Endlosschleifen -- brauchten bisher aber
    IMMER einen Mentor, der von Hand eine neue Diagnose schreibt und
    strategie.fordere_korrektur() aufruft. Ohne wachen Mentor blieb ein Task dort
    einfach liegen (live beobachtet: 9h Leerlauf ueber Nacht, obwohl der Code laut
    Mentor-Live-Test schon fast fertig war).

    Diese Funktion gibt dem BEREITS VORHANDENEN Code EINMAL TAEGLICH pro Task
    automatisch eine frische Chance durch Debugger+Analyst (status='code_ready',
    KEINE Neugenerierung -- vermeidet das mehrfach dokumentierte Regressions-Risiko
    einer kompletten Coder-Neuschreibung auf moeglicherweise schon fast fertigem
    Code). Nutzt den Analysten als eingebaute Diagnose-Instanz (LLM-Urteil + neue,
    frische Fehlermeldung in werft_memory) statt selbst zu interpretieren was ein
    Fehler bedeutet -- das waere eine Bewertung, keine reine Struktur-Aktion.

    Bewusst OHNE Freigabe-Gate (wie die DNA-Regeln): reversibel (nur ein
    Status-Wechsel + Retry-Budget-Reset, kein Datenverlust, keine Ressourcen-
    intensive Neugenerierung), tages-limitiert (kein Endlos-Verbrauch bei echt
    festsitzenden Faellen), nutzt ausschliesslich die bereits bestehende, bewaehrte
    Debugger+Analyst-Pruefkette -- keine neue Entscheidungsmacht, nur ein neuer
    Ausloeser fuer eine bereits vorhandene Pruefung."""
    con = sqlite3.connect(DB_PATH)
    try:
        kandidaten = con.execute(
            "SELECT task_id FROM task_pipeline WHERE status IN ('review_pending', 'archived')"
        ).fetchall()
        angestossen = 0
        for (task_id,) in kandidaten:
            bestellung_id = task_id.replace("task_bestellung_", "") if task_id.startswith("task_bestellung_") else None
            if not bestellung_id:
                continue
            marker = f"autokorrektur:{task_id}"
            heute_schon = con.execute(
                "SELECT 1 FROM mentor_actions_log WHERE action_type='mentor_autokorrektur' "
                "AND beschreibung LIKE ? AND date(vorgeschlagen_at) = date('now')",
                (f"%{marker}%",),
            ).fetchone()
            if heute_schon:
                continue
            existiert = con.execute(
                "SELECT 1 FROM bestellungen WHERE bestellung_id=?", (bestellung_id,)
            ).fetchone()
            if not existiert:
                continue

            # Fund 2026-07-21 (Betreiber-Auftrag, Punkt 5 einer 5-teiligen Automatisierungs-
            # Liste, offene Frage aus der urspruenglichen Erweiterung vom 20.07.): das
            # Tageslimit verhindert MEHRFACHE Ausloesung am selben Tag, aber nicht ein
            # potenziell endloses "jeden Tag EINMAL automatisch neu versuchen" ueber
            # viele Tage, ohne dass je ein Mensch die eigentliche Ursache anschaut.
            # Gesamt-Obergrenze: ab N bisherigen Autokorrektur-Versuchen (ueber alle
            # Tage summiert) NICHT mehr automatisch zuruecksetzen, sondern eine echte
            # Eskalation loggen -- der Task bleibt bewusst in review_pending/archived
            # stehen, bis ein Mentor die Ursache klaert (analog zur Debugger-Eskalations-
            # schwelle, gleiches Prinzip: Wiederholung ohne Fortschritt ist ein Signal,
            # kein Grund fuer noch einen automatischen Versuch).
            bisherige_versuche = con.execute(
                "SELECT COUNT(*) FROM mentor_actions_log WHERE action_type='mentor_autokorrektur' "
                "AND beschreibung LIKE ?",
                (f"%{marker}%",),
            ).fetchone()[0]
            max_versuche = int(config.get("mentor_autokorrektur.max_versuche", 5))
            if bisherige_versuche >= max_versuche:
                bereits_eskaliert = con.execute(
                    "SELECT 1 FROM mentor_actions_log WHERE action_type='mentor_autokorrektur_eskalation' "
                    "AND beschreibung LIKE ?",
                    (f"%{marker}%",),
                ).fetchone()
                if not bereits_eskaliert:
                    con.execute(
                        "INSERT INTO mentor_actions_log (action_type, beschreibung, status) VALUES (?, ?, 'pending_review')",
                        ("mentor_autokorrektur_eskalation",
                         f"[{marker}] {task_id} wurde bereits {bisherige_versuche}x automatisch "
                         f"auf code_ready zurueckgesetzt, ohne dass die Ursache je geklaert wurde -- "
                         f"Gesamt-Obergrenze ({max_versuche}) erreicht, KEINE weitere automatische "
                         f"Korrektur mehr, wartet auf echte Mentor-Entscheidung (nicht nur eine "
                         f"weitere frische Pruefung desselben Codes)."),
                    )
                    con.commit()
                continue

            con.execute("UPDATE task_pipeline SET status='code_ready' WHERE task_id=?", (task_id,))
            con.commit()

            # Retry-Budget zuruecksetzen -- gleicher Mechanismus wie strategie.
            # fordere_korrektur() (Marker owner='mentor_review', tags enthaelt
            # 'fordere_korrektur'), hier aber gezielt OHNE den dortigen Zwangs-Reset
            # auf status='spec', weil der vorhandene Code weiterverwendet wird.
            remember_system(
                owner="mentor_review",
                target=f"bestellung_{bestellung_id}",
                content=f"[{marker}] Automatische Mentor-Autokorrektur: bereits vorhandener Code "
                        f"bekommt eine frische Debugger+Analyst-Pruefung ohne Neugenerierung.",
                tags="fordere_korrektur,mentor_autokorrektur",
            )
            con.execute(
                "INSERT INTO mentor_actions_log (action_type, beschreibung, status) VALUES (?, ?, 'auto_angewendet')",
                ("mentor_autokorrektur",
                 f"[{marker}] {task_id} war festsitzend, automatisch auf code_ready zurueckgesetzt "
                 f"fuer eine frische Debugger+Analyst-Pruefung des vorhandenen Codes -- keine "
                 f"Mentor-Freigabe noetig (reversibel, keine Neugenerierung, dieselbe Pruefkette "
                 f"wie bei jeder anderen Lieferung)."),
            )
            con.commit()
            angestossen += 1
        return angestossen
    finally:
        con.close()


def _sortiere_rollen_swap_bewusst(rollen: list) -> list:
    """Modul 3 (SPEC_RESSOURCEN_MANAGER.md): stabiler Sort -- innerhalb der
    uebergebenen Rollen wandern jene, deren konfiguriertes Modell auf ihrem
    eigenen Host (host_fuer_rolle()) laut RessourcenStatus BEREITS geladen
    ist, an den Anfang. Reihenfolge innerhalb der beiden Gruppen (geladen /
    nicht-geladen) bleibt unveraendert -- kein neues willkuerliches
    Kriterium, nur ein zusaetzliches schwaches (Spec-Wortlaut).

    `rollen`: Liste von (rollenname, config_key, funktion)-Tupeln. Bewusst
    NUR auf Rollen ohne dokumentierte harte Ausfuehrungsreihenfolge
    angewendet (siehe zyklus(): 'debugger' MUSS vor 'analyst' laufen,
    'planer' MUSS vor 'summarizer' laufen -- diese Anker bleiben in zyklus()
    fest, nur die swap-relevanten Rollen DAZWISCHEN werden sortiert).

    Modul-5-Hinweis: seit summarizer/analyst per host_fuer_rolle() auf pu_2
    geroutet sind, teilen sich die aktuell einzigen zwei sortierten Rollen
    (summarizer, coder) KEINEN Host mehr -- diese Funktion aendert an ihnen
    live nichts mehr (beide Zustaende sind fast immer 'nicht kollidierend'),
    bleibt aber korrekt UND als Sicherheitsnetz sinnvoll, falls die PU-
    Trennung je zurueckgenommen wird oder weitere Rollen denselben Host
    teilen."""
    def ist_bereits_geladen(eintrag) -> bool:
        rolle, config_key, _fn = eintrag
        modell = config.get(config_key)
        if not modell:
            return False
        return _ressourcen_status.ist_geladen(modell, host=host_fuer_rolle(rolle))

    return sorted(rollen, key=lambda eintrag: not ist_bereits_geladen(eintrag))


def zyklus() -> dict:
    """Ein Durchlauf: Bestellungs-Eingang, Judge, Coordinator, Debugger (Vor-Merge-
    Statik + periodische Waechter), dann jede Kernrolle einmal (falls Arbeit ansteht).
    Jeder Schritt laeuft fehlertolerant ueber _sicher() -- siehe dort."""
    ergebnis = {"externe_bestellungen": _sicher("externe_bestellungen", external_orders.import_orders)}
    ergebnis["bestellung_eingang"] = _sicher("bestellung_eingang", bestellungen.eingang_schritt)
    _beobachte_modell_swap("judge", "ollama.model.judge")
    ergebnis["judge"] = _sicher("judge", judge.judge_schritt)
    ergebnis["neue_tasks"] = _sicher("coordinator", coordinator_schritt)
    ergebnis["requeue"] = _sicher("requeue", requeue_schritt)
    ergebnis["planer"] = _sicher("planer", planer.run_once)  # VOR Summarizer -- reichert knowledge_links an
    # Modul 3 (SPEC_RESSOURCEN_MANAGER.md): summarizer/coder haben keine dokumentierte
    # harte Reihenfolge zueinander (anders als debugger->analyst) -- swap-bewusst sortiert.
    for _rolle, _config_key, _fn in _sortiere_rollen_swap_bewusst([
        ("summarizer", "ollama.model.summarizer", summarizer.run_once),
        ("coder", "ollama.model.coder", coder.run_once),
    ]):
        _beobachte_modell_swap(_rolle, _config_key)
        ergebnis[_rolle] = _sicher(_rolle, _fn)
    ergebnis["debugger"] = _sicher("debugger", debugger.zyklus)  # Vor-Merge-Statik VOR der Analyst-Taufe
    _beobachte_modell_swap("analyst", "ollama.model.analyst")
    ergebnis["analyst"] = _sicher("analyst", analyst.run_once)
    ergebnis["bestellung_auslieferung"] = _sicher("bestellung_auslieferung", bestellungen.auslieferung_schritt)
    _beobachte_modell_swap("stratege", "ollama.model.stratege")
    ergebnis["stratege"] = _sicher("stratege", stratege_schritt)
    ergebnis["mentor_autokorrektur"] = _sicher("mentor_autokorrektur", mentor_autokorrektur_schritt)
    # Betreiber-Auftrag 2026-07-27 (Tisch 15:54): Bereinigung automatisieren statt wie
    # bisher 3x manuell nachzuholen (07-16/18/19) -- reine SQL-Operation, billig
    # genug fuer jeden Zyklus (siehe mentor_log_pflege.py).
    ergebnis["mentor_log_pflege"] = _sicher("mentor_log_pflege", mentor_log_pflege.bereinigen_schritt)
    return ergebnis


def _war_aktiv(ergebnis: dict) -> bool:
    """debugger liefert immer ein (moeglicherweise leeres) dict zurueck -- dessen
    Werte muessen einzeln geprueft werden, sonst gilt jeder Zyklus faelschlich
    als aktiv und die Idle-Erkennung ('Keine Arbeit anstehend') greift nie."""
    for key, value in ergebnis.items():
        if key == "debugger":
            if any(value.values()):
                return True
            continue
        if key == "mentor_log_pflege":
            # bereinigen_schritt() liefert IMMER ein nicht-leeres dict ({'aktiv':
            # True, 'geprueft': N, ...}) -- nur echte Aenderungen zaehlen als
            # Aktivitaet, sonst greift die Idle-Erkennung wie beim debugger-Fund
            # oben nie (jeder Zyklus haette sonst faelschlich "war aktiv").
            if isinstance(value, dict) and (
                value.get("geschlossen_resolved") or value.get("geschlossen_duplikat")
            ):
                return True
            continue
        if value:
            return True
    return False


def main(max_zyklen: int = None, pause_s: int = None) -> None:
    if pause_s is None:
        pause_s = int(config.get("timeouts.master_loop_pause_sec", 10))
    # Fund 2026-07-20 (Betreiber: "muesste es vor jedem Start einen Lauf geben,
    # der alles Unnoetige/Ueberholte automatisch bereinigt? Dann wuerden auch
    # Vorfaelle wie ein Stromausfall im vollen Lauf abgefangen"): einmaliger
    # Sweep VOR der Zyklus-Schleife, nicht pro Zyklus -- siehe werft_startup.py.
    try:
        startup_ergebnis = werft_startup.sweep()
        print(f"Werft-Startup-Sweep: {startup_ergebnis}")
    except Exception as exc:
        print(f"Werft-Startup-Sweep FEHLER (Loop startet trotzdem): {type(exc).__name__}: {exc}")

    # Fund 2026-07-21 (Betreiber-Auftrag): Code-Aenderungen an der Werft selbst
    # wirken im laufenden Prozess nicht, solange niemand manuell neu startet.
    # Fingerabdruck beim Start einfrieren, bei Abweichung sauber beenden --
    # ein aeusserer Wrapper (start_werft.sh) startet automatisch neu.
    start_hash = werft_startup.werft_code_hash()

    zyklus_nr = 0
    while max_zyklen is None or zyklus_nr < max_zyklen:
        zyklus_nr += 1
        ergebnis = zyklus()
        print(f"Zyklus {zyklus_nr}: {ergebnis}")
        if not _war_aktiv(ergebnis):
            print("Keine Arbeit anstehend, warte...")

        if werft_startup.werft_code_hash() != start_hash:
            print("Werft-Code hat sich geaendert -- beende sauber fuer automatischen Neustart mit frischem Code.")
            sys.exit(0)

        time.sleep(pause_s)


if __name__ == "__main__":
    main()
