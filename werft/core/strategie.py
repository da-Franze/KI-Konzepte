from __future__ import annotations

"""Stratege: 6. Kernrolle (ERWEITERUNG_6_ROLLE_STRATEGE.md).

Arbeitsstrukturierung (grosse Bestellung -> geordnete Teil-Serie), Reissbrett
(Systemmap als DB-Struktur statt nur Markdown), Bottleneck-R&D-Trigger
(kumulierte Kosten, Pareto-Schnitt), Archiv+Patente-Pflege.

Bewusst NICHT im kritischen Pfad fuer einzelne Tasks -- wird nur aktiv bei
grossen Bestellungen (Zerlegung) oder periodisch (Bottleneck-Scan).
"""
import json
import re
import sqlite3
from pathlib import Path

from configloader import config
from json_utils import extrahiere_json_liste
from memory import remember_system
from ollama_client import ask_model, host_fuer_rolle
from vdb_client import vdb

from runtime_paths import WERFT_DB_PATH as DB_PATH

ZERLEGUNGS_PROMPT = """Du bist der Stratege. Zerlege die folgende grosse Bestellung in eine
geordnete Serie kleiner, einzeln lieferbarer Teile.

WICHTIG (Lehre aus dem ersten Versuch in einem realen Zielprojekt): jeder Teil muss klein genug sein,
dass daraus hoechstens {max_features} konkrete Features abgeleitet werden koennen --
sonst Gap-Analyse-Explosion. Lieber mehr, kleinere Teile als wenige grosse.

BEREITS VORHANDENE MODULE (NICHT erneut planen, nur als Abhaengigkeit referenzieren
falls gebraucht -- Fund 2026-07-15: ein Testlauf ohne diese Liste plante RouterAgent
ein zweites Mal, obwohl er schon Teil 2 war):
{vorhandene_module}

BESTELLUNG:
{spec_inhalt}

Antworte NUR als JSON-Liste, ein Objekt pro NEUEM Teil (keine bereits vorhandenen
Module erneut auflisten), in Bau-Reihenfolge:
[
  {{"kanonischer_name": "<datei_name>.py", "geplante_klasse": "<KlassenName>",
    "geplante_methoden": ["<methode1(args) -> typ>", "..."],
    "abhaengigkeiten": ["<kanonischer_name eines fruehreren/vorhandenen Teils, falls genutzt>"]}}
]

Regeln: kanonische Namen sind eindeutig und sprechend (kein "bestellung_..."-Praefix).
Abhaengigkeiten nur auf bereits FRUEHERE/VORHANDENE Teile, nie Vorwaerts-Referenzen.
"""


def _vorhandene_module() -> str:
    """Namen bereits gelieferter/verbindlich geplanter Module -- Kontext gegen
    Doppelplanung. Fund 2026-07-15 (beim Bau des plan_entwurf-Freigabe-Gates
    entdeckt): die urspruengliche Bedingung war invertiert
    (`status != 'geplant'`) -- schloss ausgerechnet die verbindlich freigegebene
    Stufe AUS und zaehlte unreviewte Entwuerfe ('plan_entwurf') faelschlich MIT.
    Korrekt: alles AUSSER unreviewten Entwuerfen gilt als vorhanden/verbindlich."""
    con = sqlite3.connect(DB_PATH)
    try:
        namen = [r[0] for r in con.execute(
            "SELECT DISTINCT kanonischer_name FROM bestellungen WHERE kanonischer_name IS NOT NULL"
        ).fetchall()]
        namen += [r[0] for r in con.execute(
            "SELECT DISTINCT kanonischer_name FROM strategie_planung WHERE status != 'plan_entwurf'"
        ).fetchall()]
    finally:
        con.close()
    return ", ".join(sorted(set(namen))) or "(keine)"


