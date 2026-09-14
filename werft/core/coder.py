from __future__ import annotations

"""Coder: Spec -> lauffaehiger Python-Code (Neustart-Spec Abschnitt 7.3).

Nutzt memory.fehlhistorie_text() statt eigener Fehlhistorien-Logik -- das ist die
strukturelle Absicherung gegen den historischen "Code als Zufallsprodukt"-Bug
(Betreiber-Auftrag 2026-07-09, 5-Why-Analyse: Fehlbegruendungen muessen GARANTIERT
beim Coder ankommen, nicht nur geschrieben werden).
"""
import json
import re
import sqlite3
import tempfile
import textwrap
from pathlib import Path

from configloader import config
from memory import fehlhistorie_text, remember_system
from ollama_client import ask_model, host_fuer_rolle
from vdb_client import vdb
from runtime_paths import GENERATED_DIR
from role_dna import load_role_dna
from werft_map_pipeline import WerftMapPipeline
from code_crawler import crawl_project

from runtime_paths import WERFT_DB_PATH as DB_PATH
_METHODEN_MUSTER = re.compile(r"^\s{0,8}def\s+(\w+\([\s\S]*?\))\s*(?:->\s*[^:\n]+)?:", re.MULTILINE)
# Fund 2026-08-26 (Betreiber-Auftrag ueber Mentor, Touchpoint 1 der neuen
# call_graph_kanten-Infrastruktur): erkennt self.<andere_methode>()-Aufrufe
# innerhalb derselben generierten Datei -- bewusst klein angefangen (nur
# intra-Datei-Kanten, keine Cross-Modul-Aufloesung), siehe
# _aktualisiere_call_graph() unten.
_SELF_AUFRUF_MUSTER = re.compile(r"self\.(\w+)\s*\(")
# Fund 2026-08-26 (beim isolierten Testen von Touchpoint 2 entdeckt, bevor es
# in Produktion haette landen koennen): bewusst NICHT _METHODEN_MUSTER oben
# wiederverwendet (dessen \s{0,8} erfasst auch VERSCHACHTELTE Hilfsfunktionen
# innerhalb einer Methode, z.B. _eval_node() in logik_spezialist.py -- die
# sind keine Klassen-Methoden, werden nie ueber self.X() aufgerufen und
# duerfen weder als von_methode noch fuer die "kein Aufrufer gefunden"-Pruefung
# in debugger.py wie echte Methoden behandelt werden). Exakt 4 Leerzeichen
# (Klassen-Methoden-Ebene) statt 0-8 -- praeziser als das bestehende Muster,
# bewusst dafuer nicht wiederverwendet.
_METHODEN_NAME_MUSTER = re.compile(r"^\s{4}def\s+(\w+)\s*\(", re.MULTILINE)
# Fund 2026-08-04 (Betreiber-Auftrag, Systemmap-Konsistenzpruefung): analyst.py
# importiert dieses Muster, ueberschrieb es aber lokal mit einer bereits
# gefixten Version (Divergenz statt Wiederverwendung -- genau das, wovor der
# DRY-Kommentar bei _aktualisiere_systemmap warnt). Hier synchronisiert:
# mehrzeilige Signaturen (Parameter ueber mehrere Zeilen) wurden vorher
# komplett uebersehen, nicht nur unvollstaendig erfasst -- betrifft auch
# _schnittstelle_aus_datei() unten, das dem Analysten/Coder bisher
# unvollstaendige Interface-Dokumentation lieferte.
_KLASSE_MUSTER = re.compile(r"^class\s+(\w+)", re.MULTILINE)
# Fund 2026-07-20 (Betreiber, nach live beobachtetem "frage"/"inhalt"-Vertragsbruch
# zwischen theke_router.py und router_agent.py): Methoden-Signaturen wie
# "route(self, nachricht: Dict[str, Any], ziel_agent: str)" sagen dem Coder
# NUR, dass ein Dict uebergeben werden muss -- NICHT, welche Schluessel darin
# intern gelesen werden. Genau diese Luecke fuehrte dazu, dass ein Aufrufer
# {"frage": ...} baute, obwohl die Methode intern nachricht.get("inhalt", "")
# liest. Diese Muster erkennen Dict-Zugriffe (varname["key"] / varname.get("key"))
# im Quelltext einer Abhaengigkeit, damit der Coder die ECHTEN erwarteten
# Schluessel sieht statt sie zu erraten.
_DICT_ZUGRIFF_MUSTER = re.compile(
    r"\b(\w+)(?:\.get\(\s*|\[\s*)[\"']([a-zA-Z_][\w]*)[\"']"
)

# Fund 2026-07-17 (beispielserie_phase2_interface_teil1 UND teil2, derselbe Fehler
# zweimal hintereinander in verschiedenen Modulen): configloader.py wird von
# praktisch jedem Modul gebraucht (DNA-Regel 6), steht aber NIE in
# strategie_planung.abhaengigkeiten (gilt als "selbstverstaendlich"), bekam
# dadurch NIE eine Ground-Truth-Schnittstelle mitgeliefert -- der Coder hat
# zweimal eine EIGENE, abweichende ConfigLoader-Klasse erfunden statt die
# echte zu importieren. Deshalb hier fest eingebaut, unabhaengig von der
# Abhaengigkeitsliste.
_IMMER_BEKANNTE_MODULE = ("configloader.py",)


_STRING_KONSTANTE_MUSTER = re.compile(
    r'^([A-Z_][A-Z0-9_]*)\s*=\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')', re.DOTALL | re.MULTILINE
)

# Fund 2026-07-27 (Betreiber-Auftrag, Tisch: "wenn du LLM-Nutzung in die eigene
# Spezifikation injizierst, wuerde die Werft das nachimplementieren? Sollte
# sowas nicht automatisch erkannt werden?"): beim Selbstbau-Experiment (siehe
# project_selbstbau_phase2_und_forex_regelwerk_2026-07-27) hat die von der
# Werft selbst geschriebene Spezifikation ihres Kern-Mechanismus NIE erwaehnt,
# dass Coder/Analyst tatsaechlich ein LLM aufrufen -- die Selbst-Beschreibung
# sah nur Klassen/Methoden/Status, keine externen Werkzeug-Abhaengigkeiten.
# Der Nachbau ersetzte die LLM-Aufrufe dadurch durch triviale Attrappen
# (Coder kopiert Text woertlich, Analyst genehmigt immer). Root Cause: dieselbe
# Kategorie Luecke wie bei String-Konstanten/Status-Werten oben -- die
# Interface-Extraktion suchte nie nach Aufrufen externer Werkzeuge. Jetzt
# dauerhaft (nicht nur fuer diesen einen Test) ergaenzt: LLM-/Netzwerk-/
# Subprocess-Aufrufe werden erkannt und als Ground Truth mitgegeben, damit ein
# Nachbau sie nicht mangels Wissen wegloesst.
#
# Erweiterung 2026-07-27 (Betreiber, Tisch direkt im Anschluss: "dazu wuerde ich
# z.b. auch andere Werkzeuge zaehlen die tief notwendig sind wie Dateisystem-
# Aufrufe, zip, ... Tools eben."): dieselbe Kategorie Luecke gilt auch fuer
# Dateisystem-/Archiv-Operationen -- open(), Path.write_text/read_text,
# zipfile/shutil/os.remove etc. sind genauso "externe Werkzeuge", deren
# Verwendung ein Nachbau nur kennt, wenn sie woertlich als Ground Truth
# mitgegeben werden, nicht weil sie sich aus Klassen-/Methodennamen ableiten
# liessen.
_EXTERNES_WERKZEUG_MUSTER = re.compile(
    r"\b(ask_model"
    r"|requests\.(?:get|post|put|delete)"
    r"|subprocess\.(?:run|Popen|call|check_output)"
    r"|open"
    r"|Path(?:\.\w+)?\.(?:write_text|read_text|write_bytes|read_bytes|mkdir|unlink|rmdir)"
    r"|zipfile\.ZipFile"
    r"|shutil\.\w+"
    r"|os\.(?:system|remove|rename|makedirs|rmdir)"
    r")\s*\("
)
_DEF_POSITION_MUSTER = re.compile(r"^\s{0,8}def\s+(\w+)", re.MULTILINE)

# Fund 2026-07-27 (Betreiber-Auftrag: "erst wenn die Werft sich selbst SAUBER
# spezifizieren kann, kann Phase 2 ausgefuehrt werden" -- letzte der 6 beim
# Selbstbau-Experiment gefundenen Luecken): db_setup.py definiert nur die
# SPALTE status (Typ TEXT), die tatsaechlich BENUTZTEN Werte (eingegangen,
# code_ready, review_pending, geliefert, ...) stehen verstreut in
# UPDATE-Statements/String-Literalen in coder.py/debugger.py/analyst.py/
# master_loop.py -- weder Klassen/Methoden-Extraktion noch die
# String-Konstanten-Extraktion oben erfasst das (Status-Werte stehen NICHT in
# Modul-Level-Konstanten, sondern inline in Methodenkoerpern). Coder erfand
# deshalb eine plausible, aber falsche Status-Wortliste (pending/coder_done/...).
_STATUS_WERT_MUSTER = re.compile(r"status\s*=\s*['\"]([a-z_]+)['\"]")


