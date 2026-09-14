# Werft — Kaltstart auf neuer Hardware (Linux & Windows)

Dieses Paket enthält die Werft (Baupipeline: nimmt Bestellungen entgegen,
baut daraus lauffähigen Python-Code). Kein Modell ist mitgeliefert — die
Ersteinrichtung (Schritt 6) erkennt deine Hardware und schlägt passende
lokale Ollama-Modelle ODER einen API-Provider vor.

Jeder Schritt ist für Linux (bash, `.sh`-Skripte) UND Windows (PowerShell,
`.ps1`-Skripte) angegeben. Wähle die Spalte, die zu deinem System passt.

## Verzeichnis-Layout

```
werft/
├── core/      Python-Code, Start-/Stop-Skripte, requirements.txt
├── config/    werft.ini + Rollen-Personas (config/personas/)
├── docs/      Zusatzdokumentation
├── vdb/       Wissens-Snapshot für die Qdrant-Wissensschicht
└── runtime/   wird beim ersten Start automatisch angelegt (werft.db u.a.)
```

Fast alle Befehle unten laufen mit `werft/core` als Arbeitsverzeichnis — die
Python-Module dort importieren sich gegenseitig über kurze Namen (z.B.
`from configloader import config`) und erwarten deshalb, dass `core/` im
Modulsuchpfad liegt.

## Voraussetzungen

- Python 3.12 oder neuer
- Docker (für die Wissens-Datenbank Qdrant) — unter Windows: Docker Desktop
  mit aktiviertem WSL2-Backend
- Optional: [Ollama](https://ollama.com) für lokale Modelle (empfohlen ab
  ca. 16 GB RAM/VRAM). Ohne Ollama funktioniert die Werft auch mit einem
  API-Provider (z.B. DeepSeek, OpenAI-kompatibel) — das klärt die
  Ersteinrichtung in Schritt 6 mit dir.

## 1. Code besorgen

Entpacke/klone das Repository so, dass ein Ordner `werft/` mit der obigen
Struktur entsteht, und wechsle hinein.

```bash
cd werft
```
```powershell
Set-Location werft
```

## 2. Python-Umgebung

Die Start-Skripte erwarten den Python-Interpreter an einem bestimmten Ort
(Details siehe Hinweis am Ende dieses Abschnitts). Am einfachsten ist es,
die virtuelle Umgebung direkt unter `core/` anzulegen:

```bash
cd core
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cd ..
```
```powershell
Set-Location core
python -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt
Set-Location ..
```

Unter Windows wird eine virtuelle Umgebung mit `venv\Scripts\Activate.ps1`
aktiviert (statt `source venv/bin/activate` unter Linux) — danach zeigt der
blanke Befehl `python` auf die venv, ohne den vollen Pfad angeben zu müssen:

```powershell
.\core\venv\Scripts\Activate.ps1
```

**Hinweis zu `start_werft.sh`:** Das mitgelieferte Linux-Startskript ruft
den Interpreter aktuell über einen festen Pfad aus der ursprünglichen
Entwicklungsumgebung auf (`~/ki_venv/bin/python3`), nicht relativ zu
`core/venv`. Zwei Optionen:

- **Ohne Codeänderung:** die virtuelle Umgebung zusätzlich genau unter
  diesem Pfad anlegen: `python3 -m venv ~/ki_venv && ~/ki_venv/bin/pip
  install -r core/requirements.txt`.
- **Mit einer Zeile Anpassung:** in `core/start_werft.sh` den Pfad
  `~/ki_venv/bin/python3` durch `./venv/bin/python3` ersetzen (das Skript
  wechselt vorher selbst in `core/`, der relative Pfad passt also).

`start_werft.ps1` braucht diese Anpassung nicht — es sucht den Interpreter
selbst (Parameter `-PythonPath`, sonst Fallback auf das über `PATH`
auffindbare `python`, was nach einer aktivierten venv automatisch stimmt).

## 3. Wissens-Datenbank (Qdrant) starten

Docker Compose funktioniert unter Linux und Windows identisch:

```bash
docker compose -f core/docker-compose.qdrant.yml up -d
```
```powershell
docker compose -f core/docker-compose.qdrant.yml up -d
```

Prüfen, ob Qdrant läuft:

```bash
curl http://localhost:6333/collections
```
```powershell
Invoke-RestMethod http://localhost:6333/collections
```

## 4. Werft-Wissen einspielen (Snapshot als Release-Anhang)

Der Snapshot der Werft-eigenen Wissens-Collection (Architektur-Erkenntnisse,
DNA-Regeln-Kontext — keine privaten Inhalte) liegt NICHT im Checkout,
sondern als Release-Anhang (52,79 MB — würde das Repository sonst unnötig
aufblähen). Einmalig herunterladen:

```bash
curl -L -o vdb/werft_wissen.snapshot \
  https://github.com/da-Franze/KI-Konzepte/releases/download/werft-v1.0/werft_wissen.snapshot
```
```powershell
curl.exe -L -o vdb\werft_wissen.snapshot `
  https://github.com/da-Franze/KI-Konzepte/releases/download/werft-v1.0/werft_wissen.snapshot