def zerlege_bestellung(ueber_bestellung: str, spec_inhalt: str) -> list[dict]:
    """Zerlegt eine grosse Bestellung in eine Teil-Serie, schreibt sie als
    'plan_entwurf' (NICHT 'geplant') in strategie_planung. Reicht NICHTS bei
    bestellungen ein.

    Fund 2026-07-15 (Betreiber-Nachfrage, "wer reguliert die Strategieplanung?"):
    ohne einen Zwischenschritt haette naechster_teil_freigeben() jeden LLM-
    Vorschlag ungeprueft als echte Bestellung eingereicht -- eine fehlerhafte
    Zerlegung (siehe Live-Fund: router_agent.py wurde zunaechst ein zweites Mal
    geplant, obwohl laengst Teil 2) haette direkt Coder/Analyst-Ressourcen
    verbraucht, BEVOR es auffiel. Genau dieselbe Luecke wie beim Debugger
    (Eskalation ohne Konsument), nur an anderer Stelle. Fix: 'plan_entwurf'
    muss erst per plan_freigeben() vom Mentor bestaetigt werden (status wird
    dabei zu 'geplant'), bevor naechster_teil_freigeben() ihn ueberhaupt sieht."""
    max_features = config.get("stratege.max_features_pro_teil", 6)
    # Fund 2026-08-24 (Mentor, Punkt 5 GPU-Haenger): wie router.py -- kein
    # eigener Host-Eintrag bisher, Kollision mit Coder/Theke auf der
    # diskreten GPU (54 Eviction-Zyklen seit Boot, journalctl ollama.service).
    # Stratege laeuft periodisch/selten (kein kritischer Latenzpfad wie
    # Coder/Theke) -- CPU-Instanz (Port 11435) reicht, bereits fuer
    # Analyst/Summarizer bewaehrtes Muster.
    antwort = ask_model(
        ZERLEGUNGS_PROMPT.format(
            spec_inhalt=spec_inhalt, max_features=max_features,
            vorhandene_module=_vorhandene_module(),
        ),
        model=config.get("ollama.model.stratege", "qwen3:14b"),
        temperature=0.2,
        host=host_fuer_rolle("stratege"),
    )
    teile = extrahiere_json_liste(antwort)

    con = sqlite3.connect(DB_PATH)
    try:
        for i, teil in enumerate(teile, start=1):
            con.execute(
                "INSERT INTO strategie_planung (ueber_bestellung, teil_nr, kanonischer_name, "
                "geplante_klasse, geplante_methoden, abhaengigkeiten, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'plan_entwurf')",
                (ueber_bestellung, i, teil["kanonischer_name"], teil.get("geplante_klasse"),
                 json.dumps(teil.get("geplante_methoden", []), ensure_ascii=False),
                 json.dumps(teil.get("abhaengigkeiten", []), ensure_ascii=False)),
            )
        con.commit()
    finally:
        con.close()
    return teile


