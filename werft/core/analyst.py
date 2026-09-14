from __future__ import annotations

"""Analyst: Taufe-Check -- prueft generierten Code gegen die Spezifikation
(Neustart-Spec Abschnitt 7.1).

Nutzt memory.remember_system() fuer Ablehnungsgruende -- das erzwingt strukturell
(nicht nur per Konvention) memory_type='system' + Mindestlaenge + Retrieval-
Verifikation. Genau das war der historische Bug (siehe memory.py-Docstring).
"""
import importlib.util
import json
import re
import sqlite3
from pathlib import Path

from configloader import config
from coder import (
    _schnittstelle_aus_datei, _KLASSE_MUSTER, _METHODEN_MUSTER, _DICT_ZUGRIFF_MUSTER,
    _METHODEN_NAME_MUSTER, _SELF_AUFRUF_MUSTER, _extrahiere_methode,
)
from json_utils import extrahiere_json_objekt
from memory import remember_system, BegruendungZuKurzError
from ollama_client import ask_model, host_fuer_rolle
from vdb_client import vdb
from product_archive import archive_product


def _aktualisiere_systemmap(ziel_ordner: str, kanonischer_name: str, code: str, bestellung_id: str | None) -> None:
    """Fund 2026-07-21 (Betreiber-Einwand, nach Klassennamen-Regression bei
    'Economic Scout'): die Systemmap (spezifikation/serie_kern/00_systemmap.md)
    wurde am 2026-07-15 GENAU wegen Namens-/Schnittstellen-Mismatches
    eingefuehrt, deckte aber nur die urspruengliche Kern-Serie ab und wurde nie
    als LAUFENDER Mechanismus weitergefuehrt (im Vorgaenger-System geschah das
    automatisch bei jeder 'Taufe' ueber _update_system_map). Diese Funktion
    reaktiviert genau das: bei JEDEM erfolgreichen Merge wird die reale
    Schnittstelle (Klasse, Methoden, erwartete Dict-Schluessel -- Wiederverwendung
    derselben Muster wie coder.py::_schnittstelle_aus_datei(), keine Duplikation)
    UND die tatsaechlich importierten lokalen Module in die systemmap-Tabelle
    geschrieben/aktualisiert -- Ist-Zustand statt Papier, das seit dem 17.07.
    niemand mehr angefasst hat."""
    con = sqlite3.connect(DB_PATH)
    try:
        klassen = _KLASSE_MUSTER.findall(code)
        # Mehrzeilige Signaturen enthalten nach dem Regex-Match noch die
        # Original-Zeilenumbrueche/Einrueckung -- fuer eine lesbare
        # systemmap.methoden-Liste auf eine Zeile normalisieren.
        methoden = [re.sub(r"\s+", " ", m).strip() for m in _METHODEN_MUSTER.findall(code)]
        dict_zugriffe: dict[str, set[str]] = {}
        for varname, key in _DICT_ZUGRIFF_MUSTER.findall(code):
            dict_zugriffe.setdefault(varname, set()).add(key)
        # Wiederverwendung derselben Import-Erkennung wie _fehlende_modul_referenzen()
        # weiter unten in dieser Datei -- DRY statt zweiter, abweichender Regex.
        ruft_auf = sorted(set(_IMPORT_MUSTER.findall(code)))

        con.execute(
            "INSERT INTO systemmap (ziel_ordner, kanonischer_name, klasse, methoden, dict_schluessel, "
            "ruft_auf, bestellung_id, aktualisiert_am) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(ziel_ordner, kanonischer_name) DO UPDATE SET "
            "klasse=excluded.klasse, methoden=excluded.methoden, dict_schluessel=excluded.dict_schluessel, "
            "ruft_auf=excluded.ruft_auf, bestellung_id=excluded.bestellung_id, aktualisiert_am=excluded.aktualisiert_am",
            (
                ziel_ordner,
                kanonischer_name,
                json.dumps(klassen, ensure_ascii=False),
                json.dumps(methoden, ensure_ascii=False),
                json.dumps({k: sorted(v) for k, v in dict_zugriffe.items()}, ensure_ascii=False),
                json.dumps(ruft_auf, ensure_ascii=False),
                bestellung_id,
            ),
        )
        con.commit()
    finally:
        con.close()


