from __future__ import annotations

"""Bestellungs-Verarbeitung (WERFT_NEUSTART_SPEZIFIKATION.md Abschnitt 12.1).

Eine Bestellung ist eine externe Spezifikation ("baue X"), die die Werft wie
einen Gap behandelt: die komplette Spezifikation wird als Einstiegs-Task
eingebettet und durchlaeuft denselben Zyklus (audit -> spec -> code_ready ->
merged) wie ein interner Gap-Fix. Nach vollstaendigem Merge wird das Ergebnis
nach ziel_ordner ausgeliefert.

Bewusst KEINE inhaltliche Pruefung WAS bestellt wird (Abschnitt 12.1: "Die
Werft prueft nicht inhaltlich ... nur OB die Bestellung eindeutig genug ist").
"""
import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

from configloader import config
from memory import remember_system
from spec_formular import SpecFormular
from vdb_client import vdb
import tisch_client

from runtime_paths import GENERATED_DIR, WERFT_DB_PATH as DB_PATH
from external_orders import write_result

_KLASSE_SPEC_MUSTER = re.compile(r"KLASSE:\s*(\w+)", re.IGNORECASE)
_PY_DATEI_MUSTER = re.compile(r"\b(\w+\.py)\b")
_FUNKTIONS_REGISTRY_MUSTER = re.compile(r"^FUNKTIONS-REGISTRY:\s*(\S+)\s*\(([^)]+)\)")

# Betreiber-Auftrag 2026-08-04 (nach dem Hypothesengenerator-Korrektur-Vorfall:
# "der Zielname sollte nicht als Auftrag da stehen, die Werft sollte ihn
# selbststaendig finden -- nur bei Unsicherheit eine Nachfrage stellen").
# Kalibriert an einem echten Fall (hypothesengenerator-Korrektur-Spec gegen
# werft_wissen getestet, 2026-08-04): der KORREKTE Treffer lag bei Score
# 0.699, ein VOELLIG UNVERWANDTER Eintrag (reine Wort-Ueberlappung ueber
# "embed"/"Kontextfenster") lag mit 0.719 sogar DAVOR -- eine reine
# Top-1-Schwelle haette hier das falsche Modul automatisch gewaehlt. Deshalb
# bewusst KEIN reiner Schwellenwert, sondern zusaetzlich ein Mindestabstand
# zum Zweitplatzierten -- ohne klaren Abstand gilt der Fall als unsicher.
_ZIEL_ERKENNUNG_MIN_SCORE = 0.65
_ZIEL_ERKENNUNG_MIN_ABSTAND = 0.05

# Ablauf-Score (Stufe 1, Methodennamen-Mengenschnitt) liegt auf einer anderen
# Skala als der Embedding-Score (Anteil TATSAECHLICH modul-eigener Methoden,
# nicht Kosinus-Aehnlichkeit von Prosa) -- eigene Schwellen noetig. Kalibriert
# am selben Realfall: hypothesengenerator.py traf mit 2/5 Methoden (Score
# 0.40) klar und mit grossem Abstand (naechster Kandidat 0.25) -- mindestens
# 2 TATSAECHLICH modul-eigene Treffer (nicht nur ein generischer Name wie
# "beantworte", den mehrere Spezialisten teilen) plus deutlicher Abstand.
_ABLAUF_MIN_TREFFER = 2
_ABLAUF_MIN_ABSTAND = 0.10


_METHODENNAME_MUSTER = re.compile(r"(\w+)\(")