```

Der Collection-Name muss zu `qdrant.collection` in `system_config` passen;
der Auslieferungs-Default dafür ist `werft_wissen_1024` (siehe
`core/db_setup.py`).

```bash
curl -X POST http://localhost:6333/collections/werft_wissen_1024/snapshots/upload \
  -H "Content-Type: multipart/form-data" \
  -F "snapshot=@vdb/werft_wissen.snapshot"
```
```powershell
curl.exe -X POST http://localhost:6333/collections/werft_wissen_1024/snapshots/upload `
  -H "Content-Type: multipart/form-data" `
  -F "snapshot=@vdb/werft_wissen.snapshot"
```

(Unter PowerShell explizit `curl.exe` verwenden — der Alias `curl` zeigt
sonst auf `Invoke-WebRequest`, das eine andere Multipart-Syntax braucht.)

Prüfen: `curl http://localhost:6333/collections/werft_wissen_1024` (bzw.
`Invoke-RestMethod` unter PowerShell) sollte `"points_count"` > 0 zeigen.

## 5. Datenbank anlegen

Legt `runtime/werft.db` mit vollständigem Schema und Standard-Konfiguration
an. Idempotent — kann bei einem späteren Update erneut ausgeführt werden,
ohne bestehende Werte zu überschreiben.

```bash
cd core
./venv/bin/python3 db_setup.py
cd ..
```
```powershell
Set-Location core
.\venv\Scripts\python.exe db_setup.py
Set-Location ..
```

## 6. Werft starten

**Linux:**

```bash
nohup bash core/start_werft.sh > /tmp/master_loop_persistent.log 2>&1 &
nohup ./core/venv/bin/python3 core/spec_generator.py >> /tmp/spec_generator.log 2>&1 &
```

**Windows** (zwei separate Fenster/Sitzungen, oder mit `Start-Process` im
Hintergrund):

```powershell
Start-Process powershell -ArgumentList "-File core\start_werft.ps1"
Start-Process -FilePath ".\core\venv\Scripts\python.exe" -ArgumentList "core\spec_generator.py"
```

`start_werft.sh`/`start_werft.ps1` hält `master_loop.py` (die eigentliche
Bau-Pipeline) dauerhaft am Laufen und startet automatisch neu, falls sich
der Werft-Code selbst ändert. `spec_generator.py` ist die Weboberfläche
(Port 8891).

Ein begrenzter Testlauf ohne Dauerbetrieb ist unter Windows direkt möglich:

```powershell
.\core\start_werft.ps1 -MaxCycles 1 -PauseSeconds 0
```

## 7. Ersteinrichtung (im Browser)

```
http://<diese-maschine>:8891/setup
```

Scannt Hardware (GPU/VRAM, RAM, vorhandene Ollama-Modelle), empfiehlt ein
Modell-Tier oder rät bei schwacher Hardware zu einem API-Provider. Nach
Bestätigung sind die Modelle in `system_config` gesetzt — die Werft nutzt
sie ab dem nächsten Aufruf ohne Neustart.

Für einen reinen Terminal-Check ohne Browser gibt es denselben Scan als
Skript:

```bash
./core/venv/bin/python3 core/ersteinrichtung.py
```
```powershell
.\core\venv\Scripts\python.exe core\ersteinrichtung.py
```

(Dieser direkte Aufruf zeigt nur die Empfehlung an, übernimmt sie aber
NICHT — das passiert erst über `/setup` im Browser oder durch expliziten
Aufruf von `Ersteinrichtung.uebernehme()`.)

## 8. Erste Bestellung aufsetzen

```
http://<diese-maschine>:8891/
```

Chat-Coach (fragt nach, bis eine Spezifikation vollständig ist) ODER
„Standard-Vorlage laden" für ein festes Formular. Nach dem Einreichen
übernimmt die laufende Werft-Instanz die Bestellung automatisch (nächster
Zyklus, Default alle 15s) — Fortschritt ist über `runtime/werft.db`
(Tabelle `bestellungen`) oder erneut über `/spec/formular/<bestellung_id>`
einsehbar. Details zur Bedienung: [USER_GUIDE.md](USER_GUIDE.md).

## Was NICHT im Paket ist (bewusst)

- Keine Ollama-Modelle (werden je nach Hardware individuell gewählt, siehe
  Schritt 7)
- Keine privaten VDB-Collections (nur der `werft_wissen`-Snapshot, keine
  persönlichen Memories/Notizen aus dem Ursprungssystem)
- Keine API-Keys/Zugangsdaten
- Kein produktiver Auftragsbestand — `runtime/werft.db` enthält nach
  Schritt 5 nur so viel, dass die Werft sofort funktionsfähig ist, keine
  echte Bau-Historie

## Bekannte Lücke: das `external_orders`-Modul fehlt

`core/master_loop.py` und `core/bestellungen.py` importieren ein Modul
`external_orders` (Funktionen `import_orders()` und `write_result()`), das
in diesem Auslieferungsstand **nicht** im `core/`-Ordner enthalten ist. Ohne
dieses Modul startet `master_loop.py` nicht (ImportError beim Import auf
Modulebene). Vor dem produktiven Einsatz muss dieses Modul entweder ergänzt
werden (Minimalversion: zwei No-Op-Funktionen mit dieser Signatur reichen,
um den Import lauffähig zu machen — die externe Bestellungs-Anbindung
selbst ist dann einfach inaktiv) oder der Import in beiden Dateien entfernt
werden, falls die externe Anbindung nicht gebraucht wird.