def _finalisiere_call_graph_kanten(con: sqlite3.Connection, lookup_name: str, code: str, bestellung_id: str | None) -> int:
    """Touchpoint 3 der call_graph_kanten-Infrastruktur (Betreiber-Auftrag ueber
    Mentor, 2026-08-26): beim finalen Merge alle intra-Datei self.X()-
    Aufruf-Kanten der GEMERGTEN Datei auf status='ist' setzen -- die hoechste
    Vertrauensstufe (unsicher -> in_pruefung -> ist). Unconditional statt nur
    fuer bereits vorhandene Zeilen: ein Setzen auf 'ist' kann nie eine
    Herabstufung sein, unabhaengig vom vorherigen Status -- deckt auch den
    Fall ab, dass Touchpoint 1/2 eine Kante aus irgendeinem Grund nie sahen
    (z.B. Patch-Modus-Korrekturrunde), die aber im finalen Code echt vorhanden
    ist. Gleiche Erkennungslogik wie coder.py::_aktualisiere_call_graph()
    (DRY, importierte Konstanten statt Duplikat).

    Try/except sqlite3.Error wie ressourcen_status.py::protokolliere_check()
    (Fund 2026-08-26, Betreiber-Nachfrage ueber Mentor) -- diese Beobachtungs-
    Aktualisierung darf die eigentliche Taufe (Merge + Registrierung) nie
    verhindern. Gibt die Anzahl aktualisierter Kanten zurueck."""
    namen = sorted(set(_METHODEN_NAME_MUSTER.findall(code)))
    namen_set = set(namen)
    kanten: set[tuple[str, str]] = set()
    for von_methode in namen:
        span = _extrahiere_methode(code, von_methode)
        if span is None:
            continue
        koerper = code[span[0]:span[1]]
        for match in _SELF_AUFRUF_MUSTER.finditer(koerper):
            ziel = match.group(1)
            if ziel in namen_set:
                kanten.add((von_methode, ziel))
    if not kanten:
        return 0

    try:
        for von_methode, nach_methode in kanten:
            con.execute(
                "INSERT INTO call_graph_kanten "
                "(von_datei, von_methode, nach_datei, nach_methode, status, quelle, task_id, aktualisiert_am) "
                "VALUES (?, ?, ?, ?, 'ist', 'analyst', ?, datetime('now')) "
                "ON CONFLICT(von_datei, von_methode, nach_datei, nach_methode) DO UPDATE SET "
                "status='ist', task_id=excluded.task_id, aktualisiert_am=excluded.aktualisiert_am",
                (lookup_name, von_methode, lookup_name, nach_methode, bestellung_id),
            )
            # Fund 2026-08-31 (2te_sok, Werft-Diagnose): 71 von 73 offenen
            # 'kein_aufrufer_gefunden'-Funden waren bereits laengst durch
            # spaetere call_graph_kanten-Eintraege widerlegt (Methode wird
            # nachweislich aufgerufen), blieben aber ewig 'offen', weil nichts
            # sie je zurueckgemeldet hat -- debugger.py::
            # _melde_unerreichte_private_methoden() meldet nur NEUE Funde, es
            # gibt keine Gegenstelle, die einen alten Fund bei neuer Evidenz
            # schliesst. Hier direkt am Punkt, an dem eine Kante endgueltig als
            # 'ist' bestaetigt wird, den passenden offenen Fund automatisch
            # aufloesen -- verhindert, dass sich dieselbe Alt-Fund-Klasse
            # wieder neu ansammelt.
            con.execute(
                "UPDATE debugger_findings SET status='verifiziert' "
                "WHERE quelle='ausfuehrungs_check' AND status='offen' "
                "AND befund_key=?",
                (f"kein_aufrufer_gefunden:{lookup_name}:{nach_methode}",),
            )
        con.commit()
        return len(kanten)
    except sqlite3.Error:
        return 0


_METHODEN_MUSTER = re.compile(r"^\s{0,8}def\s+(\w+\([\s\S]*?\))\s*(?:->\s*[^:\n]+)?:", re.MULTILINE)
# Fund 2026-08-04 (Betreiber-Auftrag, Systemmap-Konsistenzpruefung bei
# hypothesengenerator.py): die alte Version verlangte Signatur+Klammer in
# EINER Zeile -- mehrzeilige Signaturen (Parameter ueber mehrere Zeilen,
# im Projekt haeufig bei Methoden mit vielen Argumenten) wurden komplett
# uebersehen, nicht nur unvollstaendig erfasst. [\s\S]*? statt .*? erlaubt
# Zeilenumbrueche innerhalb der Klammer; das ")"+optionaler Return-Type+":"
# am Ende grenzt die Signatur zuverlaessiger ab als vorher.