def _ablauf_kandidaten(spec_inhalt: str, con: sqlite3.Connection, top_k: int = 3) -> list[dict]:
    """Betreiber-Einwand 2026-08-04 (nach dem Embedding-Fehltreffer): "eine reine
    Wortueberlappung produziert ein so hohes Trefferergebnis? Nicht der
    Ablauf?" -- nomic-embed-text misst thematische/lexikalische Naehe von
    Fliesstext, NICHT strukturelle Aehnlichkeit des tatsaechlichen Ablaufs.
    Zwei Texte, die beide ueber "Embedding"/"Kontextfenster" REDEN, clustern
    nah zusammen, auch wenn die beschriebenen Ablaeufe (HTTP-Fehlerbehandlung
    vs. Kandidatensuche) nichts miteinander zu tun haben (live reproduziert).

    Deterministischer Ersatz: `systemmap.methoden` enthaelt die ECHTEN
    Methodennamen jedes Moduls. Ein Spec-Text, der eine Korrektur beschreibt,
    nennt ueblicherweise die betroffenen Methoden woertlich (z.B.
    "_hole_konzept_kontext() ..."). Abgleich per Mengenschnitt statt
    Embedding-Distanz -- kein LLM, kein Embedding-Aufruf, exakt nachvollziehbar
    (Score = Anteil der modul-eigenen Methoden, die im Spec-Text vorkommen).

    Gibt eine Liste von {"kanonischer_name", "ziel_ordner", "score",
    "treffer_methoden"} zurueck, hoechster Score zuerst, leer wenn kein
    Modul mindestens eine erwaehnte Methode hat."""
    kandidaten = []
    # Fund 2026-08-26 (Mentor): Selbstbau-Experiment-Dateien (werft_kern_nachbau*.py)
    # sind Labor-Artefakte, keine echten Werft-Bausteine -- ohne Ausschluss waeren sie
    # trotzdem valide Ziel-Kandidaten fuer voellig unabhaengige Bestellungen (live fast
    # passiert bei specgen_4779e4aa0d, Score 0.133, nur knapp unter dem, was Stufe 1
    # bei quelle='ablauf' als min_score=0.0 durchgehen laesst -- siehe eingang_schritt()).
    for kanonischer_name, ziel_ordner, methoden_json in con.execute(
        "SELECT kanonischer_name, ziel_ordner, methoden FROM systemmap "
        "WHERE methoden IS NOT NULL AND ziel_ordner NOT LIKE '%/selbstbau_test/%'"
    ):
        try:
            methoden = json.loads(methoden_json) if methoden_json else []
        except (TypeError, json.JSONDecodeError):
            continue
        eigene_methoden = {m2.group(1) for m in methoden if (m2 := _METHODENNAME_MUSTER.match(m))}
        if not eigene_methoden:
            continue
        treffer = {
            n for n in eigene_methoden
            if re.search(rf"\b{re.escape(n)}\b", spec_inhalt)
        }
        # Mindestens _ABLAUF_MIN_TREFFER echte Treffer -- ein einzelner,
        # generischer Methodenname (z.B. "beantworte", den mehrere
        # Spezialisten teilen) reicht nicht als Beleg fuer DIESES Modul.
        if len(treffer) < _ABLAUF_MIN_TREFFER:
            continue
        score = len(treffer) / len(eigene_methoden)
        kandidaten.append({
            "kanonischer_name": kanonischer_name, "ziel_ordner": ziel_ordner,
            "score": score, "treffer_methoden": sorted(treffer), "quelle": "ablauf",
        })
    kandidaten.sort(key=lambda k: -k["score"])
    return kandidaten[:top_k]


def _erkenne_bestehendes_ziel_kandidaten(spec_inhalt: str, top_k: int = 3) -> list[dict]:
    """Findet bereits gelieferte Module, die zu einer neuen Bestellung passen
    koennten -- ZWEISTUFIG (Betreiber-Auftrag 2026-08-04, "Ablauf statt reiner
    Wortueberlappung"):

    Stufe 1 (bevorzugt, deterministisch): _ablauf_kandidaten() -- Abgleich
    der im Spec-Text woertlich genannten Methodennamen gegen systemmap.
    Praeziser als Embedding-Distanz, weil er die tatsaechliche Schnittstelle
    vergleicht statt Prosa-Themennaehe (siehe dortiger Docstring fuer den
    Live-Fund, der das ausgeloest hat).

    Stufe 2 (Fallback, nur wenn Stufe 1 nichts findet): semantische Suche
    gegen werft_wissen (Funktions-Registry-Eintraege, siehe
    analyst.py::_registriere_funktions_registry) -- fuer Faelle ohne
    Methodennamen im Spec-Text (z.B. eine Bestellung, die nur den Zweck
    beschreibt). Der dort gespeicherte code_pfad ist der transiente
    generiert/bestellung_<id>.py-Archivpfad, wird ueber systemmap.
    bestellung_id auf den echten kanonischen Namen aufgeloest.

    Gibt eine Liste von {"kanonischer_name", "ziel_ordner", "score", ...}
    zurueck, hoechster Score zuerst, leer wenn nichts gefunden."""
    con = sqlite3.connect(DB_PATH)
    try:
        ablauf = _ablauf_kandidaten(spec_inhalt, con, top_k=top_k)
        if ablauf:
            return ablauf

        treffer = vdb.search(spec_inhalt, top_k=top_k * 4)
        kandidaten = []
        gesehen = set()
        for t in treffer:
            m = _FUNKTIONS_REGISTRY_MUSTER.match(t["text"])
            if not m:
                continue
            target_name = m.group(1)
            bestellung_id = target_name[len("bestellung_"):] if target_name.startswith("bestellung_") else target_name
            row = con.execute(
                "SELECT kanonischer_name, ziel_ordner FROM systemmap WHERE bestellung_id=? "
                "ORDER BY aktualisiert_am DESC LIMIT 1",
                (bestellung_id,),
            ).fetchone()
            if not row or not row[0]:
                continue  # kein aufloesbarer kanonischer Name -- kein brauchbarer Kandidat
            kanonischer_name, ziel_ordner = row
            if kanonischer_name in gesehen:
                continue
            gesehen.add(kanonischer_name)
            kandidaten.append({
                "kanonischer_name": kanonischer_name, "ziel_ordner": ziel_ordner,
                "score": t["score"], "quelle": "embedding",
            })
            if len(kandidaten) >= top_k:
                break
        return kandidaten
    finally:
        con.close()