def _schnittstelle_aus_datei(pfad: Path) -> str:
    """Extrahiert Klassenname(n) UND Methoden-Signaturen aus einer Datei --
    Fund 2026-07-17: die alte Version extrahierte NUR Methoden, nie den
    Klassennamen selbst. Der Coder bekam dadurch korrekte Methodennamen,
    aber nie den Namen, unter dem er die Klasse tatsaechlich importieren/
    instanziieren muss -- riet einen generischen Namen (z.B. "VDBClient"
    statt "VDBClientProjekt"), was zu einem stillen ImportError-Fallback auf
    eine Stub-Klasse fuehrte (leere Suchergebnisse statt Fehler).

    Fund 2026-07-23 (Mentor, live reproduziert am Selbstbau-Experiment
    Variante B): fuer Dateien OHNE Klassen (z.B. db_setup.py -- reines
    Schema-Skript, eine Funktion main(), der eigentliche Inhalt steckt in
    einer Modul-Level-String-Konstante wie SCHEMA = \"\"\"...\"\"\") lieferte
    diese Funktion nur "(keine gefunden)" fuer Klassen/Methoden -- der Coder
    bekam bei korrekt deklarierter Abhaengigkeit trotzdem KEIN brauchbares
    Grounding und lehnte daraufhin ehrlich ab statt zu raten (kein Fehler des
    Coders, aber eine Luecke in dem, was als "Schnittstelle" gilt). Jetzt
    zusaetzlich GROSSGESCHRIEBENE Modul-Level-String-Konstanten (SCHEMA,
    PROMPT_TEMPLATE, DNA, etc. -- Python-Konvention fuer Konstanten) per
    Regex einsammeln und woertlich (gekuerzt) mit ausgeben, als Ground Truth
    fuer Datei-Typen, deren Inhalt nicht in Klassen/Methoden lebt."""
    code = pfad.read_text(encoding="utf-8", errors="ignore")
    klassen = _KLASSE_MUSTER.findall(code)
    # Mehrzeilige Signaturen normalisieren (siehe _METHODEN_MUSTER-Fund
    # 2026-08-04) -- sonst reisst eine mehrzeilige Signatur die lesbare
    # "Methoden = [...]"-Ground-Truth-Zeile mit rohen Zeilenumbruechen auf.
    methoden = [re.sub(r"\s+", " ", m).strip() for m in _METHODEN_MUSTER.findall(code)]

    # Dict-Zugriffe pro Variablenname sammeln (z.B. "nachricht" -> {"inhalt", "hop_zahl"}) --
    # das sind die tatsaechlich im Code gelesenen Schluessel, Ground Truth statt Raten.
    dict_zugriffe: dict[str, set[str]] = {}
    for varname, key in _DICT_ZUGRIFF_MUSTER.findall(code):
        dict_zugriffe.setdefault(varname, set()).add(key)
    vertraege = [f"{var}: {sorted(keys)}" for var, keys in dict_zugriffe.items()]

    basis = f"Klasse(n) = {klassen or '(keine gefunden)'}, Methoden = {methoden or '(keine gefunden)'}"
    if vertraege:
        basis += f", Erwartete Dict-Schluessel (woertlich, NICHT selbst benennen): {'; '.join(vertraege)}"

    # Universell (nicht nur bei fehlenden Klassen) -- Status-Werte werden inline
    # in Methodenkoerpern gesetzt, nicht in Signaturen oder Modul-Konstanten.
    status_werte = sorted(set(_STATUS_WERT_MUSTER.findall(code)))
    if status_werte:
        basis += f"\nTatsaechlich verwendete status-Werte in dieser Datei (woertlich, NICHT erfinden): {status_werte}"

    if not klassen:
        # Nur bei Dateien ohne Klassen zusaetzlich versuchen -- bei normalen
        # Modulen wuerde das den Prompt unnoetig aufblaehen (z.B. lange
        # DNA/PROMPT_TEMPLATE-Konstanten in coder.py/analyst.py selbst).
        konstanten = []
        for match in _STRING_KONSTANTE_MUSTER.finditer(code):
            name = match.group(1)
            inhalt = match.group(2) or match.group(3) or ""
            inhalt = inhalt.strip()
            if inhalt:
                gekuerzt = inhalt[:8000] + ("..." if len(inhalt) > 8000 else "")
                konstanten.append(f"{name} = \"\"\"{gekuerzt}\"\"\"")
        if konstanten:
            basis += f"\nModul-Level-String-Konstanten (woertlich, als Ground Truth verwenden):\n" + "\n---\n".join(konstanten)

    # Externe Werkzeug-Aufrufe (LLM/Netzwerk/Subprocess/Dateisystem) nach
    # umschliessender Funktion gruppieren -- Ground Truth, damit ein Nachbau
    # sie nicht mangels Wissen durch triviale Attrappen ersetzt (siehe Fund
    # 2026-07-27 oben). Umschliessende Funktion = die naechste vorangehende
    # "def ..."-Zeile (Positionsvergleich, kein echter AST-Scope-Parser noetig
    # fuer diesen Zweck -- Ground-Truth-Hinweis, keine exakte Scope-Analyse).
    werkzeug_treffer = list(_EXTERNES_WERKZEUG_MUSTER.finditer(code))
    if werkzeug_treffer:
        def_positionen = [(m.start(), m.group(1)) for m in _DEF_POSITION_MUSTER.finditer(code)]
        werkzeuge_pro_funktion: dict[str, set[str]] = {}
        for treffer in werkzeug_treffer:
            umschliessend = "(Modulebene)"
            for pos, name in def_positionen:
                if pos <= treffer.start():
                    umschliessend = name
                else:
                    break
            werkzeuge_pro_funktion.setdefault(umschliessend, set()).add(treffer.group(1))
        eintraege = [f"{funktion}() nutzt {sorted(werkzeuge)}" for funktion, werkzeuge in werkzeuge_pro_funktion.items()]
        basis += (
            "\nExterne Werkzeug-Aufrufe (LLM/Netzwerk/Subprocess/Dateisystem, woertlich "
            "als Ground Truth -- diese Abhaengigkeiten MUESSEN beim Nachbau erhalten "
            "bleiben, NICHT durch eine Attrappe/Kopie ersetzen): " + "; ".join(eintraege)
        )

    return basis


def _abhaengigkeits_schnittstellen(task_id: str) -> str:
    """Fund 2026-07-15 (beispielserie_teil3): der Coder rief eine nicht
    existierende Methode (get_config()) auf einer echten Abhaengigkeit auf --
    die einzige Quelle dafuer war die unscharfe VDB-Aehnlichkeitssuche, nie die
    TATSAECHLICHE Schnittstelle. Diese Funktion liest die echten Klassen- und
    Methoden-Signaturen der in strategie_planung.abhaengigkeiten gelisteten
    Module PLUS der immer-relevanten Module (_IMMER_BEKANNTE_MODULE) aus den
    bereits ausgelieferten Dateien -- Ground Truth statt Aehnlichkeit."""
    con = sqlite3.connect(DB_PATH)
    try:
        bestellung_id = task_id.replace("task_bestellung_", "") if task_id.startswith("task_bestellung_") else None
        if not bestellung_id:
            return "(keine bekannten Abhaengigkeiten -- kein Bestellungs-Task)"

        row = con.execute(
            "SELECT ziel_ordner, kanonischer_name FROM bestellungen WHERE bestellung_id=?",
            (bestellung_id,),
        ).fetchone()
        if not row or not row[1]:
            return "(keine bekannten Abhaengigkeiten)"
        ziel_ordner, eigener_name = row

        planung = con.execute(
            "SELECT abhaengigkeiten FROM strategie_planung WHERE kanonischer_name=? ORDER BY id DESC LIMIT 1",
            (eigener_name,),
        ).fetchone()
        # Fund 2026-07-19 (beispiel_charakter_serie, Live-Absturz): strategie_planung.abhaengigkeiten
        # sollte immer ein JSON-Array sein (so schreibt es strategie.zerlege_bestellung()),
        # ein manueller/aelterer Insert hatte aber einen nackten String ("configloader.py"
        # statt '["configloader.py"]") -- json.loads() riss die GESAMTE Coder-Rolle jeden
        # Zyklus erneut ab. Defensiv: bei ungueltigem JSON als Ein-Element-Liste interpretieren
        # statt zu crashen (Daten selbst schon korrigiert, das hier ist die strukturelle Absicherung).
        roh = planung[0] if planung and planung[0] else None
        if roh:
            try:
                abhaengigkeiten = json.loads(roh)
            except json.JSONDecodeError:
                abhaengigkeiten = [roh]
        else:
            abhaengigkeiten = []

        alle_namen = list(dict.fromkeys(
            [d for d in _IMMER_BEKANNTE_MODULE if d != eigener_name] + abhaengigkeiten
        ))
        if not alle_namen:
            return "(keine Abhaengigkeiten laut Systemmap-Planung)"

        texte = []
        for dep_name in alle_namen:
            for kandidat in (Path(ziel_ordner) / dep_name, Path(__file__).parent / dep_name):
                if kandidat.exists():
                    texte.append(f"- {dep_name}: {_schnittstelle_aus_datei(kandidat)}")
                    break
            else:
                texte.append(f"- {dep_name}: Datei nicht gefunden, Vorsicht -- keine Schnittstelle bekannt")
        return "\n".join(texte) if texte else "(keine bekannten Abhaengigkeiten)"
    finally:
        con.close()