def _registriere_funktions_registry(target_name: str, spec_inhalt: str, code: str, code_pfad: str) -> str:
    """Funktions-Registry (Betreiber-Vorschlag 2026-07-15): jede erfolgreiche Taufe
    schreibt zusaetzlich einen semantisch durchsuchbaren Eintrag in werft_wissen
    -- Wiederbelebung der alten typ=system_map-Idee (PIPELINE_ABLAUF.md,
    _update_system_map), ergaenzt die reine specialists_v8-SQL-Zeile um
    Methoden-Signaturen, damit kuenftige Coder/Summarizer-Aufrufe per VDB-Suche
    finden koennen, was bereits existiert, statt es zu duplizieren.

    Fund 2026-07-15 (beispielserie_teil5): ein Registry-Eintrag aus einem
    SPAETER als fehlerhaft erkannten Merge blieb sonst dauerhaft mit hohem
    Aehnlichkeits-Score abrufbar -- der Coder bekam bei jedem Korrektur-Retry
    sein EIGENES, laengst ueberholtes (fehlerhaftes) Muster als "aehnliche
    fruehere Loesung" zurueckgespiegelt und wiederholte es trotz gegenteiliger
    Fehlhistorie (3 identische Fehlschlaege in Folge, bevor das auffiel). Fix:
    VOR jedem neuen Eintrag den alten fuer dasselbe Ziel loeschen (Poka-Yoke --
    strukturell unmoeglich machen statt nur zu hoffen, dass er nicht stoert)."""
    con = sqlite3.connect(DB_PATH)
    try:
        alt = con.execute(
            "SELECT point_id FROM funktions_registry_punkte WHERE target_name=?", (target_name,)
        ).fetchone()
        if alt:
            try:
                vdb.delete(alt[0])
            except Exception:
                pass  # Loeschen ist best-effort -- ein verwaister Punkt ist kein Blocker

        methoden = _METHODEN_MUSTER.findall(code)
        text = (
            f"FUNKTIONS-REGISTRY: {target_name} ({code_pfad}). Zweck: {spec_inhalt[:200]}. "
            f"Methoden: {', '.join(methoden) if methoden else '(keine gefunden)'}."
        )
        neuer_point_id = vdb.upsert(text, {"owner": "analyst", "memory_type": "system", "tags": "funktions_registry"})
        con.execute(
            "INSERT OR REPLACE INTO funktions_registry_punkte (target_name, point_id, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (target_name, neuer_point_id),
        )
        con.commit()
        return neuer_point_id
    finally:
        con.close()

from runtime_paths import WERFT_DB_PATH as DB_PATH
from runtime_paths import GENERATED_DIR
from role_dna import load_role_dna


def _deklarierte_abhaengigkeiten(task_id: str) -> list[str]:
    """Fund 2026-07-23 (Mentor, ehrliche-Ablehnung-Erkennung): liest dieselbe
    strategie_planung.abhaengigkeiten-Deklaration, die auch coder.py fuer das
    Grounding nutzt -- hier NUR die Dateinamen (nicht die volle Schnittstelle),
    um zu pruefen ob eine Coder-Ablehnung konkrete, tatsaechlich erwartete
    Abhaengigkeiten benennt statt vage zu sein."""
    con = sqlite3.connect(DB_PATH)
    try:
        bestellung_id = task_id.replace("task_bestellung_", "") if task_id.startswith("task_bestellung_") else None
        if not bestellung_id:
            return []
        row = con.execute(
            "SELECT kanonischer_name FROM bestellungen WHERE bestellung_id=?", (bestellung_id,)
        ).fetchone()
        if not row or not row[0]:
            return []
        planung = con.execute(
            "SELECT abhaengigkeiten FROM strategie_planung WHERE kanonischer_name=? ORDER BY id DESC LIMIT 1",
            (row[0],),
        ).fetchone()
        roh = planung[0] if planung and planung[0] else None
        if not roh:
            return []
        try:
            abhaengigkeiten = json.loads(roh)
        except json.JSONDecodeError:
            abhaengigkeiten = [roh]
        return abhaengigkeiten if isinstance(abhaengigkeiten, list) else []
    finally:
        con.close()


_IMPORT_MUSTER = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)