def _sicherstellen_formular_spalte(con: sqlite3.Connection) -> None:
    """Idempotente Spalten-Migration (kein ALTER-Vorbild im Repo -- Standard-
    sqlite3-Idiom: Duplicate-Column-Fehler abfangen statt vorher zu pruefen)."""
    try:
        con.execute("ALTER TABLE bestellungen ADD COLUMN formular_vorschlag TEXT")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def _plane_aus_spec(con: sqlite3.Connection, bestellung_id: str, kanonischer_name: str | None, inhalt: str) -> None:
    """Fund 2026-07-21 (Betreiber-Auftrag, Punkte 2+3 einer 5-teiligen Automatisierungs-
    Liste, nach der Economic-Scout-Klassennamen-Regression): der Klassenname aus
    dem Spec-Freitext ("KLASSE: X") war bisher nur eine unverbindliche Empfehlung
    -- ohne strategie_planung-Eintrag faellt der Coder manchmal auf die naive
    Namensableitung zurueck (live reproduziert). Ausserdem gab es KEINEN Abgleich
    einer neuen Bestellung gegen bereits bestehende, in der systemmap-Tabelle
    dokumentierte Module VOR dem Bauen (Betreiber: "Stratege hat keine Systemmap
    erstellt oder mit einer bestehenden abgeglichen, das ist ein echter Mangel").
    Diese Funktion macht bei JEDER neuen Bestellung automatisch beides: Klassen-
    namen aus der Spec extrahieren + verbindlich in strategie_planung eintragen,
    UND im Spec-Text erwaehnte .py-Dateien gegen die systemmap-Tabelle abgleichen
    (als abhaengigkeiten eingetragen, damit coder.py::_abhaengigkeits_schnittstellen()
    sie beim Bauen automatisch beruecksichtigt, statt dass ich als Mentor jede
    Abhaengigkeit manuell im Freitext wiederholen muss)."""
    if not kanonischer_name:
        return

    klasse_match = _KLASSE_SPEC_MUSTER.search(inhalt)
    geplante_klasse = klasse_match.group(1) if klasse_match else None

    erwaehnte_dateien = set(_PY_DATEI_MUSTER.findall(inhalt)) - {kanonischer_name}
    bekannte_module = {
        row[0] for row in con.execute("SELECT DISTINCT kanonischer_name FROM systemmap").fetchall()
    }
    abhaengigkeiten = sorted(erwaehnte_dateien & bekannte_module)

    if not geplante_klasse and not abhaengigkeiten:
        return  # nichts Verwertbares gefunden -- kein Eintrag noetig

    con.execute(
        "INSERT INTO strategie_planung (ueber_bestellung, teil_nr, kanonischer_name, "
        "geplante_klasse, abhaengigkeiten, status) VALUES (?, 1, ?, ?, ?, 'geplant')",
        (bestellung_id, kanonischer_name, geplante_klasse, json.dumps(abhaengigkeiten, ensure_ascii=False)),
    )


