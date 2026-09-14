#!/usr/bin/env python3
from __future__ import annotations

"""
spec_generator.py — Spec-Generator fuer die Werft.

Betreiber-Auftrag (2026-07-17): eine Chat-/Upload-Oberflaeche, in der Spezifikationen
diskutiert und verfeinert werden koennen -- inspiriert von einem frueheren
manuellen Muster in einem Vorgaengerprojekt (Betreiber lud einer Claude-Instanz
mehrere Spezifikationen zum Abgleichen/Konsolidieren hoch), aber jetzt als
festes Werft-Werkzeug statt Ad-hoc-Chat.

Urspruenglich (bis 2026-07-27) loeste eine Einreichung zusaetzlich einen
eigenen Hintergrund-master_loop.main()-Lauf aus ("Autostart-Ersatz ohne
Cron-Daemon", damals loeste das die groesste offene Struktur-Luecke: die
Werft hatte noch KEINEN Autostart). Seit start_werft.sh als Dauerprozess
existiert, kollidierte dieser Zusatz-Trigger nur noch mit dessen laufender
master_loop-Instanz (DB-Lock-Kontention + unnoetiger Selbst-Neustart durch
deren Code-Hash-Aenderungs-Erkennung, live im Formular-Test 2026-07-27
reproduziert) -- entfernt (Betreiber-Go Tisch 17:04). Eine Einreichung legt nur
noch die `bestellungen`-Zeile an; die stehende Werft-Instanz uebernimmt sie
in ihrem naechsten Zyklus (Default 15s).

Architektur (bewusst getrennt von der Werft-DB fuer den Chatverlauf, analog
zu forum.py "getrennt vom Tisch"):
  - Chatverlauf: eigene lokale SQLite-DB (data/spec_generator.db)
  - Einreichung: schreibt DIREKT in werft.db.bestellungen (status='eingegangen')

API:
  GET  /                                -- HTML-Chat-Oberflaeche im Browser
  POST /spec/chat        {session_id, nachricht}         -> Coach-Antwort
  POST /spec/einreichen  {session_id, titel, spec_inhalt,
                           ziel_ordner, kanonischer_name?} -> bestellung_id
  GET  /spec/verlauf/{session_id}       -- Chatverlauf als JSON

Start:  ~/ki_venv/bin/python3 spec_generator.py
"""

import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from configloader import config  # noqa: E402
from ollama_client import ask_model  # noqa: E402
from spec_formular import STANDARD_VORLAGE  # noqa: E402
from ersteinrichtung import Ersteinrichtung  # noqa: E402
from runtime_paths import SPEC_DB_PATH, WERFT_DB_PATH  # noqa: E402

PORT = 8891
DB_PATH = SPEC_DB_PATH

app = FastAPI(title="Werft Spec-Generator")