def _kanonische_klasse_und_bestehender_code(task_id: str) -> tuple[str | None, str | None, str | None]:
    """Fund 2026-07-16 (Betreiber, nach 5 von 9 Klassennamen-Abweichungen +
    2 Regressionen bei Korrekturrunden): target_name_capitalized war bislang
    eine reine String-Rate-Berechnung aus dem task_id, die strategie_planung
    NIE abfragte -- obwohl der echte kanonische Klassenname dort steht
    (00_systemmap.md wird genau dorthin geschrieben). Diese Funktion holt
    (1) den verbindlichen Klassennamen aus strategie_planung.geplante_klasse
    und (2) den bereits ausgelieferten Code, falls diese Bestellung schon
    einmal geliefert wurde (fuer Korrekturrunden -- Betreiber: 'getesteter Code
    verliert sonst seinen Status und muss komplett neu geprueft werden').
    Gibt (kanonische_klasse, bestehender_code, ziel_pfad) zurueck, jedes
    Element None falls nicht vorhanden."""
    con = sqlite3.connect(DB_PATH)
    try:
        bestellung_id = task_id.replace("task_bestellung_", "") if task_id.startswith("task_bestellung_") else None
        if not bestellung_id:
            return None, None, None
        row = con.execute(
            "SELECT ziel_ordner, kanonischer_name FROM bestellungen WHERE bestellung_id=?",
            (bestellung_id,),
        ).fetchone()
        if not row or not row[1]:
            return None, None, None
        ziel_ordner, eigener_name = row

        klasse_row = con.execute(
            "SELECT geplante_klasse FROM strategie_planung WHERE kanonischer_name=? ORDER BY id DESC LIMIT 1",
            (eigener_name,),
        ).fetchone()
        kanonische_klasse = klasse_row[0] if klasse_row and klasse_row[0] else None

        ziel_pfad = Path(ziel_ordner) / eigener_name
        bestehender_code = ziel_pfad.read_text(encoding="utf-8", errors="ignore") if ziel_pfad.exists() else None
        return kanonische_klasse, bestehender_code, (str(ziel_pfad) if bestehender_code else None)
    finally:
        con.close()


DNA = """PROGRAMMIERER-DNA: ZERO CODE-CHANGE PARADIGMA.
1. Parameter (Modellnamen, Ports, Pfade, Schwellwerte) NIE hardcoden --
   immer aus einer Config-Quelle lesen.
2. Erzeuge modularen Code, der sich selbst registrieren kann.
3. PEP8-Docstrings sind zwingend -- sie sind der Wissens-Transfer an
   kuenftige Instanzen, die diesen Code lesen muessen.
4. PFAD-PORTABILITAET (ergaenzt 2026-07-15, Mentor-Review-Funde Teil 1+2):
   Eigene Ressourcen-Pfade (DB, Dateien) IMMER Path(__file__).parent-relativ,
   NIE os.path.expanduser("~/...") oder ein nackter relativer String als
   Default-Wert -- letzteres loest gegen das Arbeitsverzeichnis zur Laufzeit
   auf, nicht gegen den Modul-Ordner, und bricht je nach Aufrufort.
5. HAEUFIGE PYTHON-IDIOM-FEHLER (ergaenzt 2026-07-15, Mentor-Review Teil 3):
   Eine Methode, die mit "with self._x(...):" verwendet wird, MUSS den
   Decorator @contextlib.contextmanager haben (nicht nur yield enthalten) --
   sonst TypeError zur Laufzeit. Import contextlib nicht vergessen.
6. KONSTRUKTOR OHNE PFLICHT-ARGUMENTE (ergaenzt 2026-07-15, Mentor-Review Teil 5,
   etabliertes Muster aus Teil 2/3): Die Hauptklasse MUSS ohne Konstruktor-
   Argumente instanziierbar sein -- braucht sie ConfigLoader oder eine andere
   Abhaengigkeit, HOLT sie sich diese SELBST im __init__ (z.B.
   self._config = ConfigLoader()), NICHT als Pflicht-Parameter von aussen.
   Grund: die Pipeline-eigene Ausfuehrungs-Pruefung instanziiert die geplante
   Hauptklasse immer ohne Argumente -- ein Pflicht-Parameter fuehrt IMMER zu
   einem TypeError-Fund, unabhaengig davon ob der Code sonst korrekt ist.
7. KEINE SIMULIERTEN/DUMMY-IMPLEMENTIERUNGEN (ergaenzt 2026-07-19, Mentor-Stress-
   test nach Stromausfall, Fund bei router_agent.py UND -- trotz dieses bereits
   bekannten Funds -- ERNEUT bei theke_antwort.py): Wenn eine Anforderung einen
   echten Aufruf verlangt (LLM, Netzwerk, echte Weiterleitung an einen anderen
   Spezialisten), MUSS der Code das tatsaechlich tun -- keine Platzhalter-Rueckgabe
   ("Simulierte Antwort", "Dummy-Ergebnis wird in Produktion ersetzt", ein fest
   verdrahtetes {"status": "erfolgreich"} ohne echten Effekt). Ein Kommentar wie
   "in der Realitaet wuerde hier X passieren" ist ein Gestaendnis, kein Feature --
   wenn eine echte Implementierung im Rahmen der Aufgabe nicht moeglich ist, MUSS
   die Methode das als Fehler zurueckgeben (z.B. {"fehler": "..."}), NICHT einen
   erfundenen Erfolg vortaeuschen. Grund: solcher Code besteht den reinen Instanz-
   iierungs-Check UND wirkt beim Lesen fertig, taeuscht damit sowohl die Pipeline
   als auch den Mentor -- deutlich gefaehrlicher als ein offensichtlicher Stub.
"""
DNA = load_role_dna("coder", fallback=DNA)

def _vollstaendige_dna() -> str:
    """Fund 2026-07-19/20 (Betreiber: "wo ist ueberall das Lernen hinterlegt, und wie
    kann die Werft das SELBST machen, ohne dass ein Mentor danebenstehen muss?"):
    Regeln 1-7 sind fest im Quellcode verankert (stabil, mehrfach mentor-geprueft).
    Ab Regel 8 kommen NEUE Regeln aus `coder_dna_regeln` (werft.db) -- dort schreibt
    der Debugger (siehe debugger.py, SIMULATIONS_PATTERNS-Fund) SELBSTSTAENDIG neue
    Regeln hinein, sobald er ein deterministisches, bisher unbekanntes Fehlermuster
    findet. Bewusst OHNE Freigabe-Gate: das ist rein ADDITIV (macht nie schlechteren
    Code moeglich, nur eine zusaetzliche Einschraenkung) und beruht auf einer
    deterministischen Regex-Erkennung, nicht auf einem LLM-Urteil -- also kein
    Hallucinations-Risiko in der Erkennung selbst. Genau die Art Aenderung, bei der
    ein Mentor-Freigabe-Gate nur Reibung ohne Sicherheitsgewinn waere."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT regel_text FROM coder_dna_regeln ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return DNA
    nummer = 8
    zusatz = "\n".join(f"{nummer + i}. {text}" for i, (text,) in enumerate(rows))
    return DNA.rstrip() + "\n" + zusatz + "\n"


MISSION_TEMPLATE = """{dna}

MISSION: {knowledge_links}
ZIEL-IDENTITAET: {target_name}
TECHNISCHE SPEZIFIKATION: {spec_json}

AEHNLICHE FRUEHERE LOESUNGEN (aus der Wissens-Schicht, zur Orientierung):
{kontext}

TATSAECHLICHE SCHNITTSTELLE DEINER ABHAENGIGKEITEN (woertlich uebernehmen,
NICHT raten oder aus Aehnlichkeit ableiten -- Fund 2026-07-15, Teil 3 rief
eine nicht existierende Methode auf einer echten Abhaengigkeit auf, weil nur
die unscharfe VDB-Aehnlichkeitssuche oben als Quelle diente):
{abhaengigkeits_schnittstellen}

ANFORDERUNGEN:
1. Name der Hauptklasse: {target_name_capitalized}
2. Dateiname als Kommentar im Header: {target_name}.py
3. Vollstaendiger, lauffaehiger Code -- kein Platzhalter, kein "# TODO: implement this".
4. Ausfuehrlicher Docstring: was leistet dieses Modul, welche Methoden gibt es,
   wie wird es aufgerufen.
5. Aussagekraeftige Variablennamen, PEP 8.
6. Fehlerbehandlung via try-except ist zwingend fuer alles was fehlschlagen kann
   (Netzwerk, Datei, externe Aufrufe).

{fehlhistorie}