def bestellung_korrigieren(bestellung_id: str, neuer_inhalt: str) -> dict:
    """Korrigiert eine bereits eingegangene Bestellung NACH eingang_schritt().

    Fund 2026-07-29 (live zweimal reproduziert, Vorfall bei einem Zielprojekt):
    ein blosses `UPDATE bestellungen SET spec_inhalt=...` wirkt NICHT auf den
    laufenden Bau, weil eingang_schritt() den Spec-Text schon beim Intake
    wortwoertlich in `task_pipeline.knowledge_links` einfriert
    (`f"BESTELLUNG:{bestellung_id} | {titel} | {inhalt}"`) -- der Coder liest
    diese eingefrorene Kopie, nie mehr `bestellungen.spec_inhalt` direkt. Eine
    Korrektur an der Bestellungs-Zeile allein ist deshalb wirkungslos, wirkt
    aber wie eine wirksame Korrektur (kein Fehler, kein Hinweis) -- genau das
    hat in der Nacht vom 28./29.07. zu zwei aufeinanderfolgenden Fehllieferungen
    gefuehrt (specgen_8da70b3e9b), obwohl die DB-Zeile korrekt aktualisiert war.

    Diese Funktion aktualisiert beide Stellen synchron und stoesst den
    zugehoerigen Task erneut ab Stufe 'audit' an (Re-Bau aus dem korrigierten
    Text), unabhaengig davon ob der Task noch in Bau oder bereits 'geliefert'
    ist -- wer korrigiert, will dass der NEUE Inhalt tatsaechlich gebaut wird,
    nicht nur dass die DB-Zeile sauber aussieht.

    Returns ein dict mit `gefunden` (bool) und `vorheriger_status` (str|None)
    des Tasks, damit der Aufrufer sieht ob wirklich etwas korrigiert wurde.
    """
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT titel, kanonischer_name FROM bestellungen WHERE bestellung_id=?",
            (bestellung_id,),
        ).fetchone()
        if not row:
            return {"gefunden": False, "vorheriger_status": None}
        titel, kanonischer_name = row

        con.execute(
            "UPDATE bestellungen SET spec_inhalt=? WHERE bestellung_id=?",
            (neuer_inhalt, bestellung_id),
        )
        # Fund 2026-08-28 (Mentor, live durch 2te-instanz-a7s Beobachtung entdeckt):
        # bei einer Korrektur an einer bereits 'geliefert'en Bestellung (Nachbesserung
        # nach Auslieferung, z.B. ein zu schwaches Abnahmekriterium wurde nachtraeglich
        # verschaerft) blieb bestellungen.status auf 'geliefert' stehen, obwohl der
        # Docstring genau diesen Fall ausdruecklich abdecken soll ("unabhaengig davon
        # ob der Task noch in Bau oder bereits 'geliefert' ist"). auslieferung_schritt()
        # verarbeitet aber NUR status='in_bau' -- ohne diesen Reset haette der neu
        # gebaute, korrigierte Code NIE den Weg in ziel_ordner gefunden, selbst wenn
        # der Task erneut sauber auf 'merged' laeuft. Zurueck auf 'in_bau' setzen,
        # damit die naechste erfolgreiche Fertigstellung wieder ausgeliefert wird.
        con.execute(
            "UPDATE bestellungen SET status='in_bau' WHERE bestellung_id=? AND status='geliefert'",
            (bestellung_id,),
        )

        task_id = f"task_bestellung_{bestellung_id}"
        task_row = con.execute(
            "SELECT status FROM task_pipeline WHERE task_id=?", (task_id,)
        ).fetchone()
        vorheriger_status = task_row[0] if task_row else None

        if task_row:
            con.execute(
                "UPDATE task_pipeline SET status='audit', knowledge_links=? WHERE task_id=?",
                (f"BESTELLUNG:{bestellung_id} | {titel} | {neuer_inhalt}", task_id),
            )

        # Veraltete Planung (Klassenname/Abhaengigkeiten aus dem ALTEN Text)
        # verwerfen und aus dem korrigierten Text neu ableiten.
        con.execute(
            "DELETE FROM strategie_planung WHERE ueber_bestellung=?", (bestellung_id,)
        )
        _plane_aus_spec(con, bestellung_id, kanonischer_name, neuer_inhalt)

        con.commit()
        remember_system(
            owner="mentor", target=bestellung_id,
            content=(
                f"BESTELLUNG-KORREKTUR fuer '{titel}' ({bestellung_id}): "
                f"spec_inhalt UND task_pipeline.knowledge_links synchron "
                f"aktualisiert, Task zurueck auf 'audit' (vorher: "
                f"{vorheriger_status or 'kein Task'}). Planung neu abgeleitet."
            ),
            tags="mentor,bestellung_korrektur",
        )
        return {"gefunden": bool(task_row), "vorheriger_status": vorheriger_status}
    finally:
        con.close()