COACH_PROMPT = """Du bist der Spezifikations-Coach der Werft. Deine Aufgabe:
Betreiber hilft eine Spezifikation so zu verfeinern, dass die Werft sie OHNE Rueckfrage
eigenstaendig umsetzen kann.

Prinzipien der Werft (aus gelebter Praxis, nicht verhandelbar):
1. KLEINTEILIG: eine Spezifikation soll aus hoechstens einem Modul mit wenigen
   (2-5) konkreten Methoden bestehen. Grosse Auftraege gehoeren in mehrere
   Teile zerlegt -- frage aktiv nach, wenn der Wunsch zu gross wirkt.
2. KANONISCHER NAME: schlage einen sprechenden Dateinamen UND Klassennamen vor
   (z.B. "content_filter.py" / "ContentFilter"), keine generischen Namen.
3. KONSTRUKTOR OHNE PFLICHTARGUMENTE: die Hauptklasse muss ohne Argumente
   instanziierbar sein (Abhaengigkeiten holt sie sich selbst, z.B. ueber
   ConfigLoader).
4. MASCHINENPRUEFBARES ABNAHMEKRITERIUM: am Ende jeder fertigen Spezifikation
   steht ein konkretes Beispiel (Methodenaufruf + erwartetes Ergebnis), an dem
   sich objektiv pruefen laesst ob der gebaute Code funktioniert.
5. ZERO-HARDCODING: Modellnamen/Pfade/Schwellwerte gehoeren in eine Config-
   Quelle, nie fest in den Code.

Stelle gezielte Rueckfragen, bis die Spezifikation diese 4 Punkte erfuellt.
Fasse am Ende NICHT selbst zusammen -- Betreiber entscheidet wann sie fertig ist
und reicht sie ueber den "Einreichen"-Knopf ein. Antworte kurz und konkret,
keine Floskeln."""


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
      str(DB_PATH), timeout=config.get("timeouts.sqlite_lock_sec")
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS spec_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        rolle TEXT NOT NULL,
        text TEXT NOT NULL,
        zeit TEXT NOT NULL)""")
    conn.commit()
    return conn


def _verlauf(session_id: str, limit: int = 20) -> list[dict]:
    conn = _db()
    rows = conn.execute(
        "SELECT rolle, text, zeit FROM spec_sessions WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    conn.close()
    return [{"rolle": r[0], "text": r[1], "zeit": r[2]} for r in reversed(rows)]


class ChatIn(BaseModel):
    session_id: str
    nachricht: str


class EinreichenIn(BaseModel):
    session_id: str
    titel: str
    spec_inhalt: str
    ziel_ordner: str = str(Path.home() / "zielprojekt")
    kanonischer_name: str | None = None
    geplante_klasse: str | None = None


@app.post("/spec/chat")
def chat(msg: ChatIn):
    if not msg.nachricht.strip():
        raise HTTPException(400, "nachricht darf nicht leer sein")

    conn = _db()
    conn.execute(
        "INSERT INTO spec_sessions(session_id, rolle, text, zeit) VALUES (?,?,?,?)",
        (msg.session_id, "nutzer", msg.nachricht.strip(), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    verlauf = _verlauf(msg.session_id, limit=20)
    dialog = "\n".join(f"{'Betreiber' if v['rolle'] == 'nutzer' else 'Coach'}: {v['text']}" for v in verlauf)
    antwort = ask_model(
        f"{COACH_PROMPT}\n\nBISHERIGER DIALOG:\n{dialog}\n\nCoach:",
        model=config.get("ollama.model.stratege", "qwen3:14b"),
        temperature=0.3,
        num_predict=int(config.get("ollama.num_predict.stratege", 800)),
    ).strip()

    conn = _db()
    conn.execute(
        "INSERT INTO spec_sessions(session_id, rolle, text, zeit) VALUES (?,?,?,?)",
        (msg.session_id, "coach", antwort, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"antwort": antwort}


@app.post("/spec/einreichen")
def einreichen(spec: EinreichenIn):
    if not spec.titel.strip():
        raise HTTPException(400, "titel darf nicht leer sein")
    if not spec.spec_inhalt.strip():
        raise HTTPException(400, "spec_inhalt darf nicht leer sein")

    bestellung_id = f"specgen_{uuid.uuid4().hex[:10]}"
    conn = sqlite3.connect(
      str(WERFT_DB_PATH), timeout=config.get("timeouts.sqlite_lock_sec")
    )
    try:
        conn.execute(
            "INSERT INTO bestellungen (bestellung_id, titel, spec_inhalt, quelle, "
            "ziel_ordner, kanonischer_name, status) VALUES (?,?,?,?,?,?, 'eingegangen')",
            (bestellung_id, spec.titel.strip(), spec.spec_inhalt.strip(),
             "Spec-Generator", spec.ziel_ordner, spec.kanonischer_name),
        )
        # Fund 2026-07-17 (eigener Testlauf): ohne strategie_planung-Eintrag
        # findet coder.py keinen verbindlichen Klassennamen (Lookup ueber
        # kanonischer_name) und faellt auf einen generischen, aus der
        # bestellung_id abgeleiteten Namen zurueck (z.B.
        # "BestellungSpecgen90e8d64b1b" statt "WortHaeufigkeit") -- derselbe
        # Bug, der zuvor bei einem aehnlichen Diagnose-Skript (Teil 10, direkt ohne
        # Strategen eingereicht) gefunden wurde. Fix an der Quelle: JEDE
        # Spec-Generator-Einreichung mit bekanntem Klassennamen bekommt sofort
        # ihren eigenen strategie_planung-Eintrag, ohne den Umweg ueber
        # zerlege_bestellung()/plan_freigeben() (das ist fuer Mehrteiler
        # gedacht, hier ist es ein einzelnes Modul).
        if spec.kanonischer_name and spec.geplante_klasse:
            conn.execute(
                "INSERT INTO strategie_planung (ueber_bestellung, teil_nr, kanonischer_name, "
                "geplante_klasse, geplante_methoden, abhaengigkeiten, status) "
                "VALUES (?,1,?,?,'[]','[]','verifiziert')",
                (bestellung_id, spec.kanonischer_name, spec.geplante_klasse),
            )
        conn.commit()
    finally:
        conn.close()

    conn = _db()
    conn.execute(
        "INSERT INTO spec_sessions(session_id, rolle, text, zeit) VALUES (?,?,?,?)",
        (spec.session_id, "system",
         f"Eingereicht als {bestellung_id} -- die laufende Werft-Instanz (start_werft.sh) "
         f"uebernimmt automatisch im naechsten Zyklus.",
         datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    # Fund 2026-07-27 (Betreiber-Go Tisch 17:04): frueher startete /spec/einreichen
    # HIER zusaetzlich einen eigenen Hintergrund-master_loop.main()-Lauf
    # (_trigger_werft()) -- das kollidierte mit dem laengst per start_werft.sh
    # DAUERHAFT laufenden master_loop (DB-Lock-Kontention + unnoetiger Selbst-
    # Neustart durch dessen Code-Hash-Aenderungs-Erkennung, live im Formular-
    # Test 2026-07-27 reproduziert). Kein eigener Trigger mehr noetig: die
    # stehende Werft-Instanz greift jede neue Bestellung ohnehin binnen ihres
    # naechsten Zyklus (Default 15s) auf.
    return {"bestellung_id": bestellung_id, "status": "eingegangen, laufende Werft-Instanz uebernimmt automatisch"}


@app.get("/spec/verlauf/{session_id}")
def verlauf(session_id: str):
    return {"verlauf": _verlauf(session_id, limit=200)}


@app.get("/spec/vorlage")
def vorlage():
    """Betreiber-Auftrag 2026-07-27 (Tisch 15:10, Teil b): standardisierte Spec-
    Vorlage als Alternative/Ergaenzung zum Chat-Coach -- besonders fuer einen
    ungeuebten Nutzer (Betreibers Bruder) einfacher als ein mehrstufiger Dialog,
    unabhaengig davon wie gut das dahinterliegende LLM gerade antwortet."""
    return {"vorlage": STANDARD_VORLAGE}


@app.get("/spec/formular/{bestellung_id}")
def formular(bestellung_id: str):
    """Zeigt eine vom Planer erkannte Formular-Rueckfrage (bestellungen.py::
    eingang_schritt, spec_formular.SpecFormular) fuer eine noch nicht
    eindeutig genug eingereichte Bestellung -- damit der Absender sieht was
    konkret zu ergaenzen ist, statt dass die Bestellung stumm bei
    status='eingegangen' haengen bleibt."""
    conn = sqlite3.connect(
      str(WERFT_DB_PATH), timeout=config.get("timeouts.sqlite_lock_sec")
    )
    try:
        row = conn.execute(
            "SELECT status, formular_vorschlag FROM bestellungen WHERE bestellung_id=?",
            (bestellung_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Bestellung nicht gefunden")
    status, vorschlag = row
    return {"bestellung_id": bestellung_id, "status": status, "formular_vorschlag": vorschlag}


class UebernehmenIn(BaseModel):
    provider: str
    modelle: dict[str, str]
    api_base_url: str | None = None
    api_key: str | None = None
    api_name: str = "api"


@app.get("/setup/scan")
def setup_scan():
    """Betreiber-Auftrag 2026-07-31 (Kaltstart auf beliebiger Ziel-Hardware, ZIP-
    Versand an NUC/Bruder): Hardware-Scan + Tier-Empfehlung fuer die
    Ersteinrichtungs-Seite (siehe ersteinrichtung.Ersteinrichtung). Rein
    lesend -- schreibt NICHTS, das macht erst /setup/uebernehmen nach
    ausdruecklicher Bestaetigung im Interface."""
    ei = Ersteinrichtung()
    scan = ei.scanne_hardware()
    empfehlung = ei.empfehle_modelle(scan)
    return {"scan": scan, "empfehlung": empfehlung}


@app.post("/setup/uebernehmen")
def setup_uebernehmen(wahl: UebernehmenIn):
    """Schreibt die im Interface bestaetigte Modell-/Provider-Wahl nach
    system_config (siehe ersteinrichtung.Ersteinrichtung.uebernehme). Bei
    provider='api' wird der Key NIE zurueckgegeben oder geloggt, nur der
    Dateipfad, in den er geschrieben wurde."""
    ei = Ersteinrichtung()
    try:
        return ei.uebernehme(
            wahl.modelle, provider=wahl.provider,
            api_base_url=wahl.api_base_url, api_key=wahl.api_key, api_name=wahl.api_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/setup", response_class=HTMLResponse)
def setup_seite():
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Werft Ersteinrichtung</title>
<style>
body{font-family:sans-serif;max-width:800px;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}
.box{border:1px solid #444;border-radius:8px;padding:1rem;margin-bottom:1rem;background:#1a1a1a}
input,select{width:100%;box-sizing:border-box;background:#222;color:#eee;border:1px solid #444;border-radius:4px;padding:0.5rem;margin-bottom:0.5rem}
button{background:#2a5;color:#fff;border:none;border-radius:4px;padding:0.6rem 1.2rem;cursor:pointer;margin-right:0.5rem}
button:hover{background:#3b6}
h2{color:#ccc}
label{display:block;margin:0.6rem 0 0.2rem;color:#aaa}
.warnung{color:#e0a060}
.ok{color:#a0e0a0}
table{width:100%;border-collapse:collapse;margin-top:0.5rem}
td{padding:0.2rem 0.4rem;border-bottom:1px solid #333}
</style></head>
<body>
<h2>Werft Ersteinrichtung -- Kaltstart</h2>
<p>Scannt diese Maschine (GPU/RAM/Ollama) und schlaegt passende Modelle vor,
bevor die Werft zum ersten Mal laeuft. <a href="/" style="color:#8ecfff">Zum Bestellungs-Chat &rarr;</a></p>
<div class="box">
  <h3>1. Hardware-Scan</h3>
  <div id="scan_ergebnis">laeuft...</div>
</div>
<div class="box">
  <h3>2. Empfehlung</h3>
  <div id="empfehlung_ergebnis"></div>
</div>
<div class="box">
  <h3>3. Provider waehlen</h3>
  <label><input type="radio" name="provider" value="ollama" checked onchange="providerWechsel()"> Lokal (Ollama)</label>
  <label><input type="radio" name="provider" value="api" onchange="providerWechsel()"> API (OpenAI-kompatibel)</label>
  <div id="api_felder" style="display:none">
    <label>API Base-URL</label><input id="api_base_url" placeholder="https://api.beispiel.de/v1">
    <label>API-Key</label><input id="api_key" type="password" placeholder="wird NIE in der DB gespeichert, nur in chmod-600-Datei">
    <label>Name (fuer die Key-Datei)</label><input id="api_name" value="api" placeholder="z.B. deepseek">
  </div>
</div>
<button onclick="uebernehmen()">Uebernehmen</button>
<p id="ergebnis"></p>
<script>
let letzteEmpfehlung = null;
async function scan(){
  const r = await fetch('/setup/scan');
  const d = await r.json();
  const s = d.scan;
  document.getElementById('scan_ergebnis').innerHTML =
    '<table>' +
    '<tr><td>OS</td><td>' + s.os + '</td></tr>' +
    '<tr><td>CPU-Kerne</td><td>' + s.cpu_kerne + '</td></tr>' +
    '<tr><td>RAM</td><td>' + (s.ram_gb ?? '?') + ' GB</td></tr>' +
    '<tr><td>GPU</td><td>' + (s.gpu_hersteller ? (s.gpu_hersteller + ', ' + s.gpu_vram_gb + ' GB VRAM') : 'keine erkannt') + '</td></tr>' +
    '<tr><td>Freier Speicher</td><td>' + s.disk_frei_gb + ' GB</td></tr>' +
    '<tr><td>Ollama</td><td>' + (s.ollama_installiert ? 'installiert' : 'NICHT gefunden') + '</td></tr>' +
    '</table>';
  letzteEmpfehlung = d.empfehlung;
  const e = d.empfehlung;
  let html = '<p class="' + (e.tier === 'api_empfohlen' ? 'warnung' : 'ok') + '">' + e.begruendung + '</p>';
  if(e.tier !== 'api_empfohlen'){
    html += '<table>';
    for(const [rolle, modell] of Object.entries(e.modelle)){
      html += '<tr><td>' + rolle + '</td><td>' + modell + '</td></tr>';
    }
    html += '</table>';
    if(e.fehlende_downloads.length){
      html += '<p class="warnung">Noch nicht lokal vorhanden, muss per <code>ollama pull</code> geladen werden: ' + e.fehlende_downloads.join(', ') + '</p>';
    }
    document.querySelector('input[name=provider][value=ollama]').checked = true;
  } else {
    document.querySelector('input[name=provider][value=api]').checked = true;
  }
  document.getElementById('empfehlung_ergebnis').innerHTML = html;
  providerWechsel();
}
function providerWechsel(){
  const provider = document.querySelector('input[name=provider]:checked').value;
  document.getElementById('api_felder').style.display = provider === 'api' ? 'block' : 'none';
}
async function uebernehmen(){
  const provider = document.querySelector('input[name=provider]:checked').value;
  const body = {provider, modelle: (letzteEmpfehlung && letzteEmpfehlung.tier !== 'api_empfohlen') ? letzteEmpfehlung.modelle : {}};
  if(provider === 'api'){
    body.api_base_url = document.getElementById('api_base_url').value;
    body.api_key = document.getElementById('api_key').value;
    body.api_name = document.getElementById('api_name').value || 'api';
  }
  const r = await fetch('/setup/uebernehmen', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  const d = await r.json();
  document.getElementById('ergebnis').innerHTML = r.ok
    ? ('<span class="ok">Uebernommen: ' + d.geschriebene_keys.join(', ') + '. <a href="/" style="color:#8ecfff">Weiter zum Bestellungs-Chat &rarr;</a></span>')
    : ('<span class="warnung">Fehler: ' + (d.detail || r.status) + '</span>');
}
scan();
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Werft Spec-Generator</title>
<style>
body{font-family:sans-serif;max-width:800px;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}
#chat{border:1px solid #444;border-radius:8px;padding:1rem;height:400px;overflow-y:auto;margin-bottom:1rem;background:#1a1a1a}
.msg{margin-bottom:0.8rem;white-space:pre-wrap}
.nutzer{color:#8ecfff}
.coach{color:#a0e0a0}
.system{color:#e0c060;font-style:italic}
textarea,input{width:100%;box-sizing:border-box;background:#222;color:#eee;border:1px solid #444;border-radius:4px;padding:0.5rem;margin-bottom:0.5rem}
button{background:#2a5;color:#fff;border:none;border-radius:4px;padding:0.6rem 1.2rem;cursor:pointer;margin-right:0.5rem}
button:hover{background:#3b6}
h2{color:#ccc}
</style></head>
<body>
<h2>Werft Spec-Generator</h2>
<p><a href="/setup" style="color:#8ecfff">&larr; Ersteinrichtung (Hardware/Modelle einrichten)</a></p>
<div id="chat"></div>
<textarea id="nachricht" rows="3" placeholder="Beschreib was die Werft bauen soll..."></textarea>
<button onclick="senden()">Senden</button>
<hr style="border-color:#444;margin:1.5rem 0">
<h2>Einreichen</h2>
<button onclick="vorlageLaden()" style="background:#456">Standard-Vorlage laden (statt Chat)</button>
<input id="titel" placeholder="Titel der Bestellung">
<input id="ziel_ordner" placeholder="Zielordner" value="/pfad/zum/zielprojekt">
<input id="kanonischer_name" placeholder="Kanonischer Dateiname (optional, z.B. mein_modul.py)">
<input id="geplante_klasse" placeholder="Klassenname (optional, z.B. MeinModul)">
<textarea id="spec_inhalt" rows="8" placeholder="Finale Spezifikation (aus dem Dialog oben zusammenfassen oder direkt einfuegen/hochladen) -- oder erst 'Standard-Vorlage laden' klicken und ausfuellen"></textarea>
<button onclick="einreichen()">An die Werft einreichen</button>
<p id="ergebnis"></p>
<script>
const sessionId = crypto.randomUUID();
const chat = document.getElementById('chat');
function anzeigen(rolle, text){
  const d = document.createElement('div');
  d.className = 'msg ' + rolle;
  d.textContent = (rolle === 'nutzer' ? 'Betreiber: ' : rolle === 'coach' ? 'Coach: ' : '') + text;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
}
async function senden(){
  const feld = document.getElementById('nachricht');
  const text = feld.value.trim();
  if(!text) return;
  anzeigen('nutzer', text);
  feld.value = '';
  const r = await fetch('/spec/chat', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({session_id: sessionId, nachricht: text})});
  const d = await r.json();
  anzeigen('coach', d.antwort);
}
async function vorlageLaden(){
  const r = await fetch('/spec/vorlage');
  const d = await r.json();
  const feld = document.getElementById('spec_inhalt');
  feld.value = (feld.value ? feld.value + '\\n\\n' : '') + d.vorlage;
}
async function einreichen(){
  const body = {
    session_id: sessionId,
    titel: document.getElementById('titel').value,
    spec_inhalt: document.getElementById('spec_inhalt').value,
    ziel_ordner: document.getElementById('ziel_ordner').value || '/pfad/zum/zielprojekt',
    kanonischer_name: document.getElementById('kanonischer_name').value || null,
    geplante_klasse: document.getElementById('geplante_klasse').value || null,
  };
  const r = await fetch('/spec/einreichen', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  const d = await r.json();
  document.getElementById('ergebnis').textContent = r.ok
    ? ('Eingereicht: ' + d.bestellung_id + ' -- ' + d.status)
    : ('Fehler: ' + (d.detail || r.status));
}
document.getElementById('nachricht').addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); senden(); }
});
</script>
</body></html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