def _ist_installiertes_paket(name: str) -> bool:
    """Fund 2026-07-15 (beispielserie_teil3, Auditor-Hook): eine hardcodierte
    Allowlist bekannter Stdlib-Module ist strukturell unvollstaendig -- 'signal',
    'traceback' (Stdlib) und 'urllib3' (installiertes 3rd-Party) wurden faelschlich
    als 'fehlendes lokales Modul' abgelehnt, weil sie nicht in der Liste standen.
    4 Requeue-Runden verbraucht, BEVOR das auffiel. Robuster Ersatz: pruefe ueber
    Pythons eigenen Import-Mechanismus (find_spec), ob der Name UEBERHAUPT als
    Stdlib/installiertes Paket aufloesbar ist -- keine Liste mehr zu pflegen."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _fehlende_modul_referenzen(code: str, task_id: str) -> tuple[list[str], list[str]]:
    """Spezifikationsproblem 2026-07-15 (beispielserie_teil2_router_agent, Fund: Betreiber):
    ueber Bestellungs-Grenzen hinweg importiert generierter Code oft ein lokales
    Modul (z.B. 'configloader') unter einem sauberen Namen, das aber nur unter
    einem anderen, bestellungs-praefigierten Dateinamen existiert (z.B.
    'bestellung_beispielserie_teil1_configloader.py'). Die Analyst-Pruefung kannte
    bisher nur den Code selbst, nicht die tatsaechlich existierenden Dateien im
    Zielordner -- konnte das strukturell nie erkennen (narrative Pruefung ohne
    Sichtbarkeit auf die Historie).

    Gibt (fehlende_module, tatsaechlich_vorhandene_dateien) zurueck. Die zweite
    Liste ist die "Historie", mit der der Coder den Import korrigieren kann.
    """
    referenzen = {m for m in _IMPORT_MUSTER.findall(code) if not _ist_installiertes_paket(m)}
    if not referenzen:
        return [], []

    suchordner = [Path(__file__).parent, Path(__file__).parent / "generiert"]
    con = sqlite3.connect(DB_PATH)
    try:
        bestellung_id = task_id.replace("task_bestellung_", "") if task_id.startswith("task_bestellung_") else None
        if bestellung_id:
            row = con.execute(
                "SELECT ziel_ordner FROM bestellungen WHERE bestellung_id=?", (bestellung_id,)
            ).fetchone()
            if row and row[0]:
                suchordner.append(Path(row[0]))
    finally:
        con.close()

    vorhandene_dateien = []
    for ordner in suchordner:
        if ordner.is_dir():
            vorhandene_dateien += [p.name for p in ordner.glob("*.py")]
    vorhandene_module = {Path(n).stem for n in vorhandene_dateien}

    fehlend = [r for r in referenzen if r not in vorhandene_module]
    return fehlend, sorted(set(vorhandene_dateien))

DNA = """RICHTER-DNA: ARCHITEKTUR-REINHEIT.
1. ZERO HARDCODING: Keine URLs oder Modellnamen im geprueften Code.
2. CONFIG-PRIME: Alle Parameter muessen aus system_config kommen, nicht aus
   Konstanten im Code.
3. ROBUSTHEIT: Try-Except ist Pflicht fuer alle externen Aufrufe (API,
   Dateisystem, Netzwerk).
4. NON-INVENTION: Wenn eine Pruefung nicht eindeutig entscheidbar ist -- nicht
   raten. Als 'unsicher' markieren statt zu blockieren oder blind durchzuwinken.
5. AUGENMASS BEI ZERO-HARDCODING: Die Regel betrifft NUR Betriebs-Parameter
   (Modellnamen, URLs, Ports, Dateipfade zu Konfiguration, Schwellwerte).
   Woerterbuch-Schluessel, Feldnamen, Fachbegriffe im Code und Beispielwerte
   in einem __main__-Block sind KEIN Hardcoding-Verstoss. Nicht wegen solcher
   Details ablehnen (verifiziert 2026-07-10: 4 unnoetige Retry-Runden fuer
   einen trivialen Wortzaehler durch genau diese Ueberdehnung).