def eingang_schritt() -> int:
    """Legt fuer jede neue Bestellung (status='eingegangen') genau einen
    Einstiegs-Task an und setzt status='in_bau'. Gibt die Anzahl neu
    angelegter Bestellungs-Tasks zurueck."""
    con = sqlite3.connect(DB_PATH)
    try:
        _sicherstellen_formular_spalte(con)
        rows = con.execute(
            "SELECT bestellung_id, titel, spec_pfad, spec_inhalt, kanonischer_name, formular_vorschlag "
            "FROM bestellungen WHERE status='eingegangen'"
        ).fetchall()
        erzeugt = 0
        formular = SpecFormular(min_laenge=int(config.get("planer.formular_min_laenge", 200)))
        for bestellung_id, titel, spec_pfad, spec_inhalt, kanonischer_name, bereits_gemeldet in rows:
            inhalt = spec_inhalt
            if not inhalt and spec_pfad:
                pfad = Path(spec_pfad)
                inhalt = pfad.read_text(encoding="utf-8") if pfad.exists() else ""
            if not inhalt:
                # G-10 Spec-Vollstaendigkeit: keine verwertbare Spezifikation da --
                # nicht raten, Bestellung bleibt 'eingegangen' bis nachgebessert wird.
                continue

            # Betreiber-Auftrag 2026-08-04 (nach dem Hypothesengenerator-Korrektur-
            # Vorfall): "der Zielname sollte nicht als Auftrag da stehen, die
            # Werft sollte ihn selbststaendig finden, nur bei Unsicherheit eine
            # Nachfrage stellen -- sonst hat die Werft selbst nicht sauber
            # gearbeitet". Bevor der Formular-Zwang greift: pruefen, ob die
            # Bestellung zu einem bereits gelieferten Modul passt (semantischer
            # Abgleich gegen werft_wissen). Bei klarem Alleinstellungsmerkmal
            # (Score + Mindestabstand zum Naechstplatzierten, siehe Kalibrierung
            # in _erkenne_bestehendes_ziel_kandidaten) automatisch uebernehmen;
            # bei Unsicherheit (Kandidaten vorhanden, aber kein klarer Abstand --
            # live kalibriert: ein unverwandter Eintrag lag um "embed"/
            # "Kontextfenster"-Wortueberlappung sogar VOR dem korrekten Treffer)
            # eine gezielte Rueckfrage statt eines blinden Griffs.
            if not kanonischer_name:
                kandidaten = _erkenne_bestehendes_ziel_kandidaten(inhalt)
                if kandidaten:
                    top = kandidaten[0]
                    abstand = top["score"] - (kandidaten[1]["score"] if len(kandidaten) > 1 else 0.0)
                    # Score-Skalen unterscheiden sich je Quelle (Ablauf-
                    # Mengenschnitt vs. Embedding-Kosinus-Aehnlichkeit) --
                    # siehe _erkenne_bestehendes_ziel_kandidaten Docstring.
                    if top.get("quelle") == "ablauf":
                        min_score, min_abstand = 0.0, _ABLAUF_MIN_ABSTAND
                    else:
                        min_score, min_abstand = _ZIEL_ERKENNUNG_MIN_SCORE, _ZIEL_ERKENNUNG_MIN_ABSTAND
                    if top["score"] >= min_score and abstand >= min_abstand:
                        kanonischer_name = top["kanonischer_name"]
                        con.execute(
                            "UPDATE bestellungen SET kanonischer_name=? WHERE bestellung_id=?",
                            (kanonischer_name, bestellung_id),
                        )
                        con.commit()
                        remember_system(
                            owner="werft", target=bestellung_id,
                            content=(
                                f"Automatisch erkannt: Bestellung '{titel}' bezieht sich vermutlich "
                                f"auf bereits geliefertes Modul '{kanonischer_name}' (Score {top['score']:.3f}, "
                                f"Abstand zum Naechstplatzierten {abstand:.3f}) -- kanonischer_name "
                                f"automatisch gesetzt, keine manuelle Nachfrage noetig."
                            ),
                            tags="werft,auto_erkennung,ziel_gefunden",
                        )
                    elif top["score"] >= min_score:
                        kandidaten_text = "; ".join(
                            f"{k['kanonischer_name']} (Score {k['score']:.3f})" for k in kandidaten
                        )
                        vorschlag = (
                            f"UNSICHER, ob diese Bestellung eine Korrektur an einem bereits "
                            f"vorhandenen Modul ist. Kandidaten (kein klarer Abstand zueinander, "
                            f"daher keine automatische Wahl): {kandidaten_text}. Bitte im "
                            f"kanonischer_name-Feld bestaetigen oder ausdruecklich als NEUES "
                            f"Modul kennzeichnen, wenn keiner der Kandidaten passt."
                        )
                        con.execute(
                            "UPDATE bestellungen SET formular_vorschlag=? WHERE bestellung_id=?",
                            (vorschlag, bestellung_id),
                        )
                        con.commit()
                        if not bereits_gemeldet:
                            tisch_client.benachrichtigen(
                                text=(
                                    f"Bestellung '{titel}' ({bestellung_id}): {vorschlag}"
                                ),
                                von="WERFT", thema="Bestellung braucht Bestaetigung (Ziel unsicher)",
                            )
                        continue  # bleibt 'eingegangen' bis Mentor bestaetigt

            # Betreiber-Auftrag 2026-07-27 (Tisch 15:10, Teil b): standardisiertes
            # Formular statt stummem Stehenbleiben, wenn eine Bestellung zwar
            # nicht leer, aber strukturell unvollstaendig ist (kein kanonischer
            # Name, kein Abnahmekriterium, ...) -- vor allem fuer ungeuebte
            # Nutzer (Betreibers Bruder) gedacht, die die Werft-Konventionen noch
            # nicht kennen. Rein strukturelle Pruefung, keine inhaltliche
            # Bewertung (G-10/Abschnitt 12.1 bleibt unangetastet).
            check = formular.pruefe(inhalt, kanonischer_name=kanonischer_name)
            if not check["vollstaendig"]:
                vorschlag = formular.formular_vorschlag(inhalt, check["fehlend"])
                con.execute(
                    "UPDATE bestellungen SET formular_vorschlag=? WHERE bestellung_id=?",
                    (vorschlag, bestellung_id),
                )
                # Fund 2026-07-27 (live im Formular-Test reproduziert): commit()
                # MUSS hier stehen, BEVOR remember_system() aufgerufen wird --
                # remember_system() oeffnet eine EIGENE sqlite3-Verbindung zur
                # selben werft.db; ohne Commit haelt diese `con` noch eine
                # offene Schreibtransaktion (die UPDATE-Zeile oben), gegen die
                # remember_system()'s eigene Verbindung mit "database is
                # locked" scheitert -- die Exception verhindert dann sogar den
                # SPAETEREN Commit am Funktionsende, der Formular-Vorschlag
                # ging dadurch komplett verloren (silently abgefangen von
                # master_loop._sicher()). Geprueft: planer.py ruft
                # remember_system() nur an Stellen auf, an denen dessen
                # eigenes `con` noch keinen Schreibbefehl ausgefuehrt hat --
                # dort tritt dieses Muster nicht auf.
                con.commit()
                remember_system(
                    owner="planer", target=bestellung_id,
                    content=(
                        f"FORMULAR-VORSCHLAG fuer Bestellung '{titel}' ({bestellung_id}): "
                        f"strukturell unvollstaendig, fehlt: {', '.join(check['fehlend'])}. "
                        f"Bleibt 'eingegangen' bis nachgebessert -- Vorschlag in "
                        f"bestellungen.formular_vorschlag hinterlegt."
                    ),
                    tags="planer,formular_vorschlag",
                )
                # Betreiber-Auftrag 2026-07-30 (Tisch 14:40): aktiv melden statt
                # nur passiv in der DB stehen -- ohne Guard wuerde dieser
                # Zweig bei JEDEM eingang_schritt()-Zyklus (alle ~15s) erneut
                # posten, solange die Bestellung nicht nachgebessert ist. Nur
                # beim ERSTEN Erkennen benachrichtigen (bereits_gemeldet war
                # noch leer).
                if not bereits_gemeldet:
                    tisch_client.benachrichtigen(
                        text=(
                            f"Bestellung '{titel}' ({bestellung_id}) ist strukturell "
                            f"unvollstaendig, fehlt: {', '.join(check['fehlend'])}. "
                            f"Bleibt 'eingegangen' bis nachgebessert -- Details in "
                            f"bestellungen.formular_vorschlag."
                        ),
                        von="WERFT", thema="Bestellung braucht Nachbesserung",
                    )
                continue  # bleibt 'eingegangen', kein Task -- wie beim Leer-Fall oben

            task_id = f"task_bestellung_{bestellung_id}"
            exists = con.execute(
                "SELECT 1 FROM task_pipeline WHERE task_id=?", (task_id,)
            ).fetchone()
            if not exists:
                con.execute(
                    "INSERT INTO task_pipeline (task_id, status, assigned_agent, "
                    "knowledge_links, priority) VALUES (?, 'audit', 'Coordinator', ?, 5)",
                    (task_id, f"BESTELLUNG:{bestellung_id} | {titel} | {inhalt}"),
                )
                _plane_aus_spec(con, bestellung_id, kanonischer_name, inhalt)
                erzeugt += 1
            con.execute(
                "UPDATE bestellungen SET status='in_bau', formular_vorschlag=NULL WHERE bestellung_id=?",
                (bestellung_id,),
            )
        con.commit()
        return erzeugt
    finally:
        con.close()


