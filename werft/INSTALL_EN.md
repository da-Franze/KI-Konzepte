# Werft — Cold Start on New Hardware (Linux & Windows)

This package contains the Werft (build pipeline: accepts orders and builds
runnable Python code from them). No model is included — first-time setup
(step 6) detects your hardware and suggests suitable local Ollama models OR
an API provider.

Every step below is given for Linux (bash, `.sh` scripts) AND Windows
(PowerShell, `.ps1` scripts). Pick the column that matches your system.

## Directory Layout

```
werft/
├── core/      Python code, start/stop scripts, requirements.txt
├── config/    werft.ini + role personas (config/personas/)
├── docs/      supplementary documentation
├── vdb/       knowledge snapshot for the Qdrant knowledge layer
└── runtime/   created automatically on first start (werft.db etc.)
```

Almost every command below runs with `werft/core` as the working directory
— the Python modules there import each other by short name (e.g. `from
configloader import config`) and therefore expect `core/` to be on the
module search path.

## Prerequisites

- Python 3.12 or newer
- Docker (for the knowledge database Qdrant) — on Windows: Docker Desktop
  with the WSL2 backend enabled
- Optional: [Ollama](https://ollama.com) for local models (recommended from
  roughly 16 GB RAM/VRAM). Without Ollama, the Werft also works with an API
  provider (e.g. DeepSeek, OpenAI-compatible) — first-time setup in step 6
  will clarify this with you.

## 1. Get the Code

Unpack/clone the repository so that a `werft/` folder with the layout above
exists, and move into it.

```bash
cd werft
```
```powershell
Set-Location werft
```

## 2. Python Environment

The start scripts expect the Python interpreter in a specific place (see
the note at the end of this section). The simplest approach is to create
the virtual environment directly under `core/`:

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

On Windows, a virtual environment is activated with
`venv\Scripts\Activate.ps1` (instead of `source venv/bin/activate` on
Linux) — after that, the bare `python` command resolves to the venv without
spelling out the full path:

```powershell
.\core\venv\Scripts\Activate.ps1
```

**Note on `start_werft.sh`:** the bundled Linux start script currently
invokes the interpreter via a fixed path from the original development
machine (`~/ki_venv/bin/python3`), not relative to `core/venv`. Two options:

- **No code change:** additionally create the virtual environment at
  exactly that path: `python3 -m venv ~/ki_venv && ~/ki_venv/bin/pip
  install -r core/requirements.txt`.
- **One-line edit:** in `core/start_werft.sh`, replace
  `~/ki_venv/bin/python3` with `./venv/bin/python3` (the script already
  changes into `core/` before this line, so the relative path resolves
  correctly).

`start_werft.ps1` does not need this adjustment — it locates the
interpreter itself (`-PythonPath` parameter, otherwise falling back to
whatever `python` resolves to on `PATH`, which is correct automatically
once a venv is activated).

## 3. Start the Knowledge Database (Qdrant)

Docker Compose works identically on Linux and Windows:

```bash
docker compose -f core/docker-compose.qdrant.yml up -d
```
```powershell
docker compose -f core/docker-compose.qdrant.yml up -d
```

Check that Qdrant is running:

```bash
curl http://localhost:6333/collections
```
```powershell
Invoke-RestMethod http://localhost:6333/collections
```

## 4. Load the Werft's Own Knowledge (Snapshot as a Release Asset)

The snapshot of the Werft's own knowledge collection (architecture
insights, DNA-rule context — no private content) is NOT part of the
checkout, but a release asset (52.79 MB — would otherwise bloat the
repository unnecessarily). Download it once:

```bash
curl -L -o vdb/werft_wissen.snapshot \
  https://github.com/da-Franze/KI-Konzepte/releases/download/werft-v1.0/werft_wissen.snapshot
```
```powershell
curl.exe -L -o vdb\werft_wissen.snapshot `
  https://github.com/da-Franze/KI-Konzepte/releases/download/werft-v1.0/werft_wissen.snapshot
```

The collection name has to match `qdrant.collection` in `system_config`;
the shipped default for that is `werft_wissen_1024` (see
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

(Use `curl.exe` explicitly in PowerShell — the bare `curl` alias points to
`Invoke-WebRequest`, which uses a different multipart syntax.)

Check with `curl http://localhost:6333/collections/werft_wissen_1024` (or
`Invoke-RestMethod` on PowerShell) — it should report `"points_count"` > 0.

## 5. Create the Database

Creates `runtime/werft.db` with the full schema and default configuration.
Idempotent — safe to run again after a later update without overwriting
existing values.

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

## 6. Start the Werft

**Linux:**

```bash
nohup bash core/start_werft.sh > /tmp/master_loop_persistent.log 2>&1 &
nohup ./core/venv/bin/python3 core/spec_generator.py >> /tmp/spec_generator.log 2>&1 &
```

**Windows** (two separate windows/sessions, or via `Start-Process` in the
background):

```powershell
Start-Process powershell -ArgumentList "-File core\start_werft.ps1"
Start-Process -FilePath ".\core\venv\Scripts\python.exe" -ArgumentList "core\spec_generator.py"
```

`start_werft.sh`/`start_werft.ps1` keeps `master_loop.py` (the actual build
pipeline) running permanently and restarts it automatically whenever the
Werft's own code changes. `spec_generator.py` is the web interface (port
8891).

A bounded test run without permanent operation is available directly on
Windows:

```powershell
.\core\start_werft.ps1 -MaxCycles 1 -PauseSeconds 0
```

## 7. First-Time Setup (in the Browser)

```
http://<this-machine>:8891/setup
```

Scans hardware (GPU/VRAM, RAM, available Ollama models), recommends a model
tier, or suggests an API provider on weaker hardware. After confirmation
the models are set in `system_config` — the Werft uses them from the next
call on, no restart needed.

For a plain terminal check without a browser, the same scan is available
as a script:

```bash
./core/venv/bin/python3 core/ersteinrichtung.py
```
```powershell
.\core\venv\Scripts\python.exe core\ersteinrichtung.py
```

(This direct call only displays the recommendation and does NOT apply it —
that only happens via `/setup` in the browser, or by explicitly calling
`Ersteinrichtung.uebernehme()`.)

## 8. Submit Your First Order

```
http://<this-machine>:8891/
```

Chat coach (asks follow-up questions until a specification is complete) OR
"Load standard template" for a fixed form. After submission, the running
Werft instance picks up the order automatically (next cycle, default every
15s) — progress can be tracked via `runtime/werft.db` (table `bestellungen`)
or again via `/spec/formular/<bestellung_id>`. For day-to-day operation,
see [USER_GUIDE_EN.md](USER_GUIDE_EN.md).

## What Is Deliberately NOT in This Package

- No Ollama models (chosen individually per hardware, see step 7)
- No private VDB collections (only the `werft_wissen` snapshot, no personal
  memories/notes from the origin system)
- No API keys/credentials
- No production order history — after step 5, `runtime/werft.db` contains
  only enough data for the Werft to be immediately functional, not a real
  build history

## Known Gap: the `external_orders` Module Is Missing

`core/master_loop.py` and `core/bestellungen.py` import a module
`external_orders` (functions `import_orders()` and `write_result()`) that
is **not** included in `core/` in this release. Without it, `master_loop.py`
fails to start (an `ImportError` at module import time). Before production
use, either add this module (a minimal stub with those two functions'
signatures is enough to make the import succeed — the external-order
integration itself then simply stays inactive), or remove the import from
both files if the external integration is not needed.