Antworte AUSSCHLIESSLICH mit sauberem, lauffaehigem Python-Code. Kein Fliesstext davor oder danach.
"""

# Fund 2026-07-21 (Mentor+Betreiber, Spec-Synthese-Testszenario): MISSION_TEMPLATE
# verlangt bedingungslos Python-Code (Anforderung 2 + Schlusszeile) -- fuer
# Bestellungen, deren Ziel kein Python-Modul ist (kanonischer_name endet nicht auf
# .py, z.B. ein konsolidiertes Markdown-Spec-Dokument), ueberstimmt diese feste
# Schlussanweisung selbst eine explizite Gegenanweisung im Bestellungstext (live
# reproduziert: Coder lieferte ein Python-Skript, das das Markdown angeblich
# erzeugen wuerde, statt es direkt zu schreiben). Eigene Weiche noetig: eigene
# Vorlage OHNE Python-Zwang fuer Nicht-Code-Ziele, statt zu hoffen dass die
# Bestellung die feste Vorlagen-Anweisung im Prompt uebertrumpft.
SPEC_TEMPLATE = """{dna}

AUFTRAG: {knowledge_links}
ZIEL-IDENTITAET: {target_name}
TECHNISCHE SPEZIFIKATION: {spec_json}

AEHNLICHE FRUEHERE LOESUNGEN (aus der Wissens-Schicht, zur Orientierung):
{kontext}

TATSAECHLICHE SCHNITTSTELLE/INHALT DEINER ABHAENGIGKEITEN (woertlich
uebernehmen, NICHT raten oder aus Aehnlichkeit ableiten -- Fund 2026-07-23,
Selbstbau-Experiment Variante B: dieser Abschnitt fehlte in SPEC_TEMPLATE
komplett, obwohl MISSION_TEMPLATE ihn schon seit 2026-07-15 hat -- der Coder
bekam bei Nicht-Python-Zielen dadurch NIE die real deklarierten
Abhaengigkeiten zu sehen, egal wie sorgfaeltig sie vorher deklariert wurden):
{abhaengigkeits_schnittstellen}

{fehlhistorie}

WICHTIG: Das Ziel dieser Bestellung ist KEIN Python-Modul, sondern ein
Dokument (Ziel-Dateiname: {target_name}). Schreibe den geforderten Inhalt
DIREKT -- keine Python-Klasse, keine Funktion, kein Code, der den Inhalt
irgendwann erzeugen wuerde. Antworte AUSSCHLIESSLICH mit dem fertigen
Dokumenten-Inhalt selbst (z.B. Markdown-Prosa mit Ueberschriften), kein
Python-Code, kein Fliesstext-Kommentar davor oder danach.
"""

# Fund 2026-08-04 (Betreiber-Auftrag, nach mehrfacher Kontext-Budget-Blockade bei
# theke_antwort.py, 778 Zeilen): KORREKTUR_TEMPLATE verlangt IMMER die
# VOLLSTAENDIGE Datei zurueck, auch wenn die Aenderung nur 1-2 Methoden
# betrifft -- bei grossen Dateien (>15000 Zeichen) sprengt das num_predict-
# Budget zuverlaessig (mehrfach live reproduziert, 3 verschiedene Infra-
# Workarounds versucht, keiner nachhaltig). Patch-Modus: nur die betroffene(n)
# Methode(n) an das Modell geben + zurueckerhalten, dann in Python spleissen --
# der Rest der Datei bleibt Byte-fuer-Byte unveraendert. Nur aktiv wenn (a)
# Datei gross genug ist UND (b) sich die betroffenen Methoden aus der Spec
# EINDEUTIG identifizieren lassen (Namen kommen in Spec-Text UND in der Datei
# vor) -- sonst (Ambiguitaet oder Extraktions-Fehler) faellt run_once() auf
# den alten, bewaehrten Voll-Datei-Pfad zurueck. Kein Risiko fuer kleine
# Dateien, die bisher zuverlaessig funktionierten.
_PATCH_MODUS_MIN_ZEICHEN = 15000

_PATCH_METHODEN_TEMPLATE = """{dna}

Fund 2026-07-16 (Betreiber): Neuschreiben statt Editieren macht bereits getesteten
Code wieder ungetestet. Die Datei ist zu gross fuer eine Voll-Antwort --
du bekommst NUR die betroffenen Methoden, nicht die ganze Datei.

AKTUELLER INHALT DER BETROFFENEN METHODE(N) (aus der bereits ausgelieferten,
getesteten Datei):
{methoden_auszug}

URSPRUENGLICHE BESTELLUNG:
{bestellung_text}

ZU BEHEBENDE(S) PROBLEM(E) (falls vorhanden):
{fehlhistorie}

Antworte NUR mit den korrigierten Methoden, jede EXAKT in diesem Format
(Methodenname exakt wie oben, mit derselben Einrueckung wie im Original):
### METHODE: <methodenname>
```python
<vollstaendiger neuer Methodenkoerper, inkl. def-Zeile und Docstring>
```