def _warnen_bei_laufenden_prozessen(ziel: Path, kopiert: list[str]) -> list[str]:
    """Fund 2026-07-29 (Betreiber, in einer schwierigen Nacht live durchlebt): die Werft liefert
    neuen Code aus, aber ein bereits laufender Python-Prozess (hier:
    theke_webserver.py) haelt den ALTEN Code noch im Arbeitsspeicher --
    ein Datei-Update aendert daran nichts, die Auslieferung wirkt fuer den
    Nutzer wirkungslos, bis jemand den Prozess neu startet, und niemand wird
    darauf hingewiesen. Reine Warnung/Log, KEIN automatischer Prozess-Kill
    oder -Neustart -- das waere ein riskanter Seiteneffekt ausserhalb der
    Auslieferungs-Verantwortung und je nach Dienst nicht ungefaehrlich.

    Bekannte Einschraenkung (live getestet 2026-07-29): reines Textmatching
    auf `ps aux`-Zeilen erzeugt auch Treffer fuer Bash-Wrapper-Prozesse, die
    den Dateinamen nur als Text in einem eval-String enthalten (z.B. der
    eigene nohup-Start-Befehl), nicht nur fuer den echten Python-Prozess
    selbst. Bewusst in Kauf genommen -- es ist ein Hinweis-Log fuer einen
    Menschen/Mentor, keine automatische Aktion, ein paar zusaetzliche
    Zeilen sind unschaedlich, ein VERPASSTER echter Treffer waere es nicht."""
    try:
        ergebnis = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True,
            timeout=config.get("timeouts.process_probe_sec"),
        )
    except Exception:
        return []
    ziel_str = str(ziel)
    treffer = []
    for zeile in ergebnis.stdout.splitlines():
        if "python" not in zeile.lower():
            continue
        if ziel_str in zeile or any(name in zeile for name in kopiert):
            treffer.append(zeile.strip())
    return treffer