6. PFAD-PORTABILITAET (ergaenzt 2026-07-15, aus einem frueheren Mentor-Review):
   Absolute Pfade (fest verdrahtete Home-Verzeichnis-Strings) sind ein
   Ablehnungsgrund -- Pfade muessen paket-relativ zum Modulverzeichnis sein.
   Das gilt auch fuer Default-Werte bei Konfigurationszugriffen: ein Default
   muss ebenfalls paket-relativ aufgeloest werden, nicht gegen das aktuelle
   Arbeitsverzeichnis zur Laufzeit. WICHTIG: pruefe AUSSCHLIESSLICH den
   TATSAECHLICH VORLIEGENDEN Code-Text auf diese Regel -- nicht ob der
   Zielname dieser Bestellung frueher schonmal einen Pfad-Fehler hatte, und
   nicht anhand von Beispiel-Mustern aus dieser Anweisung. Ein Ziel, dessen
   Pfade bereits alle paket-relativ sind, ist bestanden, auch wenn dasselbe
   Ziel in einer frueheren Runde abgelehnt wurde.
   KRITISCH (Fund 2026-07-19, beispiel_charakter_serie_teil2, 8 von 8 Ablehnungen
   desselben Musters, davon mehrere die woertlich ein Beispiel-Codeschnipsel
   aus DIESER Regel-Beschreibung zitierten, obwohl es im geprueften Code gar
   nicht vorkam): zitiere im "grund"-Feld NUR Code, der wortwoertlich im
   CODE-Abschnitt oben steht. Wenn du unsicher bist, ob ein Muster im Code
   vorkommt: suche danach, bevor du es als Ablehnungsgrund nennst. Enthaelt
   dein eigener "grund"-Text einen Widerspruch (z.B. "der Code ist korrekt,
   ABER..." gefolgt von einer hypothetischen Problematik) -- dann ist das
   ein Selbstwiderspruch, KEIN echter Verstoss, und pass MUSS true sein.
7. KEINE SEITENEFFEKTE BEIM MODUL-IMPORT (ergaenzt 2026-07-15, selbe Quelle):
   Eine Modul-Ebene-Zeile wie "x = MeineKlasse()" darf NICHT bei Konstruktion
   bereits DB-/Netzwerk-Zugriffe ausloesen. Pruefe: wuerde "import <modul>"
   allein (ohne jeden Methodenaufruf) fehlschlagen, wenn eine externe Ressource
   (DB-Tabelle, Datei, Netzwerk) noch nicht existiert? Wenn ja: ablehnen, Grund
   "Lazy Loading noetig -- DB-Zugriff gehoert in die erste Methode, nicht in
   __init__/Modul-Ebene."
"""
DNA = load_role_dna("analyst", fallback=DNA)

PROMPT_TEMPLATE = """{dna}

Du bist der Analyst. Pruefe folgenden Code gegen die Spezifikation.

SPEZIFIKATION:
{spec_inhalt}

CODE:
{generierter_code}

Antworte NUR mit einem JSON-Objekt:
{{"pass": true/false, "grund": "<konkrete Begruendung, die dem Programmierer sagt
was genau zu aendern ist -- mindestens 1 Satz, keine Floskel wie 'passt nicht'>"}}

WICHTIG (Fund 2026-07-17: haeufigster Grund fuer kaputtes JSON): wenn du im "grund"-Text
Code-Ausschnitte oder Variablennamen zitierst, die selbst doppelte Anfuehrungszeichen
enthalten (z.B. config.get("KEY", "default")), verwende dafuer AUSSCHLIESSLICH einfache
Anfuehrungszeichen oder Backticks (z.B. config.get('KEY', 'default') oder
`config.get(...)`) -- NIEMALS unescapte doppelte Anfuehrungszeichen innerhalb des
JSON-String-Werts, das macht die gesamte Antwort unparsbar.

Lehne nur ab wenn der Code die Spezifikation eindeutig verfehlt (Skeleton-Code,
fehlende Kernfunktion, offensichtliche Platzhalter). Bei vager Spezifikation und
plausiblem Code: pass=true (fail-open -- besser ein brauchbarer erster Versuch
als ein Endlos-Loop).
"""


def run_once() -> bool:
    """Holt einen Task mit status='code_ready', fuehrt die Taufe-Pruefung durch,
    setzt status='merged' oder 'spec_failed' (mit strukturell abgesicherter
    Begruendung, siehe memory.py)."""
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT task_id, knowledge_links FROM task_pipeline WHERE status='code_ready' LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        task_id, knowledge_links = row
        target_name = task_id.replace("task_", "")

        # Fund 2026-08-05 (Mentor): rsplit-bis-Stringende nahm auch
        # nachtraeglich angehaengten Text (z.B. Archivierungs-Notizen) als
        # Teil des Pfads -- siehe debugger.py::_code_pfad_aus_knowledge_links
        # fuer denselben Fix. Letztes Vorkommen gewinnt weiterhin (Retries
        # haengen weitere CODE_PFAD: an -- der juengste Code-Stand zaehlt).
        pfad_treffer = re.findall(r"CODE_PFAD:([^\s|]+)", knowledge_links or "")
        code_pfad = pfad_treffer[-1] if pfad_treffer else None
        if not code_pfad:
            # Fund 2026-08-25 (Mentor, live diagnostiziert): dieser Fall
            # (status='code_ready' OHNE je einen CODE_PFAD-Marker bekommen zu
            # haben -- z.B. eine Planer-Spec, die versehentlich direkt auf
            # code_ready gesetzt wurde, ohne dass der Coder je lief) gab bisher
            # STILL 'return False' zurueck -- kein remember_system(), kein
            # Statuswechsel, KEIN Log-Eintrag. Da run_once() ohne ORDER BY
            # immer dieselbe (aelteste) code_ready-Zeile zuerst zieht, hat
            # GENAU DIESER stille Fehlschlag die GESAMTE Analyst-Warteschlange
            # dahinter blockiert -- 4 weitere, laengst fertige Tasks
            # (u.a. specgen_49c875842a) kamen deshalb nie an die Reihe, ohne
            # jede sichtbare Fehlermeldung. Jetzt wie der bereits vorhandene
            # 'leerer Code'-Fall behandelt: strukturelle Ablehnung MIT Log,
            # spec_failed statt endlosem stillen Blockieren derselben Zeile.
            remember_system(
                owner="analyst",
                target=target_name,
                content=(
                    f"Kein CODE_PFAD-Marker in knowledge_links gefunden -- dieser Task "
                    f"stand auf 'code_ready', obwohl nie ein Coder-Lauf einen Code-Pfad "
                    f"eingetragen hat (z.B. eine Planer-Spec, die faelschlich direkt auf "
                    f"code_ready gesetzt wurde). Strukturelle Ablehnung ohne LLM-Urteil, "
                    f"muss neu durch coder.py laufen."
                ),
                tags="content_check_fail,fehlender_code_pfad",
            )
            con.execute("UPDATE task_pipeline SET status='spec_failed' WHERE task_id=?", (task_id,))
            con.commit()
            return True
        code_datei = GENERATED_DIR / Path(code_pfad).name
        generierter_code = code_datei.read_text(encoding="utf-8") if code_datei.exists() else ""

        # Harte Sperre VOR dem LLM-Urteil: leerer/fehlender Code darf nie durch
        # fail-open mergen (real passiert 2026-07-10: doppelter CODE_PFAD-Marker
        # -> nicht-existenter Pfad -> leerer Code -> falsches pass=true).
        if not generierter_code.strip():
            remember_system(
                owner="analyst",
                target=target_name,
                content=f"Code-Datei fehlt oder ist leer (Pfad: {code_pfad}) -- "
                        f"strukturelle Ablehnung ohne LLM-Urteil, Coder muss neu generieren.",
                tags="content_check_fail,leerer_code",
            )
            con.execute("UPDATE task_pipeline SET status='spec_failed' WHERE task_id=?", (task_id,))
            con.commit()
            return True

        # Harte Sperre VOR dem LLM-Urteil: begruendete ehrliche Ablehnung erkennen
        # und an den Mentor eskalieren statt sie wie gewoehnlichen Muell zu behandeln.
        #
        # Fund 2026-07-23 (Mentor, Selbstbau-Experiment Variante B): der Coder
        # antwortete auf eine Bestellung, deren Grounding technisch fehlte, EHRLICH
        # ("Um die Spezifikation zu erstellen, benoetige ich Zugriff auf db_setup.py,
        # coder.py, ...") statt zu konfabulieren -- genau das gewuenschte Verhalten
        # aus der KI-Psychologie-Hypothese ("Ich weiss es nicht" als vollwertiges,
        # kein degradiertes Ergebnis). Die Pipeline behandelte das aber wie jeden
        # anderen Fehlschlag und liess es mechanisch bis zur Archivierung retryen --
        # kein Weg, eine ehrliche Ablehnung als GUTES Signal zu erkennen.
        #
        # Betreibers Auftrag (23.07., Tisch, "kannst du den ich-weiss-es-nicht-Fix
        # einplanen? mit Begruendung, damit das Modell es nicht zur Standardantwort
        # macht!"): die Erkennung MUSS an eine konkrete Begruendung gebunden sein --
        # eine vage "kann ich nicht" OHNE Nennung eines tatsaechlich erwarteten,
        # konkreten Elements zaehlt weiterhin als normaler Fehlschlag (faellt durch
        # zum LLM-Urteil unten), sonst waere "ich weiss es nicht" ein bequemer
        # Freifahrtschein statt einer ehrlichen, gepruefeten Aussage.
        ablehnungs_muster = (
            "benötige ich zugriff", "benoetige ich zugriff", "kann ich nicht generieren",
            "kann ich keine", "i don't have access", "i can't generate", "i cannot generate",
            "cannot provide", "ohne diese dateien", "ohne zugriff auf",
        )
        code_lower_fuer_ablehnung = generierter_code.lower()
        wirkt_wie_ablehnung = (
            len(generierter_code) < 1000  # echte Deliverables sind laenger
            and any(m in code_lower_fuer_ablehnung for m in ablehnungs_muster)
        )
        if wirkt_wie_ablehnung:
            deklarierte_abhaengigkeiten = _deklarierte_abhaengigkeiten(task_id)
            konkret_genannt = [
                d for d in deklarierte_abhaengigkeiten if d.lower() in code_lower_fuer_ablehnung
            ]
            if konkret_genannt:
                remember_system(
                    owner="analyst",
                    target=target_name,
                    content=(
                        f"MENTOR-ESKALATION: begruendete ehrliche Ablehnung erkannt "
                        f"(kein Konfabulieren) -- der Coder nennt konkret fehlende, "
                        f"tatsaechlich deklarierte Abhaengigkeiten ({konkret_genannt}) "
                        f"statt zu raten. Wortlaut: '{generierter_code.strip()[:400]}'. "
                        f"Keine automatische Korrekturrunde -- braucht Mentor-Entscheidung "
                        f"ob/wie das Grounding nachgebessert wird."
                    ),
                    tags="mentor_eskalation,ehrliche_ablehnung_begruendet",
                )
                con.execute("UPDATE task_pipeline SET status='spec_failed' WHERE task_id=?", (task_id,))
                con.commit()
                return True
            # Sieht aus wie eine Ablehnung, nennt aber NICHTS konkret Erwartetes --
            # bewusst NICHT privilegieren, faellt durch zum normalen LLM-Urteil,
            # das es voraussichtlich (und zurecht) ablehnt.

        # Harte Sperre VOR dem LLM-Urteil: Modul-Referenzen gegen die tatsaechlich
        # existierenden Dateien pruefen (Spezifikationsproblem 2026-07-15, Betreiber-Fund).
        fehlende_module, vorhandene_dateien = _fehlende_modul_referenzen(generierter_code, task_id)
        if fehlende_module:
            remember_system(
                owner="analyst",
                target=target_name,
                content=(
                    f"Import-Ziel(e) {fehlende_module} existieren nicht als Datei im "
                    f"Zielordner -- strukturelle Ablehnung ohne LLM-Urteil. Tatsaechlich "
                    f"vorhandene Module (Historie, zur Korrektur des Imports nutzen): "
                    f"{vorhandene_dateien or 'keine gefunden'}."
                ),
                tags="content_check_fail,modul_referenz_fehlt",
            )
            con.execute("UPDATE task_pipeline SET status='spec_failed' WHERE task_id=?", (task_id,))
            con.commit()
            return True

        spec_marker = "SPEC_JSON:"
        # Fund 2026-08-24 (Mentor, live im Coder-DoE-Testlauf gefunden):
        # `code_marker` war nirgends definiert -- NameError bei JEDEM Analyst-
        # Zyklus. coder.py verwendet an derselben Stelle (Zeile 718) denselben
        # Split OHNE zweiten Marker -- hier analog korrigiert, kein neues
        # Trennzeichen noetig.
        spec_inhalt = knowledge_links.split(spec_marker, 1)[-1] if spec_marker in knowledge_links else knowledge_links

        prompt = PROMPT_TEMPLATE.format(dna=DNA, spec_inhalt=spec_inhalt, generierter_code=generierter_code)
        antwort = ask_model(
            prompt,
            model=config.get("ollama.model.analyst", "qwen3:14b"),
            temperature=0.0,
            host=host_fuer_rolle("analyst"),
        )
        verdikt = extrahiere_json_objekt(antwort)

        # Fund 2026-07-20 (Scout-Portierung, live beobachtet): DNA-Regel 6
        # verlangt vom Analyst-Modell selbst, einen Selbstwiderspruch im
        # eigenen "grund"-Text zu erkennen und dann pass=true zu setzen --
        # qwen3:14b haelt sich trotz temperature=0.0 nicht zuverlaessig daran
        # (3x live reproduziert: "COLLECTION ist bereits korrekt auf 'foo'
        # gesetzt, ABER muss auf 'foo' geaendert werden" -> pass=false).
        # Deterministisches Sicherheitsnetz statt weiterem Vertrauen auf
        # Prompt-Befolgung: typische Selbstwiderspruchs-Formulierungen im
        # "grund"-Text direkt erkennen, unabhaengig vom Modell-Urteil.
        if not verdikt.get("pass") and verdikt.get("grund"):
            grund_lower = verdikt["grund"].lower()
            widerspruchs_muster = ("bereits korrekt", "ist bereits", "ist korrekt", "bereits richtig")
            forderungs_muster = ("muss", "sollte", "geändert werden", "geaendert werden")
            if any(w in grund_lower for w in widerspruchs_muster) and any(f in grund_lower for f in forderungs_muster):
                remember_system(
                    owner="analyst",
                    target=target_name,
                    content=f"Selbstwiderspruch im Analyst-Urteil deterministisch abgefangen "
                            f"(Modell sagte pass=false trotz Selbstwiderspruch im Grund-Text): "
                            f"'{verdikt['grund']}' -- automatisch auf pass=true korrigiert.",
                    tags="analyst_selbstwiderspruch_abgefangen",
                )
                verdikt["pass"] = True

        # Fund 2026-07-23 (Mentor, live reproduziert an tisch_archivierer_stufe1
        # UND bereits einmal zuvor bei schwarzes_brett_bearbeiter.py, 22.07.):
        # qwen3:14b lehnt wiederholt (6x in Folge bei diesem Task) Code ab, der
        # DNA-Regel 4 (Path(__file__).parent-relative Pfade) KORREKT befolgt --
        # mit der Begruendung, das erzeuge einen "nicht portablen absoluten Pfad".
        # Kategorie-Fehler des Modells: Path(__file__).parent-basierte Pfade SIND
        # absolut UND das ist genau richtig/gewollt (portabel heisst hier "haengt
        # am Modul-Ort", nicht "ist relativ"). Reine Prompt-Klarstellung hat das
        # bisher nicht zuverlaessig verhindert (DNA-Text war schon eindeutig).
        # Deterministisches Sicherheitsnetz nach demselben Muster wie der
        # Selbstwiderspruchs-Check oben: wenn die Ablehnung auf "Pfad" +
        # "absolut"/"portabel"/"paket-relativ" abzielt UND der Code selbst
        # tatsaechlich korrekt Path(__file__).parent nutzt (kein echtes
        # os.path.expanduser/nackter relativer String als Default), automatisch
        # auf pass=true korrigieren statt weiterer Korrekturrunden.
        if not verdikt.get("pass") and verdikt.get("grund"):
            grund_lower = verdikt["grund"].lower()
            pfad_muster = ("pfad-portabilit", "portabler pfad", "nicht portabel", "paket-relativ", "absoluten pfad")
            code_lower = generierter_code.lower()
            if (
                any(p in grund_lower for p in pfad_muster)
                and "path(__file__).parent" in code_lower
                and "os.path.expanduser" not in code_lower
            ):
                remember_system(
                    owner="analyst",
                    target=target_name,
                    content=f"Bekannter Analyst-Fehlalarm bei Path(__file__).parent deterministisch "
                            f"abgefangen (Code nutzt korrekt DNA-Regel 4, Modell lehnte trotzdem "
                            f"wegen 'Pfad-Portabilitaet' ab): '{verdikt['grund']}' -- automatisch "
                            f"auf pass=true korrigiert.",
                    tags="analyst_pfad_fehlalarm_abgefangen",
                )
                verdikt["pass"] = True

        if verdikt.get("pass"):
            con.execute("UPDATE task_pipeline SET status='merged' WHERE task_id=?", (task_id,))
            # Taufe ist Pruefung UND Registrierung (Neustart-Spec §1, Lifecycle
            # Embryo -> Audit -> Taufe -> Registrierung in specialists_v8).
            #
            # Fund 2026-07-17 (Betreiber-Auftrag, Phase-2-Routing-Faehigkeit): bisher
            # wurde IMMER target_name (interner Bestellungs-Name, z.B.
            # "bestellung_beispielserie_teil7") + der transiente generiert/-
            # Pfad (wird nach Auslieferung ueberschrieben/geloescht) registriert
            # -- fuer echtes Spezialisten-Routing (RouterAgent soll den Ziel-
            # Spezialisten tatsaechlich aufrufen koennen) unbrauchbar, da weder
            # Name noch Pfad stabil/kanonisch sind. Falls die Bestellung einen
            # kanonischer_name hat (Systemmap-Teil), diesen + den ECHTEN
            # Ziel-Pfad registrieren; sonst Fallback auf bisheriges Verhalten
            # (nicht jede Bestellung ist ein wiederverwendbarer Spezialist).
            registry_name, registry_pfad = target_name, code_pfad
            bestellung_id = task_id.replace("task_bestellung_", "") if task_id.startswith("task_bestellung_") else None
            if bestellung_id:
                row = con.execute(
                    "SELECT ziel_ordner, kanonischer_name FROM bestellungen WHERE bestellung_id=?",
                    (bestellung_id,),
                ).fetchone()
                if row and row[1]:
                    registry_name = row[1]
                    registry_pfad = str(Path(row[0]) / row[1])
            con.execute(
                "INSERT OR REPLACE INTO specialists_v8 (name, role_description, module_path, active) "
                "VALUES (?, ?, ?, 1)",
                (registry_name, spec_inhalt[:300], registry_pfad),
            )
            # Commit VOR _registriere_funktions_registry(): die Funktion oeffnet
            # eine EIGENE sqlite3-Connection -- solange `con` hier eine offene
            # Schreib-Transaktion haelt, blockiert das die zweite Connection
            # ("database is locked", live reproduziert 2026-07-15, dieselbe
            # Kategorie Bug wie zuvor in debugger.py._melde_befund()).
            con.commit()
            _registriere_funktions_registry(target_name, spec_inhalt, generierter_code, code_pfad)
            if bestellung_id and row and row[1]:
                _aktualisiere_systemmap(row[0], row[1], generierter_code, bestellung_id)
                archive_product(row[1], code_datei, spec_inhalt, bestellung_id)
                # Touchpoint 3 (call_graph_kanten, Betreiber-Auftrag ueber Mentor,
                # 2026-08-26): nur an dieser Stelle, direkt neben der Systemmap-
                # Aktualisierung -- gleiche Voraussetzung (kanonischer Name
                # bekannt), gleicher Erfolgsfall (echter Merge).
                _finalisiere_call_graph_kanten(con, row[1], generierter_code, bestellung_id)
        else:
            grund = verdikt.get("grund", "")
            try:
                remember_system(
                    owner="analyst",
                    target=target_name,
                    content=grund,
                    tags="content_check_fail",
                )
            except BegruendungZuKurzError:
                # Analyst hat keine brauchbare Begruendung geliefert -- das ist ein
                # Analyst-Fehler, kein gueltiges Verdikt. Nicht als spec_failed
                # eintragen (das waere eine leere Ablehnung wie im alten System),
                # sondern Task auf 'audit' zurueck fuer einen neuen Pruefversuch.
                con.execute("UPDATE task_pipeline SET status='audit' WHERE task_id=?", (task_id,))
                con.commit()
                return True
            con.execute("UPDATE task_pipeline SET status='spec_failed' WHERE task_id=?", (task_id,))
        con.commit()
        return True
    finally:
        con.close()


if __name__ == "__main__":
    bearbeitet = run_once()
    print("Task bearbeitet" if bearbeitet else "Keine offenen code_ready-Tasks")