def plan_pruefen(ueber_bestellung: str) -> list[dict]:
    """Gibt alle 'plan_entwurf'-Eintraege zur Mentor-Pruefung zurueck -- VOR
    jeder Freigabe aufrufen, nicht direkt plan_freigeben()."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT id, teil_nr, kanonischer_name, geplante_klasse, geplante_methoden, abhaengigkeiten "
            "FROM strategie_planung WHERE ueber_bestellung=? AND status='plan_entwurf' ORDER BY teil_nr",
            (ueber_bestellung,),
        ).fetchall()
    finally:
        con.close()
    return [
        {"id": r[0], "teil_nr": r[1], "kanonischer_name": r[2], "geplante_klasse": r[3],
         "geplante_methoden": json.loads(r[4] or "[]"), "abhaengigkeiten": json.loads(r[5] or "[]")}
        for r in rows
    ]


def plan_freigeben(ueber_bestellung: str) -> int:
    """Mentor-Freigabe: hebt ALLE 'plan_entwurf'-Eintraege einer ueber_bestellung
    auf 'geplant' -- ERST danach kann naechster_teil_freigeben() sie einreichen.
    Bewusst kein Teil-Filter (alles-oder-nichts) -- bei nur teilweiser Zustimmung
    soll der Mentor die Zerlegung korrigieren/neu anstossen, nicht Teile stumm
    weglassen und die Abhaengigkeitskette dadurch inkonsistent machen."""
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "UPDATE strategie_planung SET status='geplant' WHERE ueber_bestellung=? AND status='plan_entwurf'",
            (ueber_bestellung,),
        )
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def naechster_teil_freigeben(ueber_bestellung: str, ziel_ordner: str) -> str | None:
    """Reicht den naechsten 'geplant'-Teil als echte Bestellung ein -- ABER nur
    wenn kein anderer Teil derselben ueber_bestellung noch in_bau/geplant-aber-
    schon-eingereicht ist (kein automatisches Durchrauschen ohne Zwischenpruefung,
    Betreiber-Vorgabe 2026-07-14). Gibt die neue bestellung_id zurueck oder None."""
    con = sqlite3.connect(DB_PATH)
    try:
        offene_teile = con.execute(
            "SELECT sp.id, sp.teil_nr, sp.kanonischer_name, sp.geplante_klasse, sp.geplante_methoden "
            "FROM strategie_planung sp WHERE sp.ueber_bestellung=? AND sp.status='geplant' "
            "ORDER BY sp.teil_nr LIMIT 1",
            (ueber_bestellung,),
        ).fetchone()
        if offene_teile is None:
            return None  # alle Teile bereits eingereicht oder fertig

        sp_id, teil_nr, kanonischer_name, geplante_klasse, geplante_methoden = offene_teile
        bestellung_id = f"{ueber_bestellung}_teil{teil_nr}"
        spec_inhalt = (
            f"Teil {teil_nr} von '{ueber_bestellung}' (Systemmap-Vorgabe, Stratege-Planung).\n"
            f"Kanonischer Dateiname (WÖRTLICH übernehmen): {kanonischer_name}\n"
            f"Klasse (WÖRTLICH übernehmen): {geplante_klasse}\n"
            f"Methoden: {geplante_methoden}\n"
        )
        con.execute(
            "INSERT INTO bestellungen (bestellung_id, titel, spec_inhalt, quelle, ziel_ordner, "
            "kanonischer_name, status) VALUES (?, ?, ?, ?, ?, ?, 'eingegangen')",
            (bestellung_id, f"{ueber_bestellung} Teil {teil_nr}: {kanonischer_name}",
             spec_inhalt, "Stratege (strategie_planung)", ziel_ordner, kanonischer_name),
        )
        con.execute("UPDATE strategie_planung SET status='eingereicht' WHERE id=?", (sp_id,))
        con.commit()
        return bestellung_id
    finally:
        con.close()


def teil_verifiziert(ueber_bestellung: str, teil_nr: int) -> None:
    """Vom Mentor nach bestandener Abnahme aufgerufen -- markiert den Teil als
    verifiziert, DAMIT naechster_teil_freigeben() den naechsten Teil einreichen
    kann. Kein automatischer Trigger (Betreiber-Vorgabe), bewusst ein expliziter Aufruf."""
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "UPDATE strategie_planung SET status='verifiziert' WHERE ueber_bestellung=? AND teil_nr=?",
            (ueber_bestellung, teil_nr),
        )
        con.commit()
    finally:
        con.close()


def fordere_korrektur(
    bestellung_id: str,
    grund: str,
    owner: str = "mentor_review",
    spec_ersetzungen: dict[str, str] | None = None,
    geplante_klasse: str | None = None,
    abnahmekriterium_code: str | None = None,
) -> None:
    """Fund 2026-07-16 (Betreiber: "die Werft kann nicht selbststaendig arbeiten,
    das ist das Ziel!"): bislang musste JEDE Korrektur an einer bereits
    gelieferten Bestellung von Hand ueber zwei getrennte SQL-Updates
    orchestriert werden (task_pipeline.status UND bestellungen.status) --
    ein vergessenes zweites Update (bestellungen.status blieb 'geliefert')
    verhinderte am 2026-07-16 mehrfach die eigentliche Auslieferung der
    Korrektur, obwohl die Fehlhistorie-Runde technisch korrekt lief. Diese
    Funktion buendelt beide Schritte atomar in einem Aufruf -- der einzige
    Weg, wie eine Korrektur an geliefertem Code angestossen werden sollte,
    nie durch direkte SQL-Updates von aussen.

    Args:
        bestellung_id: z.B. 'beispielserie_teil6' (ohne 'task_bestellung_'-Praefix)
        grund: konkrete, fuer den Coder verwertbare Fehlbeschreibung
               (wird 1:1 in die Fehlhistorie geschrieben)
        owner: Standard 'mentor_review' (zaehlt NICHT gegen automatische_fehlversuche(),
               siehe memory.automatische_fehlversuche() -- nur owner='analyst' zaehlt)
        spec_ersetzungen: Fund 2026-07-21 (Betreiber-Auftrag, Punkt 4 einer 5-teiligen
               Automatisierungs-Liste, nach live erlebtem eigenem Mentor-Fehler bei
               der Economic-Scout-Umbenennung): eine fordere_korrektur(), die z.B.
               einen Klassennamen aendert, liess bisher das URSPRUENGLICHE
               Abnahmekriterium in bestellungen.spec_inhalt unangetastet -- der Coder
               stand dann vor einem Widerspruch (alte Spec verlangt X, neue
               Fehlhistorie verlangt Y) und loeste ihn inkonsistent. Optionales
               {alt: neu}-Mapping, wird auf spec_inhalt PER .replace() angewendet,
               BEVOR die Korrekturrunde startet -- haelt Spec und Korrektur-Anweisung
               synchron, verhindert genau diesen Fehler strukturell statt durch
               Mentor-Sorgfalt allein.
        geplante_klasse: Fund 2026-07-21 (direkt im Live-Betrieb entdeckt, NACHDEM
               Punkt 3 der 5-teiligen Liste schon gebaut war): _plane_aus_spec()
               (bestellungen.py) traegt den Klassennamen nur bei der ERSTMALIGEN
               Bestellungsanlage automatisch in strategie_planung ein -- eine
               spaetere fordere_korrektur(), die den Klassennamen aendert (z.B. bei
               einer Umbenennung), aktualisiert diesen Eintrag NICHT. Ergebnis: der
               Coder driftet bei jeder weiteren Korrekturrunde wieder zurueck zum
               alten/fehlenden Namen (live reproduziert: WissensAkquirator
               regressierte zweimal zurueck zu BestellungBeispielScout).
               Optionaler Klassenname, wird per UPSERT in strategie_planung
               eingetragen (ueber_bestellung=bestellung_id, teil_nr=1) --
               synchronisiert die Planung mit der Korrektur, nicht nur die Spec.
        abnahmekriterium_code: Fund 2026-08-24 (Betreiber-Auftrag, Root-Cause-Korrektur
               beispielserie_phase2_interface_teil4 als 'geliefert' markiert obwohl der Bug
               weiterbestand): fordere_korrektur() schrieb den `grund` bisher NUR in
               die Fehlhistorie (werft_memory) -- analyst.py::run_once() baut seinen
               Pruef-Prompt aber AUSSCHLIESSLICH aus bestellungen.spec_inhalt, liest
               die Fehlhistorie nie. Bei einer vagen/generischen Original-Spec griff
               dazu noch der explizite Fail-Open-Bias im Analyst-Prompt ("bei vager
               Spezifikation und plausiblem Code: pass=true") -- der Korrekturgrund
               hatte dadurch strukturell NIE eine Chance, tatsaechlich geprueft zu
               werden, egal wie konkret er formuliert war.
               debugger.ausfuehrungs_check() hat dagegen schon LAENGST einen echten,
               LLM-unabhaengigen Mechanismus (seit 2026-07-19/07-28): findet es in
               spec_inhalt einen Abschnitt "## Abnahmekriterium" mit einem
               ```python-Codeblock, fuehrt es genau den als echten Integrationstest
               aus (inkl. Differenztest gegen die alte ausgelieferte Version -- besteht
               die AUCH, gilt das Kriterium als zu schwach und blockiert trotzdem).
               Dieser Parameter nutzt dieses bereits vorhandene Werkzeug (Werkzeug-vor-
               Neubau-Prinzip): der uebergebene Code wird als
               "## Abnahmekriterium (Pflicht)"-Abschnitt in spec_inhalt UND
               knowledge_links eingefuegt (ein bereits vorhandener Abschnitt wird
               ersetzt, nicht dupliziert) -- macht die Korrektur dadurch fuer
               ausfuehrungs_check() sofort sichtbar und LLM-unabhaengig pruefbar,
               bevor der Analyst ueberhaupt zum Zug kommt. Sollte Standard sein fuer
               jede Korrektur, deren `grund` sich in einen konkreten Aufruf +
               erwartetes Ergebnis uebersetzen laesst.
    """
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT status FROM bestellungen WHERE bestellung_id=?", (bestellung_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Bestellung '{bestellung_id}' existiert nicht.")
    finally:
        con.close()

    # remember_system() oeffnet selbst eine Verbindung -- Fund 2026-07-16:
    # eine gleichzeitig offene, unbestaetigte Transaktion aus DIESER Funktion
    # fuehrt sonst zu 'database is locked'. Deshalb hier zuerst callen und
    # abschliessen lassen, DANACH die eigene Verbindung fuer die Status-Updates
    # oeffnen -- derselbe Lock-Fund wie in debugger.py/analyst.py heute frueh.
    target = f"bestellung_{bestellung_id}"
    remember_system(owner=owner, target=target, content=grund, tags="fordere_korrektur")

    # Fund 2026-08-30 (Mentor, live 3x manuell nachbereinigt am selben Tag --
    # theke_antwort.py x2, theke_webserver.py, router_agent.py, schwarzes_brett.py):
    # wird ueber spec_ersetzungen ein neuer "## Konstanten-Patch"-Block eingefuegt
    # (das etablierte Muster fuer exakte String-Ersetzungen ohne LLM-Regenerierung),
    # blieb ein etwaiger AELTERER Block aus einer frueheren Korrekturrunde bisher
    # unangetastet in spec_inhalt/knowledge_links stehen. coder.py::
    # _versuche_konstanten_patch() findet per re.search() aber nur den ERSTEN
    # Treffer -- der laengst ueberholte alte Block wurde dadurch weiter versucht
    # (und schlug fehl, da sein ALT-Text nach der vorigen Lieferung nicht mehr im
    # Live-Code vorkam), der neue, eigentlich gewollte Block wurde nie erreicht.
    # Musste bisher jedes Mal von Hand per Direkt-SQL nachbereinigt werden. Fix:
    # sobald ein neuer Konstanten-Patch-Block eingefuegt wird, alle bereits
    # vorhandenen zuerst automatisch entfernen (sie sind nach ihrer eigenen
    # Lieferung ohnehin wirkungslose Altlast, kein Informationsverlust).
    _KONSTANTEN_PATCH_MUSTER = re.compile(r"## Konstanten-Patch\n.*?```\n\n", re.DOTALL)

    def _ohne_alte_konstanten_patches(text: str) -> str:
        return _KONSTANTEN_PATCH_MUSTER.sub("", text or "")

    con = sqlite3.connect(DB_PATH)
    try:
        if spec_ersetzungen:
            fuegt_konstanten_patch_ein = any(
                "## Konstanten-Patch" in neu for neu in spec_ersetzungen.values()
            )
            spec_row = con.execute(
                "SELECT spec_inhalt FROM bestellungen WHERE bestellung_id=?", (bestellung_id,)
            ).fetchone()
            neuer_spec = spec_row[0]
            if fuegt_konstanten_patch_ein:
                neuer_spec = _ohne_alte_konstanten_patches(neuer_spec)
            for alt, neu in spec_ersetzungen.items():
                neuer_spec = neuer_spec.replace(alt, neu)
            con.execute(
                "UPDATE bestellungen SET spec_inhalt=? WHERE bestellung_id=?",
                (neuer_spec, bestellung_id),
            )
            # Fund 2026-07-21 (Live-Regression, direkt bei der ersten Nutzung von
            # spec_ersetzungen entdeckt): bestellungen.spec_inhalt ist NICHT die
            # Quelle, aus der coder.py tatsaechlich liest -- das ist
            # task_pipeline.knowledge_links (gebaut EINMALIG bei eingang_schritt()
            # aus spec_inhalt, seitdem nie mehr synchronisiert). Ohne dieses Update
            # bleibt der Coder-Prompt beim alten Text, obwohl spec_inhalt laengst
            # korrigiert ist -- exakt reproduziert: Analyst lehnte WissensAkquirator
            # ab und verlangte wieder EconomicScout, weil knowledge_links noch die
            # alte Bezeichnung enthielt. Beide Felder muessen synchron bleiben.
            task_id = f"task_bestellung_{bestellung_id}"
            kl_row = con.execute(
                "SELECT knowledge_links FROM task_pipeline WHERE task_id=?", (task_id,)
            ).fetchone()
            if kl_row:
                neue_kl = kl_row[0]
                if fuegt_konstanten_patch_ein:
                    neue_kl = _ohne_alte_konstanten_patches(neue_kl)
                for alt, neu in spec_ersetzungen.items():
                    neue_kl = neue_kl.replace(alt, neu)
                con.execute(
                    "UPDATE task_pipeline SET knowledge_links=? WHERE task_id=?",
                    (neue_kl, task_id),
                )
        if abnahmekriterium_code:
            neuer_abschnitt = (
                "## Abnahmekriterium (Pflicht)\n"
                "Aufruf (von strategie.fordere_korrektur() gesetzt -- wird von "
                "debugger.ausfuehrungs_check() automatisch als echter Integrationstest "
                "ausgefuehrt, inkl. Differenztest gegen die alte ausgelieferte Version; "
                "ein Fehlschlag blockiert den Merge UNABHAENGIG vom Analyst-Urteil):\n"
                f"```python\n{abnahmekriterium_code.strip()}\n```\n"
            )
            # Gleiches Muster wie debugger.py's Erkennungs-Regex (Abnahmekriterium
            # .*? ```python ... ```), DOTALL -- ein bereits vorhandener Abschnitt
            # wird ERSETZT (z.B. bei einer zweiten Korrekturrunde), nicht dupliziert.
            #
            # Fund 2026-08-25 (Mentor, live durch zwei stumme Fehlschlaege in
            # dieser Session aufgedeckt, bei specgen_4779e4aa0d und
            # beispielserie_economic_scout): "##?" verlangt REGEX-technisch MINDESTENS
            # ein woertliches '#' (das "?" gilt nur fuer das ZWEITE '#', nicht
            # fuer die ganze Gruppe) -- kein Zufallstreffer, re.search(r"##?", "x")
            # liefert nachweislich None. Bestellungen, deren urspruengliches
            # Abnahmekriterium ohne Markdown-Ueberschrift geschrieben war (z.B.
            # nur "Abnahmekriterium:\n```python...", wie beim urspruenglichen
            # Formular-Text), matchten deshalb NIE -- der Ersetzungs-Zweig griff
            # nie, der Fallback haengte den neuen Abschnitt einfach ans Ende an
            # und liess den alten, jetzt widerspruechlichen Abschnitt stehen
            # (der Coder sah dadurch zwei sich widersprechende Abnahmekriterien).
            # debugger.py's eigene Erkennungs-Regex hat dieses Problem NICHT
            # (sie verlangt gar kein '#'), war also nie "das gleiche Muster",
            # wie der Kommentar hier bisher behauptete. Fix: 0 bis 2 '#' statt
            # mindestens 1.
            abnahme_muster = re.compile(
                r"#{0,2}\s*Abnahmekriterium.*?```python\n.*?```\n?", re.DOTALL
            )

            def _mit_abnahmekriterium(text: str) -> str:
                # Callable statt Roh-String als Ersatz: neuer_abschnitt enthaelt
                # oft Backslash-Sequenzen (\d,   etc. aus eingebettetem
                # Regex-Testcode) -- re.sub() interpretiert Backslashes in einem
                # STRING-Ersatz als Backreferences/Escapes (z.B. \d ist dort kein
                # gueltiges Escape), ein Callable umgeht das komplett.
                text = text or ""
                if abnahme_muster.search(text):
                    return abnahme_muster.sub(lambda _m: neuer_abschnitt, text, count=1)
                return text.rstrip() + "\n\n" + neuer_abschnitt

            spec_row = con.execute(
                "SELECT spec_inhalt FROM bestellungen WHERE bestellung_id=?", (bestellung_id,)
            ).fetchone()
            con.execute(
                "UPDATE bestellungen SET spec_inhalt=? WHERE bestellung_id=?",
                (_mit_abnahmekriterium(spec_row[0] if spec_row else ""), bestellung_id),
            )
            # Derselbe Sync-Fund wie bei spec_ersetzungen (2026-07-21): der Coder
            # liest tatsaechlich aus task_pipeline.knowledge_links, nicht aus
            # bestellungen.spec_inhalt -- ohne dieses Update saehe der Coder das neue
            # Abnahmekriterium nie (debugger.ausfuehrungs_check() liest zwar direkt
            # aus bestellungen.spec_inhalt und wuerde es trotzdem pruefen, aber der
            # Coder bekaeme dann ein Kriterium vorgesetzt, das er nie kannte).
            task_id_ak = f"task_bestellung_{bestellung_id}"
            kl_row = con.execute(
                "SELECT knowledge_links FROM task_pipeline WHERE task_id=?", (task_id_ak,)
            ).fetchone()
            if kl_row:
                con.execute(
                    "UPDATE task_pipeline SET knowledge_links=? WHERE task_id=?",
                    (_mit_abnahmekriterium(kl_row[0]), task_id_ak),
                )
        if geplante_klasse:
            kanon_row = con.execute(
                "SELECT kanonischer_name FROM bestellungen WHERE bestellung_id=?", (bestellung_id,)
            ).fetchone()
            kanonischer_name = kanon_row[0] if kanon_row else None
            if kanonischer_name:
                bereits_geplant = con.execute(
                    "SELECT id FROM strategie_planung WHERE ueber_bestellung=? AND teil_nr=1",
                    (bestellung_id,),
                ).fetchone()
                if bereits_geplant:
                    con.execute(
                        "UPDATE strategie_planung SET kanonischer_name=?, geplante_klasse=? WHERE id=?",
                        (kanonischer_name, geplante_klasse, bereits_geplant[0]),
                    )
                else:
                    con.execute(
                        "INSERT INTO strategie_planung (ueber_bestellung, teil_nr, kanonischer_name, "
                        "geplante_klasse, status) VALUES (?, 1, ?, ?, 'geplant')",
                        (bestellung_id, kanonischer_name, geplante_klasse),
                    )
        con.execute("UPDATE bestellungen SET status='in_bau' WHERE bestellung_id=?", (bestellung_id,))
        con.execute(
            "UPDATE task_pipeline SET status='spec' WHERE task_id=?",
            (f"task_bestellung_{bestellung_id}",),
        )
        con.commit()
    finally:
        con.close()


def bottleneck_scan() -> list[dict]:
    """2.3: Pareto-Schnitt ueber kumulierte Kosten (Haeufigkeit x Dauer).
    Gibt Kandidaten fuer einen R&D-Versuch zurueck (nur die Kandidatenliste --
    ob tatsaechlich ein Versuch gestartet wird, entscheidet der Mentor, das ist
    eine neue-Architektur-Aktivitaet und braucht mentor_actions_log)."""
    pareto_prozent = float(config.get("stratege.rnd_pareto_schnitt_prozent", 20))
    mindest_stichprobe = int(config.get("stratege.rnd_mindest_stichprobe", 10))

    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT charakter_id, modell, n_aufrufe, kumulierte_kosten, cpk_status "
            "FROM charakter_leistung WHERE n_aufrufe >= ? ORDER BY kumulierte_kosten DESC",
            (mindest_stichprobe,),
        ).fetchall()
    finally:
        con.close()

    if not rows:
        return []
    gesamt = sum(r[3] for r in rows) or 1.0
    kandidaten = []
    laufend = 0.0
    for charakter_id, modell, n_aufrufe, kosten, cpk_status in rows:
        laufend += kosten
        anteil_prozent = 100 * kosten / gesamt
        if 100 * laufend / gesamt <= pareto_prozent or not kandidaten:
            kandidaten.append({
                "charakter_id": charakter_id, "modell": modell, "n_aufrufe": n_aufrufe,
                "kumulierte_kosten": kosten, "anteil_prozent": round(anteil_prozent, 1),
                "cpk_status": cpk_status,
                "verstaerkt": cpk_status in ("incapable", "marginal"),
            })
    return kandidaten


def archiv_eintrag(bestellung_id: str) -> str:
    """2.4: Konsolidierter Archiv-Eintrag NACH Auslieferung -- Bauplan (Systemmap-
    Auszug) + finale Schnittstelle + Bau-/Korrekturhistorie in EINEM Eintrag,
    statt verstreut ueber specialists_v8/Funktions-Registry/bestellungen-Status."""
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT titel, kanonischer_name, ziel_ordner, status FROM bestellungen WHERE bestellung_id=?",
            (bestellung_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Bestellung {bestellung_id} nicht gefunden")
        titel, kanonischer_name, ziel_ordner, status = row
        planung = con.execute(
            "SELECT teil_nr, geplante_klasse, geplante_methoden FROM strategie_planung "
            "WHERE kanonischer_name=?", (kanonischer_name,),
        ).fetchone()
    finally:
        con.close()

    text = (
        f"ARCHIV: Bestellung '{titel}' ({bestellung_id}), Status={status}. "
        f"Kanonischer Name: {kanonischer_name}, ausgeliefert nach {ziel_ordner}."
    )
    if planung:
        text += f" Geplante Klasse: {planung[1]}, Methoden: {planung[2]}."
    return vdb.upsert(text, {"owner": "stratege", "memory_type": "system", "tags": "archiv"})


def patent_vorschlagen(beschreibung: str, quelle: str) -> str:
    """2.4: Kreative, wiederverwendbare Loesung -- qualitativ anders als eine
    Fehlhistorie-Lehre (was ging schief) oder ein Archiv-Eintrag (was wurde
    geliefert). Kandidaten kommen vom Strategen (nach gewonnenem R&D-Vergleich)
    oder vom Mentor/Debugger bei einer eigenstaendig dokumentierenswerten Idee."""
    text = f"PATENT ({quelle}): {beschreibung}"
    return vdb.upsert(text, {"owner": "stratege", "memory_type": "system", "tags": "patent"})


if __name__ == "__main__":
    print("Bottleneck-Kandidaten:", bottleneck_scan())