def auslieferung_schritt() -> int:
    """Prueft alle Bestellungen mit status='in_bau': sind ALLE zugehoerigen
    Tasks 'merged', wird der generierte Code nach ziel_ordner kopiert und
    status='geliefert' gesetzt. Gibt die Anzahl ausgelieferter Bestellungen zurueck.
    
    Falls keine Dateien kopiert werden konnten (leere 'kopiert'-Liste), wird der
    Status NICHT auf 'geliefert' gesetzt, sondern ein Fehler-Eintrag im Systemgedaechtnis
    hinterlegt und die Bestellung bleibt auf 'in_bau'."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT bestellung_id, titel, ziel_ordner, kanonischer_name, quelle "
            "FROM bestellungen WHERE status='in_bau'"
        ).fetchall()
        ausgeliefert = 0
        for bestellung_id, titel, ziel_ordner, kanonischer_name, quelle in rows:
            tasks = con.execute(
                "SELECT status FROM task_pipeline WHERE task_id LIKE ?",
                (f"task_bestellung_{bestellung_id}%",),
            ).fetchall()
            if not tasks:
                continue
            if any(s != "merged" for (s,) in tasks):
                continue  # noch nicht alle Teil-Tasks fertig, oder eine ist spec_failed/archived

            ziel = Path(ziel_ordner)
            ziel.mkdir(parents=True, exist_ok=True)
            
            code_dateien = con.execute(
                "SELECT knowledge_links FROM task_pipeline WHERE task_id LIKE ?",
                (f"task_bestellung_{bestellung_id}%",),
            ).fetchall()
            kopiert = []
            fehlgeschlagene_gruende = []
            
            for (knowledge_links,) in code_dateien:
                pfad_treffer = re.findall(r"CODE_PFAD:([^\s|]+)", knowledge_links or "")
                if pfad_treffer:
                    rel_pfad = pfad_treffer[-1]
                    gespeicherter_pfad = Path(rel_pfad)
                    quell_datei = (
                        gespeicherter_pfad
                        if gespeicherter_pfad.is_absolute()
                        else GENERATED_DIR / gespeicherter_pfad.name
                    )
                    if quell_datei.exists():
                        shutil.copy2(quell_datei, ziel / quell_datei.name)
                        kopiert.append(quell_datei.name)
                        # Fund 2026-07-15 (Betreiber, Systemmap-Vorschlag): zusaetzlich
                        # unter dem kanonischen Namen ausliefern, sonst heissen alle
                        # Module "bestellung_<id>.py" und koennen sich gegenseitig
                        # nicht sauber importieren (siehe 00_systemmap.md).
                        if kanonischer_name:
                            shutil.copy2(quell_datei, ziel / kanonischer_name)
                            kopiert.append(kanonischer_name)
                    else:
                        fehlgeschlagene_gruende.append(f"Datei existiert nicht: {quell_datei}")
                else:
                    fehlgeschlagene_gruende.append(f"Kein CODE_PFAD in knowledge_links: {knowledge_links}")

            if not kopiert:
                # Fund 2026-09-04 (Mentor): Leere Auslieferung darf nicht als 'geliefert' gemeldet werden.
                # Status bleibt 'in_bau', Fehler wird im Systemgedaechtnis dokumentiert.
                hinweis = (
                    f"Bestellung '{titel}' ({bestellung_id}) NICHT ausgeliefert: "
                    f"Keine Dateien kopiert. Gruende: {fehlgeschlagene_gruende}"
                )
                print(f"FEHLER: {hinweis}")
                try:
                    remember_system(
                        owner="werft", 
                        target=bestellung_id, 
                        content=hinweis,
                        tags="auslieferung,leer_blockiert",
                    )
                except Exception:
                    pass
                continue

            con.execute(
                "UPDATE bestellungen SET status='geliefert' WHERE bestellung_id=?",
                (bestellung_id,),
            )
            try:
                write_result(
                    bestellung_id,
                    "verified",
                    quelle or "werft",
                    artifact=str(ziel),
                )
            except Exception as exc:
                remember_system(
                    owner="werft",
                    target=bestellung_id,
                    content=f"Ergebnis-Rueckkanal konnte nicht geschrieben werden: {exc}",
                    tags="auslieferung,rueckkanal_fehler",
                )
            ausgeliefert += 1
            print(f"Bestellung '{titel}' ({bestellung_id}) ausgeliefert nach {ziel}: {kopiert}")

            if kopiert:
                prozess_treffer = _warnen_bei_laufenden_prozessen(ziel, kopiert)
                if prozess_treffer:
                    hinweis = (
                        f"Bestellung '{titel}' ({bestellung_id}) nach {ziel} ausgeliefert, "
                        f"aber {len(prozess_treffer)} laufende(r) Python-Prozess(e) "
                        f"referenzieren diesen Ordner/diese Dateien und koennten den alten "
                        f"Code noch im Speicher halten -- Neustart pruefen: {prozess_treffer}"
                    )
                    print(f"WARNUNG: {hinweis}")
                    try:
                        remember_system(
                            owner="werft", target=bestellung_id, content=hinweis,
                            tags="auslieferung,stale_prozess_warnung",
                        )
                    except Exception:
                        pass
        con.commit()
        return ausgeliefert
    finally:
        con.close()
if __name__ == "__main__":
    print("eingang:", eingang_schritt())
    print("auslieferung:", auslieferung_schritt())