Keine anderen Methoden, keine Erklaerung davor/danach, keine dritte Methode
die nicht oben aufgefuehrt ist."""


def _extrahiere_methode(code: str, methodenname: str) -> tuple[int, int] | None:
    """Findet Start/Ende einer Methode im Quelltext (per Zeichen-Index).

    Fund 2026-08-04 (Mentor, Testlauf vor dem ersten echten Einsatz): die
    urspruengliche Version suchte das Ende nur ueber die naechste def/class/
    Decorator-Zeile -- Modul-Level-Code ZWISCHEN zwei Methoden (z.B. eine
    globale Registry-Variable) landete dadurch faelschlich INNERHALB des
    extrahierten Bereichs und waere beim Spleissen der vom Modell
    zurueckgegebenen (kuerzeren) Methode STILL GELOESCHT worden. Fix:
    zeilenweise scannen, Ende = erste NICHT-LEERE Zeile mit Einrueckung
    <= der eigenen Einrueckung, unabhaengig davon ob es def/class/Kommentar/
    Modul-Code ist -- das ist die tatsaechliche Grenze eines Funktionskoerpers,
    nicht nur der Spezialfall 'naechste Methode'.

    Gibt None wenn der Methodenname nicht EINDEUTIG (genau 1x) gefunden wird
    -- Ambiguitaet ist ein Fall fuer den sicheren Voll-Datei-Fallback, kein
    Rateversuch."""
    treffer = list(re.finditer(
        rf"^([ \t]*)def\s+{re.escape(methodenname)}\s*\(", code, re.MULTILINE
    ))
    if len(treffer) != 1:
        return None
    start = treffer[0].start()
    einrueckung = len(treffer[0].group(1))
    # Fund 2026-08-04 (2. Runde, Mentor): bei mehrzeiligen Signaturen (Param.
    # ueber mehrere Zeilen) hat die schliessende ")"+Doppelpunkt-Zeile oft
    # GERINGERE Einrueckung als der Funktionskoerper selbst -- ein direkter
    # Scan ab Ende der ERSTEN Zeile haette die eigene Signatur faelschlich
    # als "Ende der Methode" erkannt. Erst die VOLLE Signatur (bis zum
    # abschliessenden Doppelpunkt) ueberspringen, DANACH die Koerper-Grenze
    # suchen.
    sig_match = re.match(
        r"[\s\S]*?\)\s*(?:->\s*[^:\n]+)?:", code[start:]
    )
    koerper_start = start + (sig_match.end() if sig_match else 0)
    zeilen_ab_hier = code[koerper_start:].splitlines(keepends=True)
    offset = koerper_start
    for zeile in zeilen_ab_hier:
        if zeile.strip() and (len(zeile) - len(zeile.lstrip(" \t"))) <= einrueckung:
            return start, offset
        offset += len(zeile)
    return start, len(code)


def _identifiziere_patch_methoden(bestellung_text: str, bestehender_code: str) -> list[str]:
    """Kreuzabgleich: welche in der Spec ERWAEHNTEN Methodennamen existieren
    auch tatsaechlich (genau 1x) in der Datei? Nur diese Schnittmenge gilt als
    sicher identifiziert -- Namen nur aus dem Spec-Text zu raten waere
    dieselbe Fehlerklasse wie die Embedding-basierte Namensverwechslung vom
    07-31 (Ablauf/Struktur statt Wortnaehe/Vermutung).

    Fund 2026-08-27 (Mentor, werft_coder_patchmodus_grossmethoden_20260806-
    Abnahmekriterium): Bestellungstexte referenzieren Methoden in dieser
    Codebasis haeufig im Konvention "Datei.py::methodenname" OHNE folgende
    Klammer (z.B. "theke_antwort.py::_synthetisiere:") -- die reine
    Aufruf-Syntax-Erkennung "name(" fand solche Erwaehnungen NICHT, obwohl
    der Methodenname eindeutig benannt war. Ergebnis: ziel_methoden blieb
    leer, Patch-Modus griff nie, obwohl Datei UND Methode klar identifizierbar
    gewesen waeren."""
    erwaehnt = set(re.findall(r"\b(_?[a-zA-Z][a-zA-Z0-9_]*)\s*\(", bestellung_text))
    erwaehnt |= set(re.findall(r"::\s*(_?[a-zA-Z][a-zA-Z0-9_]*)\b", bestellung_text))
    vorhanden = set(m.group(1) for m in re.finditer(r"^\s*def\s+(\w+)\s*\(", bestehender_code, re.MULTILINE))
    treffer = sorted(erwaehnt & vorhanden)
    # Nur Methoden, die sich auch EINDEUTIG extrahieren lassen (kein Duplikat-Name).
    return [m for m in treffer if _extrahiere_methode(bestehender_code, m) is not None]


def _versuche_konstanten_patch(bestehender_code: str, bestellung_text: str) -> str | None:
    """Deterministischer Konstanten-Patch (kein LLM): erkennt einen
    '## Konstanten-Patch'-Abschnitt in bestellung_text mit exaktem ALT-/NEU-
    Textpaar. Nur wenn ALT GENAU EINMAL woertlich in bestehender_code
    vorkommt, wird direkt ersetzt -- sonst None (bestehender Patch-/Voll-
    Datei-Pfad greift unveraendert). Loest den Fall, den
    _versuche_patch_methoden()/_identifiziere_patch_methoden() strukturell
    nicht abdecken koennen: Modul-Konstanten statt def-Methoden."""
    muster = re.compile(
        r"##\s*Konstanten-Patch\s*\n+###\s*ALT\s*\n+```(?:python)?\n(.*?)\n```\s*\n+"
        r"###\s*NEU\s*\n+```(?:python)?\n(.*?)\n```",
        re.DOTALL,
    )
    treffer = muster.search(bestellung_text)
    if not treffer:
        return None
    alt, neu = treffer.group(1), treffer.group(2)
    if bestehender_code.count(alt) != 1:
        return None
    return bestehender_code.replace(alt, neu, 1)


def _versuche_patch_modus(
    bestehender_code: str, bestellung_text: str, fehlhistorie: str, primaer_modell: str,
) -> str | None:
    """Gibt den gespleissten Volltext zurueck bei Erfolg, sonst None (Aufrufer
    faellt dann auf den alten Voll-Datei-Pfad zurueck -- kein Absturz, kein
    stiller Datenverlust)."""
    if len(bestehender_code) < _PATCH_MODUS_MIN_ZEICHEN:
        return None
    ziel_methoden = _identifiziere_patch_methoden(bestellung_text, bestehender_code)
    if not ziel_methoden:
        return None

    auszug = "\n\n".join(
        f"# --- {name}() ---\n" + bestehender_code[_extrahiere_methode(bestehender_code, name)[0]:_extrahiere_methode(bestehender_code, name)[1]]
        for name in ziel_methoden
    )
    dna = _vollstaendige_dna()

    # Fund 2026-08-27 (Mentor, werft_coder_patchmodus_grossmethoden_20260806):
    # eine ueber viele Korrekturrunden gewachsene Fehlhistorie frisst unbegrenzt
    # Kontext-Budget und verdraengt damit genau das Ausgabe-Budget, das fuer die
    # VOLLSTAENDIGE Reproduktion einer grossen Zielmethode noetig ist --
    # _sicheres_num_predict() kappt num_predict_patch dann weit unter die
    # Methodengroesse, der Patch-Versuch schneidet ab, _wirkt_abgeschnitten()
    # erkennt das korrekt, aber der Aufrufer faellt danach auf den Voll-Datei-
    # Pfad zurueck, der beim selben Fehlhistorie-Volumen ebenso scheitert
    # (KORREKTUR_TEMPLATE-Fund 2026-08-04) -- Ergebnis: Datei komplett
    # unbearbeitbar. Fix: Fehlhistorie auf das Budget kuerzen, das NACH
    # Reservierung von DNA + Auszug + Bestellung + dem fuer die
    # Methodenwiedergabe noetigen Ausgabe-Budget noch uebrig ist. Die
    # JUENGSTEN Eintraege zaehlen am meisten (der naechste Versuch soll den
    # zuletzt beobachteten Fehler beheben, nicht den aeltesten) -- deshalb
    # vom ENDE her behalten, nicht vom Anfang.
    _output_budget = _geschaetzte_prompt_tokens(auszug) * 2 + 200
    _fest_budget = (
        _geschaetzte_prompt_tokens(dna)
        + _geschaetzte_prompt_tokens(auszug)
        + _geschaetzte_prompt_tokens(bestellung_text)
        + _output_budget
        + 400  # Template-Fliesstext + Sicherheitsmarge
    )
    _fehlhistorie_max_zeichen = max(0, _num_ctx() - _fest_budget) * 3
    if len(fehlhistorie) > _fehlhistorie_max_zeichen:
        gekuerzt_um = len(fehlhistorie) - _fehlhistorie_max_zeichen
        fehlhistorie = (
            f"[... {gekuerzt_um} Zeichen aeltere Fehlhistorie gekuerzt, nur die "
            "juengsten Eintraege sind fuer den naechsten Versuch am hilfreichsten ...]\n"
            + fehlhistorie[-_fehlhistorie_max_zeichen:]
        )

    prompt = _PATCH_METHODEN_TEMPLATE.format(
        dna=dna, methoden_auszug=auszug,
        bestellung_text=bestellung_text, fehlhistorie=fehlhistorie,
    )
    # Fund 2026-08-25 (Mentor, live durch beispiel_kurzantwort_relevanz_20260804
    # aufgedeckt): num_predict=2500 war FEST verdrahtet, unabhaengig von der
    # Groesse der zu patchenden Methode(n) -- _synthetisiere() allein braucht
    # bereits ~2800 Tokens nur um UNVERAENDERT reproduziert zu werden (laut
    # _geschaetzte_prompt_tokens), der Patch-Versuch schnitt mitten im
    # Methodenkoerper ab (kein schliessendes ``` je erreicht), die Regex fand
    # dadurch KEINEN Block -- Patch-Modus scheiterte still, Aufrufer fiel auf
    # den Voll-Datei-Pfad zurueck, der beim selben Fehlhistorie-Volumen
    # ebenfalls zu knapp budgetiert war (spec_failed). Fix: Budget an der
    # tatsaechlichen Groesse der Eingabemethoden bemessen (Ausgabe ist in der
    # Regel aehnlich lang wie die Eingabe, Sicherheitsfaktor 2 fuer neue
    # Logik+Marker-Overhead), weiterhin ueber _sicheres_num_predict gegen den
    # verfuegbaren Kontext gedeckelt.
    num_predict_patch = _sicheres_num_predict(
        prompt, max(2500, _geschaetzte_prompt_tokens(auszug) * 2 + 200)
    )
    antwort = ask_model(prompt, model=primaer_modell, temperature=0.1, num_predict=num_predict_patch,
                        host=host_fuer_rolle("coder"))
    bloecke = dict(re.findall(
        r"###\s*METHODE:\s*(\w+)\s*```(?:python)?\n([\s\S]*?)```", antwort
    ))
    if not bloecke or not set(bloecke) <= set(ziel_methoden):
        return None

    ergebnis = bestehender_code
    # Rueckwaerts (hinten nach vorne) spleissen, damit Indexverschiebungen
    # durch vorherige Ersetzungen nachfolgende Positionen nicht verfaelschen.
    for name in sorted(ziel_methoden, key=lambda n: _extrahiere_methode(bestehender_code, n)[0], reverse=True):
        if name not in bloecke:
            continue
        start, ende = _extrahiere_methode(bestehender_code, name)
        neuer_koerper = bloecke[name].rstrip() + "\n"
        ergebnis = ergebnis[:start] + neuer_koerper + ergebnis[ende:]

    if not _wirkt_abgeschnitten(ergebnis):
        return ergebnis
    return None


def _knoten_optimierung_besteht(
    bestehender_code: str,
    kandidat: str,
    target_name: str,
    method_names: list[str],
) -> bool:
    """Prueft gespleisste Patch-Methoden vor dem Schreiben isoliert."""
    if not method_names:
        return True
    with tempfile.TemporaryDirectory(prefix="werft-coder-node-") as temporary:
        source_path = Path(temporary) / f"{target_name}.py"
        source_path.write_text(bestehender_code, encoding="utf-8")
        project_map = crawl_project(temporary)
        pipeline = WerftMapPipeline()
        for method_name in method_names:
            nodes = [
                node
                for module in project_map["modules"]
                for node in module.get("nodes", [])
                if node["kind"] in {"function", "method"}
                and (node["name"] == method_name or node["name"].endswith(f".{method_name}"))
            ]
            if len(nodes) != 1:
                return False
            positions = _extrahiere_methode(kandidat, method_name)
            if positions is None:
                return False
            start, end = positions
            result = pipeline.pruefe_knoten_optimierung(
                temporary,
                nodes[0]["id"],
                textwrap.dedent(kandidat[start:end]),
            )
            if result["status"] != "freigegeben":
                return False
    return True


def _kartenpruefung_vor_schreiben(code: str, task_id: str) -> dict:
    """Prueft jede Python-Ausgabe in einer isolierten Kartenregion."""
    return WerftMapPipeline().pruefe_teilauftrag(
        {"task_id": task_id, "node_ids": []}, code
    )


KORREKTUR_TEMPLATE = """{dna}

Fund 2026-07-16 (Betreiber): Neuschreiben statt Editieren macht bereits getesteten
Code wieder ungetestet -- die folgende Aufgabe ist eine KORREKTUR an bereits
FUNKTIONIERENDEM, gelieferten Code, KEINE Neuentwicklung.

BEREITS GETESTETER, AUSGELIEFERTER CODE (NUR die unten geforderten
Aenderungen vornehmen -- ALLES andere WOERTLICH beibehalten:
Klassenname, Methodennamen, Imports, Struktur, Kommentare, Docstrings):
```python
{bestehender_code}
```

VERBINDLICHER KLASSENNAME (aus der Systemmap-Planung, NICHT vom Bestellungs-
Namen ableiten -- Fund 2026-07-16: 2 von 3 Korrekturrunden verloren zuvor
korrekte Klassennamen durch komplette Neuschreibung): {kanonische_klasse}

URSPRUENGLICHE BESTELLUNG/SPEC (Fund 2026-07-20, Scout-Portierung: bei einer
ERSTEN Korrekturrunde -- z.B. Portierung von Altcode, bereits als
bestehender_code vorgeseedet -- ist die Fehlhistorie unten noch LEER oder
enthaelt nur technische Vorfehler, NICHT die eigentlich gewuenschten
inhaltlichen Aenderungen. Ohne diesen Block hatte der Coder keinerlei
Information darueber, WAS inhaltlich zu aendern ist, und lieferte den
bestehenden Code unveraendert zurueck -- live beobachtet bei
beispiel_scout_portierung, 3 Korrekturrunden, 0 Aenderungen):
{knowledge_links}

ZU BEHEBENDE(S) PROBLEM(E) (aus vorherigen Pruef-/Ausfuehrungsrunden, falls
vorhanden -- ergaenzt die Bestellung oben, ersetzt sie NICHT):
{fehlhistorie}

Antworte mit dem VOLLSTAENDIGEN Datei-Inhalt NACH der Korrektur (nicht nur
dem geaenderten Ausschnitt) -- aber inhaltlich soll sich NUR aendern, was
oben explizit gefordert ist (Bestellung + Fehlhistorie). Kein Fliesstext
davor oder danach.
"""


def _extrahiere_code(antwort: str) -> str:
    """Entfernt Markdown-Codefences, falls das Modell sie trotz Anweisung liefert."""
    match = re.search(r"```(?:python)?\n(.*?)```", antwort, re.DOTALL)
    return match.group(1).strip() if match else antwort.strip()


def _num_ctx() -> int:
    """Fund 2026-07-20: vorher als Konstante dupliziert (Drift-Risiko, wenn nur
    einer der beiden Orte angepasst wird) -- jetzt bei jedem Aufruf frisch aus
    derselben Config-Quelle wie ollama_client.ask_model() gelesen."""
    return int(config.get("ollama.num_ctx", 16384))


def _geschaetzte_prompt_tokens(prompt: str) -> int:
    """Grobe Heuristik (keine echte Tokenisierung verfuegbar): ~3.5 Zeichen/Token
    fuer gemischten Python-Code+Deutsch-Text, mit Sicherheitsaufschlag (aufrunden)."""
    return -(-len(prompt) // 3)  # bewusst konservativ (3 statt 3.5) -- lieber Budget
    # unterschaetzen als ueberschaetzen und wieder abschneiden.


def _sicheres_num_predict(prompt: str, gewuenscht: int) -> int:
    """Fund 2026-07-19 (Betreiber-Auftrag, RouterAgent-Absturz-Root-Cause, 5-Why-
    Analyse): coder.num_predict_max wurde bisher UNGEPRUEFT an ask_model()
    durchgereicht. ask_model() setzt num_ctx=8192 FEST (ollama_client.py) --
    bei einer stark gewachsenen Fehlhistorie (RouterAgent: 15+ Korrekturrunden,
    Prompt real ~14.9k Zeichen / ~4250 Tokens) blieb far weniger Rest-Budget
    als die angeforderten 6144 Tokens (4250+6144=10394 > 8192). Das Modell
    wurde dadurch NICHT von num_predict gestoppt, sondern hart vom
    Kontextfenster mitten im Code abgeschnitten (SyntaxError: unterminated
    triple-quoted string, live reproduziert). Fix: Rest-Budget explizit
    berechnen, mit Sicherheitsmarge (200 Tokens fuer Antwort-Overhead), NIE
    mehr anfordern als tatsaechlich verfuegbar ist."""
    sicherheitsmarge = 200
    num_ctx = _num_ctx()
    rest_budget = num_ctx - _geschaetzte_prompt_tokens(prompt) - sicherheitsmarge
    if rest_budget < gewuenscht:
        print(f"coder: num_predict von {gewuenscht} auf {max(rest_budget, 256)} gekuerzt "
              f"(Prompt ~{_geschaetzte_prompt_tokens(prompt)} Tokens, num_ctx={num_ctx}) -- "
              f"sonst haette das Kontextfenster die Antwort abgeschnitten.")
        return max(rest_budget, 256)  # 256 als absolute Untergrenze -- darunter ist ohnehin
        # kein brauchbares Modul mehr moeglich, dann lieber sichtbar knapp als 0.
    return gewuenscht  # genug Platz -- unveraendert, NIE ueber die Konfiguration hinaus erhoehen.


def _wirkt_abgeschnitten(code: str) -> bool:
    """Deterministischer Vollstaendigkeits-Check per compile() -- billiger und
    frueher als der Debugger-Ausfuehrungs-Check (der erst NACH einem kompletten
    Pipeline-Durchlauf greift). Fund 2026-07-19: `len(code) >= 20` allein haette
    auch abgeschnittenen Code (endet mitten in einem Docstring/Statement) als
    Erfolg durchgewinkt -- SyntaxError bei compile() ist ein starkes, gunstiges
    Signal genau fuer diesen Fehlermodus (nicht fuer inhaltliche Fehler, die
    bleiben Sache des Analysten)."""
    try:
        compile(code, "<coder_check>", "exec")
        return False
    except SyntaxError:
        return True


def _generiere_code(prompt: str, primaer_modell: str, ziel_ist_python: bool = True) -> str:
    """Ruft den Coder mit Retry+Fallback bei leerer ODER abgeschnittener Antwort auf.

    Fund 2026-07-21 (Mentor+Betreiber, Spec-Synthese-Testszenario): die interne
    Abgeschnitten-Erkennung nutzt compile() (Python-spezifisch) -- fuer
    Nicht-Python-Ziele (ziel_ist_python=False) waere jeder Versuch faelschlich
    "abgeschnitten", weil Markdown-Prosa nie gueltiges Python ist. Fuer
    Nicht-Python-Ziele zaehlt nur noch die Mindestlaenge als Abbruchkriterium.

    Fund 2026-07-17 (vdb_client_projekt-Korrekturrunde): gpt-oss:20b lieferte
    wiederholt eine KOMPLETT LEERE Antwort (0 Byte) trotz kurzer, praeziser
    Korrekturanweisung -- nicht durch den num_predict-Deckel erklaerbar
    (Gesamt-Prompt weit unter num_ctx). Vermutlich ein Ollama/gpt-oss-
    spezifisches Thinking-Mode-Artefakt (aehnlich dem bekannten qwen3-Problem,
    das ask_model() schon mit think=False adressiert). Bisher EIN Einzelfall,
    keine ausreichende Stichprobe fuer eine R&D-Vergleichsstudie -- daher
    pragmatischer Retry+Fallback statt Modellwechsel: (1) EIN Retry auf
    demselben Modell (deckt Lastspitzen/GPU-Kontention ab, siehe manuell
    beobachtete haengende Ollama-Runner-Prozesse), (2) bei erneut leerer
    Antwort Fallback auf coder.model.fallback (Default qwen3:32b -- der
    Coder-Default VOR dem R&D-belegten Wechsel zu gpt-oss:20b, siehe
    mentor_actions_log action_type='coder_default_wechsel'). Haeuft sich das
    Muster kuenftig, ist eine echte gepaarte R&D-Studie (wie beim damaligen
    Coder-Wechsel) der naechste Schritt, keine dauerhafte stille Fallback-
    Krücke.

    Fund 2026-07-19: derselbe Retry+Fallback-Mechanismus deckt jetzt AUCH
    abgeschnittenen (syntaktisch ungueltigen) Code ab, nicht nur leere Antworten
    -- beide sind derselbe Fehlerklasse ('Modell konnte nicht vollstaendig
    antworten'), verdienen dieselbe Behandlung."""
    # Ohne explizite Fallback-Konfiguration beim zentral gewählten Primärmodell
    # bleiben; ein historischer Modellname darf keinen Live-Lauf brechen.
    fallback_modell = config.get("coder.model.fallback", primaer_modell)
    num_predict_gewuenscht = int(config.get("coder.num_predict_max", 6144))
    num_predict = _sicheres_num_predict(prompt, num_predict_gewuenscht)
    code = ""
    for versuch, modell in enumerate((primaer_modell, primaer_modell, fallback_modell)):
        try:
            antwort = ask_model(prompt, model=modell, temperature=0.1, num_predict=num_predict,
                               host=host_fuer_rolle("coder"))
        except Exception as exc:
            # Fund 2026-08-26: ein Netzwerk-/Timeout-Fehler (z.B. ReadTimeout
            # bei read timeout=300) wurde bisher NICHT abgefangen -- er propagierte
            # bis zu master_loop.py's aeusserem Catch-All durch, OHNE dass
            # run_once() je die spec_failed-Markierung erreichte. Wie eine leere
            # Antwort behandeln: naechster Versuch, kein Absturz.
            print(f"coder: Versuch {versuch + 1} ({modell}) schlug mit Verbindungsfehler fehl "
                  f"({exc}) -- verworfen, naechster Versuch.")
            code = ""
            continue
        code = _extrahiere_code(antwort)
        abgeschnitten = ziel_ist_python and _wirkt_abgeschnitten(code)
        if len(code) >= 20 and not abgeschnitten:
            if versuch > 0:
                print(f"coder: leere/abgeschnittene Antwort von {primaer_modell}, Erfolg bei Versuch {versuch + 1} ({modell})")
            return code
        if abgeschnitten:
            print(f"coder: Versuch {versuch + 1} ({modell}) lieferte syntaktisch abgeschnittenen Code "
                  f"({len(code)} Zeichen) -- verworfen, naechster Versuch.")
    print(f"coder: alle 3 Versuche ({primaer_modell} x2, {fallback_modell}) lieferten leere/zu kurze/abgeschnittene Antwort")
    return code


def graph_dateiname(kanonischer_name: str | None, target_name: str) -> str:
    """Fund 2026-09-14 (Sokrates, quad-ki-neu-Instanz, live verifiziert):
    call_graph_kanten-Abfragen trafen strukturell nie, weil der Coder unter
    dem internen Bestellungsnamen schrieb, der Debugger aber unter dem
    kanonischen Dateinamen las. Ein Namensschluessel fuer beide Seiten."""
    if kanonischer_name:
        return kanonischer_name
    return target_name if target_name.endswith(".py") else f"{target_name}.py"


def _aktualisiere_call_graph(target_name: str, code: str, task_id: str) -> int:
    """Touchpoint 1 der call_graph_kanten-Infrastruktur (Betreiber-Auftrag ueber
    Mentor, 2026-08-26): schreibt bei jeder erfolgreichen Code-Generierung
    'unsicher'-Kanten fuer self.<andere_methode>()-Aufrufe innerhalb derselben
    Datei -- bewusst klein angefangen (nur intra-Datei-Kanten), Cross-Modul-
    Aufloesung ist ein spaeterer Ausbauschritt. debugger.py promotet bei
    bestandenem ausfuehrungs_check auf 'in_pruefung', analyst.py auf 'ist'
    beim Merge (nie herabstufen) -- dieselbe Struktur wie
    analyst.py::_aktualisiere_systemmap() fuer die bestehende systemmap-Tabelle,
    nur granularer (Methoden- statt Datei-Ebene).

    Gibt die Anzahl geschriebener/aktualisierter Kanten zurueck."""
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

    # Fund 2026-08-26 (Betreiber-Nachfrage ueber Mentor, vor dem ersten Live-
    # Neustart): reine Beobachtungs-Schreibzugriffe wie dieser duerfen NIE den
    # eigentlichen Rollen-Ablauf blockieren oder crashen -- gleiche Konvention
    # wie ressourcen_status.py::protokolliere_check() (try/except sqlite3.Error,
    # timeout gegen unbegrenztes Warten bei DB-Lock). Ohne das wuerde ein reiner
    # Beobachtungsfehler (z.B. "database is locked") die nachfolgende
    # status='code_ready'-Aktualisierung in run_once() verhindern und einen
    # eigentlich fertigen Task grundlos stecken lassen.
    try:
        con = sqlite3.connect(DB_PATH, timeout=config.get("timeouts.sqlite_lock_sec"))
        try:
            for von_methode, nach_methode in kanten:
                con.execute(
                    "INSERT INTO call_graph_kanten "
                    "(von_datei, von_methode, nach_datei, nach_methode, status, quelle, task_id, aktualisiert_am) "
                    "VALUES (?, ?, ?, ?, 'unsicher', 'coder', ?, datetime('now')) "
                    "ON CONFLICT(von_datei, von_methode, nach_datei, nach_methode) DO UPDATE SET "
                    # Nie herabstufen: eine bereits von debugger/analyst hochgestufte
                    # Kante (in_pruefung/ist) bleibt es, auch wenn der Coder dieselbe
                    # Kante bei einer spaeteren Korrekturrunde erneut erkennt.
                    "status=CASE WHEN call_graph_kanten.status='unsicher' THEN 'unsicher' "
                    "ELSE call_graph_kanten.status END, "
                    "task_id=excluded.task_id, aktualisiert_am=excluded.aktualisiert_am",
                    (target_name, von_methode, target_name, nach_methode, task_id),
                )
            con.commit()
            return len(kanten)
        finally:
            con.close()
    except sqlite3.Error:
        # Beobachtung darf den Coder-Ablauf nie blockieren -- ein DB-Lock o.ae.
        # verhindert nicht die eigentliche Code-Auslieferung.
        return 0


def run_once() -> bool:
    """Holt einen Task mit status='spec', erzeugt Code, setzt status='code_ready'."""
    con = sqlite3.connect(DB_PATH)
    try:
        # Fund 2026-08-26 (Mentor+Betreiber, master_loop_persistent.log-Analyse):
        # additive, idempotente Schema-Sicherung -- kein Gate/keine separate
        # Migration noetig, ADD COLUMN schlaegt beim zweiten Aufruf einfach mit
        # OperationalError fehl, das wird bewusst geschluckt.
        try:
            con.execute(
                "ALTER TABLE task_pipeline ADD COLUMN coder_fehlversuche INTEGER DEFAULT 0"
            )
            con.commit()
        except sqlite3.OperationalError:
            pass
        # Fund 2026-08-26: ohne ORDER BY liefert SQLite praktisch immer denselben
        # (aeltesten/erst-eingefuegten) Task zurueck -- ein einzelner hartnaeckig
        # scheiternder Task blockiert dadurch alle anderen unbegrenzt. Tasks mit
        # wenigen/keinen bisherigen Coder-Fehlversuchen werden jetzt bevorzugt.
        row = con.execute(
            "SELECT task_id, knowledge_links FROM task_pipeline WHERE status='spec' "
            "ORDER BY coder_fehlversuche ASC, created_at ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        task_id, knowledge_links = row

        spec_marker = "SPEC_JSON:"
        spec_json = knowledge_links.split(spec_marker, 1)[-1] if spec_marker in knowledge_links else "{}"
        target_name = task_id.replace("task_", "")

        # Fund 2026-07-21 (Mentor+Betreiber, Spec-Synthese-Testszenario): Weiche VOR
        # der Prompt-Erstellung -- welches Ziel-Format wird ueberhaupt gefordert?
        # kanonischer_name aus der Bestellung ist die verbindliche Quelle (nicht
        # der interne task_name, der immer ohne Endung ist). Ohne Bestellung oder
        # ohne kanonischer_name: Python bleibt der Standardfall (Rueckwaertskompatibel
        # mit allen Bestellungen ohne explizites Ziel-Format).
        bestellung_id_fuer_weiche = task_id.replace("task_bestellung_", "") if task_id.startswith("task_bestellung_") else None
        ziel_ist_python = True
        if bestellung_id_fuer_weiche:
            kn_row = con.execute(
                "SELECT kanonischer_name FROM bestellungen WHERE bestellung_id=?",
                (bestellung_id_fuer_weiche,),
            ).fetchone()
            if kn_row and kn_row[0] and not kn_row[0].endswith(".py"):
                ziel_ist_python = False

        kanonischer_name_fuer_graph = kn_row[0] if (bestellung_id_fuer_weiche and kn_row) else None

        kontext_treffer = vdb.search(knowledge_links, top_k=3)
        kontext = "\n".join(f"- {t['text'][:200]}" for t in kontext_treffer) or "(keine)"

        kanonische_klasse, bestehender_code, _ = _kanonische_klasse_und_bestehender_code(task_id)
        naive_klasse = "".join(w.capitalize() for w in target_name.split("_"))

        # Strukturelle Absicherung: Fehlhistorie kommt garantiert aus derselben
        # Quelle, die der Analyst beim Ablehnen befuellt (memory.py).
        # Fund 2026-07-20 (Budget-Spirale router_agent.py/theke_antwort.py): bei
        # Korrekturrunden (bestehender_code vorhanden) steht der volle alte Code
        # UND die Fehlhistorie im selben Prompt -- kleineres Limit haelt Budget
        # frei fuer die geforderte VOLLSTAENDIGE Datei-Antwort. Bei Erstbau
        # (kein bestehender_code) bleibt das alte, groessere Limit.
        fehlhistorie = fehlhistorie_text(target_name, limit=4 if bestehender_code else 10)

        patch_code: str | None = None
        if bestehender_code:
            # Fund 2026-07-16 (Betreiber): Korrekturrunde auf bereits geliefertem,
            # getestetem Code -- editieren statt neu schreiben, verbindlichen
            # Klassennamen aus strategie_planung erzwingen statt zu raten.
            # Fund 2026-07-20 (Scout-Portierung, live im Test gefunden): der volle
            # knowledge_links-Text enthaelt NACH "SPEC_JSON:" denselben Inhalt
            # nochmal als JSON-Duplikat -- bei Korrekturrunden mit grossem
            # bestehendem Code sprengt das reine Verdoppeln das num_predict-Budget
            # (886 statt benoetigter ~1500+ Tokens, alle 3 Modellversuche lieferten
            # daraufhin leere/abgeschnittene Antworten). Nur den lesbaren
            # Fliesstext-Teil VOR dem JSON-Duplikat mitgeben.
            spec_marker = "SPEC_JSON:"
            bestellung_text = knowledge_links.split(spec_marker, 1)[0].strip()
            # Fund 2026-08-04: bei grossen Dateien zuerst Patch-Modus versuchen
            # (siehe _versuche_patch_modus) -- nur bei eindeutig identifizierten
            # Zielmethoden, sonst None und der bewaehrte Voll-Datei-Pfad greift
            # unveraendert.
            patch_code = _versuche_konstanten_patch(bestehender_code, bestellung_text)
            if patch_code is None:
                patch_code = _versuche_patch_modus(
                    bestehender_code, bestellung_text, fehlhistorie,
                    config.get("ollama.model.coder", "qwen3:32b"),
                )
            prompt = KORREKTUR_TEMPLATE.format(
                dna=_vollstaendige_dna(),
                bestehender_code=bestehender_code,
                kanonische_klasse=kanonische_klasse or naive_klasse,
                knowledge_links=bestellung_text,
                fehlhistorie=fehlhistorie,
            )
        elif not ziel_ist_python:
            # Spec-Synthese-Weiche: KEIN Python-Zwang, eigene Vorlage ohne die
            # hart codierte "Antworte AUSSCHLIESSLICH mit Python-Code"-Schlusszeile.
            prompt = SPEC_TEMPLATE.format(
                dna=_vollstaendige_dna(),
                knowledge_links=knowledge_links,
                target_name=target_name,
                spec_json=spec_json,
                kontext=kontext,
                abhaengigkeits_schnittstellen=_abhaengigkeits_schnittstellen(task_id),
                fehlhistorie=fehlhistorie,
            )
        else:
            prompt = MISSION_TEMPLATE.format(
                dna=_vollstaendige_dna(),
                knowledge_links=knowledge_links,
                target_name=target_name,
                target_name_capitalized=kanonische_klasse or naive_klasse,
                spec_json=spec_json,
                kontext=kontext,
                abhaengigkeits_schnittstellen=_abhaengigkeits_schnittstellen(task_id),
                fehlhistorie=fehlhistorie,
            )
        # num_predict-Deckel: Fund 2026-07-17 (Recycle-Pfad-Testlauf,
        # router_agent-Korrekturrunde) -- ask_model() ohne num_predict laesst
        # dem Modell den VOLLEN Kontext (num_ctx=8192) als Obergrenze; bei
        # einer Wiederholungsschleife (bekanntes Quantisierungs-Artefakt)
        # generiert es minutenlang statt sauber abzubrechen, was den 300s-
        # Timeout reisst und die ganze Master-Loop haengen liess. Ein Python-
        # Modul dieser Groessenordnung braucht keine 8192 Tokens; Deckel bei
        # 6144 begrenzt die Worst-Case-Laufzeit ohne normale Antworten
        # abzuschneiden. Retry+Fallback bei leerer Antwort: siehe _generiere_code().
        if patch_code is not None:
            print(f"coder: Patch-Modus erfolgreich ({target_name}) -- nur betroffene "
                  f"Methode(n) generiert+gespleisst, kein Voll-Datei-Aufruf noetig.")
            code = patch_code
        else:
            code = _generiere_code(prompt, config.get("ollama.model.coder", "qwen3:32b"), ziel_ist_python=ziel_ist_python)

        if patch_code is not None and ziel_ist_python and bestehender_code:
            patch_methods = _identifiziere_patch_methoden(bestellung_text, bestehender_code)
            if not _knoten_optimierung_besteht(
                bestehender_code, code, target_name, patch_methods
            ):
                remember_system(
                    owner="coder",
                    target=target_name,
                    content="Isolierte Landkartenpruefung hat den Patch-Kandidaten abgelehnt.",
                    tags="content_check_fail,node_optimization",
                )
                con.execute(
                    "UPDATE task_pipeline SET status='spec_failed', "
                    "coder_fehlversuche=coder_fehlversuche+1 WHERE task_id=?", (task_id,)
                )
                con.commit()
                return True

        # Fund 2026-07-19 (RouterAgent-Funktionstest nach dem num_predict-Fix):
        # _generiere_code() kann nach 3 erschoepften Versuchen (alle syntaktisch
        # kaputt/leer) IMMER NOCH den letzten, kaputten Code zurueckgeben --
        # bisher wurde der trotzdem klaglos als 'code_ready' weitergereicht und
        # verbrauchte einen vollen Analyst-LLM-Aufruf fuer etwas, das schon
        # lokal per compile() als kaputt bekannt war. Harte Sperre analog zur
        # bereits vorhandenen Leer-Code-Sperre in analyst.py: bei erschoepftem
        # Retry MIT weiterhin ungueltiger Syntax sofort spec_failed, mit einer
        # Begruendung die konkret genug ist, dass der naechste Versuch nicht
        # blind denselben Fehler wiederholt.
        # Fund 2026-07-21 (Mentor, Spec-Synthese-Testszenario): _wirkt_abgeschnitten()
        # nutzt compile() als Python-Syntax-Truncation-Indikator -- fuer Nicht-Python-
        # Ziele (ziel_ist_python bereits oben per Weiche bestimmt) schlaegt compile()
        # IMMER fehl, auch bei vollstaendigem, korrektem Inhalt (live reproduziert:
        # Markdown-Prosa faelschlich als "abgeschnitten" abgelehnt). Fuer Nicht-Python-
        # Ziele diese Pruefung ueberspringen -- Vollstaendigkeit/Qualitaet prueft dort
        # der Analyst-Taufe-Schritt per LLM-Urteil, nicht compile().
        # Fund 2026-08-26: compile("", ...) ist gueltiges Python (leeres
        # Programm) -- ohne Laengen-Check wuerde ein durch Verbindungsfehler
        # ausgeloester leerer Fallback-Code NICHT als abgeschnitten erkannt und
        # faelschlich als code_ready durchgewinkt. len(code)<20 nutzt dasselbe
        # Kriterium, das _generiere_code() intern schon fuer seine eigene
        # Retry-Entscheidung verwendet (Zeile 730), keine Verhaltensaenderung
        # fuer echte, vollstaendige Dateien (die immer weit ueber 20 Zeichen
        # liegen), schliesst nur die neue Luecke.
        if ziel_ist_python and (len(code) < 20 or _wirkt_abgeschnitten(code)):
            remember_system(
                owner="coder",
                target=target_name,
                content=(
                    f"Alle Generierungsversuche lieferten syntaktisch ungueltigen oder leeren Code "
                    f"(compile()-Fehler oder Verbindungsfehler bei allen 3 Versuchen) -- vermutlich "
                    f"reicht das num_predict-Budget "
                    f"({_sicheres_num_predict(prompt, int(config.get('coder.num_predict_max', 6144)))} "
                    f"Tokens bei diesem Prompt-Umfang) nicht fuer eine vollstaendige Datei, oder Ollama "
                    f"war ueberlastet/nicht erreichbar. Falls das wiederholt auftritt: Fehlhistorie ist "
                    f"vermutlich zu gross geworden (laenger als hilfreich) und sollte gekuerzt/"
                    f"zusammengefasst werden, nicht weiter unveraendert akkumulieren."
                ),
                tags="content_check_fail,code_abgeschnitten",
            )
            con.execute(
                "UPDATE task_pipeline SET status='spec_failed', "
                "coder_fehlversuche=coder_fehlversuche+1 WHERE task_id=?", (task_id,)
            )
            con.commit()
            return True

        if ziel_ist_python:
            kartenpruefung = _kartenpruefung_vor_schreiben(code, task_id)
            if kartenpruefung["status"] != "bestanden":
                remember_system(
                    owner="coder",
                    target=target_name,
                    content=(
                        "Kartenpruefung vor dem Schreiben fehlgeschlagen: "
                        + "; ".join(kartenpruefung["errors"])
                    ),
                    tags="content_check_fail,map_gate",
                )
                con.execute(
                    "UPDATE task_pipeline SET status='spec_failed', "
                    "coder_fehlversuche=coder_fehlversuche+1 WHERE task_id=?", (task_id,)
                )
                con.commit()
                return True

        output_dir = GENERATED_DIR
        output_dir.mkdir(exist_ok=True)
        (output_dir / f"{target_name}.py").write_text(code, encoding="utf-8")

        if ziel_ist_python:
            # Touchpoint 1 (call_graph_kanten, Betreiber-Auftrag 2026-08-26) --
            # nur fuer echte Python-Ziele sinnvoll, nicht fuer Markdown-/
            # Dokument-Bestellungen (ziel_ist_python-Weiche siehe oben).
            _aktualisiere_call_graph(graph_dateiname(kanonischer_name_fuer_graph, target_name), code, task_id)

        con.execute(
            "UPDATE task_pipeline SET status='code_ready', "
            "knowledge_links=knowledge_links || ' | CODE_PFAD:generiert/' || ? || '.py' "
            "WHERE task_id=?",
            (target_name, task_id),
        )
        con.commit()
        return True
    finally:
        con.close()


if __name__ == "__main__":
    bearbeitet = run_once()
    print("Task bearbeitet" if bearbeitet else "Keine offenen spec-Tasks")
