# The Werft — User Guide

What it can do, how to give it a job, how to follow a build, and what to do
when something stalls. Every number and workflow in this guide comes
straight from the running code, not from a guess — the example values below
are taken from a long-running reference installation; a freshly set-up
Werft will look different at first.

For setting it up on bare hardware, see [INSTALL_EN.md](INSTALL_EN.md).
This guide assumes that's done — in particular, that `runtime/werft.db`
already exists and that the Werft is started from `werft/`, with the code
and scripts living under `werft/core/`.

---

# Part 1 — What the Werft Does

## In One Sentence

**You order a piece of software in plain prose, and the Werft builds it** —
it writes the requirements, generates the code, checks it against the
specification, finds and fixes bugs, and delivers the finished file into a
target folder.

No human writes code in this process. The work is done by local language
models, each acting in a fixed role.

## The Life of an Order

```
  Order in prose
        │  status = received
        ▼
  Coordinator ──► creates the entry task              status = building
        │
        ▼
  Planner     ──► enriches: which existing code is affected?
  Summarizer  ──► turns the description into a concrete requirements doc
  Coder       ──► writes Python code
  Debugger    ──► static check BEFORE acceptance, catches bugs
  Analyst     ──► acceptance against the specification  ("christening")
        │
        ├── passed ──► merged ──► delivered to the target folder
        │                                          status = delivered
        └── failed ──► back to the Coder, up to the retry limit
                       after that ──► review_pending  (waiting for a human)
```

Two roles run alongside the main cycle: the **Strategist** plans beyond
individual orders, and the **Judge** rates how important each open gap is.
A **mentor auto-correction** step clears known blockages on its own.

## Example: The Balance Sheet of a Long-Running Reference System

```
Orders               124 delivered · 5 building · 3 archived · 2 received
Tasks                128 merged · 3 `archiviert` · 3 `archived` · 2 review_pending
```

(Two different literal status strings for the same thing, `archiviert`
German and `archived` English, both used inconsistently across the
codebase — not a translation artifact of this guide, a genuine quirk of
the underlying database; see the note on this in `debugger.py`.)

These numbers come from a Werft instance after several weeks of operation,
not the state right after a cold start. What matters here: the 2 orders
sitting in `review_pending` are the normal case, not a bug — that's where
**the Werft is waiting for a human decision**. Ignore them and the Werft
stalls — in one reference system that once lasted two full days before
anyone noticed.

## What It Remembers About Itself

`runtime/werft.db` has more than 30 tables. The ones that matter day to day:

| Table | Contents |
|---|---|
| `bestellungen` | the orders, including specification and target folder |
| `task_pipeline` | the individual build steps and their state |
| `debugger_findings` | discovered bugs, with a repeat counter |
| `systemmap`, `call_graph_kanten` | which code calls which — the map |
| `coder_dna_regeln` | rules the Coder must follow |
| `projekt_verbotene_muster` | patterns that must never appear in generated code |
| `mentor_actions_log` | what the Werft repaired on its own, and what it escalated |
| `spezialist_charakter`, `charakter_leistung` | characters and their measured quality |
| `judge_log` | importance ratings for open gaps |
| `strategie_planung` | the Strategist's longer-term planning |

The Werft **keeps its failure history**. That's deliberate:
`debugger_findings` counts how often the same finding recurred, and the
Werft learns from that count.

---

# Part 2 — Operation

## Starting and Stopping

The Werft consists of **two processes**:

```bash
cd werft

# 1. The build pipeline (keeps itself alive)
nohup bash core/start_werft.sh > /tmp/master_loop_persistent.log 2>&1 &

# 2. The web interface for placing orders (port 8891)
nohup ./core/venv/bin/python3 core/spec_generator.py >> /tmp/spec_generator.log 2>&1 &
```

On Windows, the equivalent uses the `.ps1` scripts (details and background
variants in [INSTALL_EN.md](INSTALL_EN.md), step 6):

```powershell
Set-Location werft
Start-Process powershell -ArgumentList "-File core\start_werft.ps1"
Start-Process -FilePath ".\core\venv\Scripts\python.exe" -ArgumentList "core\spec_generator.py"
```

`start_werft.sh`/`start_werft.ps1` are wrappers by design: `master_loop.py`
**exits on its own** as soon as its own code changes — otherwise the Python
runtime would keep the old imported modules cached indefinitely, and a fix
would have no effect until someone restarted it by hand. The wrapper
restarts it after every exit, with freshly loaded code. An exit here is
therefore normal, not a crash.

**Stopping (Linux):**

```bash
pkill -f start_werft.sh          # the wrapper first, or it just restarts things
pkill -f "from master_loop"
pkill -f spec_generator.py
```

**Stopping (Windows):** `core/stop_werft.ps1` cleanly stops the
`master_loop` process (it matches the command line via `Win32_Process`,
the same idea as `pkill -f`):

```powershell
.\core\stop_werft.ps1
Get-Process python* | Where-Object { $_.MainWindowTitle -match "spec_generator" } | Stop-Process
```

**Is it running?**

```bash
ps aux | grep -E "master_loop|spec_generator" | grep -v grep
ss -lptn 'sport = :8891'
tail -f /tmp/master_loop_persistent.log
```
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'master_loop|spec_generator' }
Get-NetTCPConnection -LocalPort 8891 -ErrorAction SilentlyContinue
Get-Content werft\runtime\master_loop_windows.log -Wait
```

## Placing an Order

### Way 1 — In the Browser (the usual way)

`http://localhost:8891`

The page has two halves:

**Top, "Werft Spec-Generator":** a chat. Describe in plain language what
should be built, and answer follow-up questions until the description is
complete. If you don't need that, click **"Load standard template (instead
of chat)"** and fill in the form directly.

**Bottom, "Submit":** a **"Submit to the Werft"** button. This sets the
order's status to `received`, and the next cycle picks it up.

### Way 2 — Directly Into the Database

For scripts and repeated submissions:

```bash
sqlite3 werft/runtime/werft.db "INSERT INTO bestellungen
  (bestellung_id, titel, spec_inhalt, quelle, ziel_ordner, status, created_at)
  VALUES ('my_order_01', 'Short title',
          '## Task
Description in prose.

## Acceptance Criterion
\`\`\`python
assert my_function(2) == 4
\`\`\`',
          'manual', '/path/to/target/project', 'eingegangen', datetime('now'))"
```

(Identical on Windows if `sqlite3.exe` is installed — otherwise a short
Python script using the built-in `sqlite3` module against
`werft\runtime\werft.db` works just as well.)

## What Makes a Good Order

This decides success or failure more than any setting.

**A specification describes the concept, not the solution.** Someone who
dictates the exact code gets a worse result than someone who describes the
goal.

**The acceptance criterion is mandatory** and must be executable Python
code inside a ```` ```python ```` block. It is what decides pass or fail.

Four traps, each one hit at least once, live, in a reference system:

1. **Only ONE `## Acceptance Criterion` section per order.** With more than
   one, the check picks the first code block after the first heading — and
   may end up testing something completely unrelated. One finished order
   stayed permanently blocked this way.

2. **No destructive patterns inside the acceptance criterion** — no
   `DELETE FROM`, no `DROP`. The Werft blocks these, and rightly so: a
   stored test criterion once wiped a production database in a reference
   system, with no backup. If you need to test against a database, create
   a second test database instead of deleting rows from the real one.

3. **Don't describe things in code notation.** If the prose casually
   mentions `myFunction()` and that function happens to exist in the
   target file, the Werft treats it as a change instruction. This happened
   three times in a row in a reference system — of all things, while
   drafting a correction that was meant to describe exactly this failure
   mode. So: *"the myFunction method"* rather than `myFunction()`.

4. **`spec_inhalt` and `knowledge_links` must stay in sync.** The Coder
   reads both. Update only one, and you get a build made from two
   contradictory versions of the spec.

## Tracking a Build

**From the command line** (database path: `werft/runtime/werft.db`):

```bash
# Where does each order stand?
sqlite3 werft/runtime/werft.db "SELECT bestellung_id, status FROM bestellungen
                  WHERE status != 'geliefert'"

# What are the tasks doing?
sqlite3 werft/runtime/werft.db "SELECT task_id, status, assigned_agent, coder_fehlversuche
                  FROM task_pipeline WHERE status NOT IN ('merged','archiviert')"

# What has the Debugger found?
sqlite3 werft/runtime/werft.db "SELECT * FROM debugger_findings ORDER BY id DESC LIMIT 10"

# What has the Werft repaired or escalated on its own?
sqlite3 werft/runtime/werft.db "SELECT * FROM mentor_actions_log ORDER BY id DESC LIMIT 10"

# Follow the log live
tail -f /tmp/master_loop_persistent.log
```

## Correcting an Order in Progress

If a build is heading in the wrong direction, you don't cancel it — you
correct it. The command has to run from `werft/core`, because
`bestellungen.py` imports its sibling modules by short name:

```bash
cd werft/core
./venv/bin/python3 -c "
import bestellungen
print(bestellungen.bestellung_korrigieren('my_order_01', '''
## Task
Corrected description.

## Acceptance Criterion
\`\`\`python
assert True
\`\`\`
'''))"
```

> **After a correction, the first failure is often just an echo of the
> old attempt.** The Coder may already have had a candidate in progress
> from before the correction. Wait one cycle before declaring the
> correction a failure.

## `review_pending` — When the Werft Is Waiting for You

Once a task hits the retry limit, it moves to `review_pending` and **stays
there until a human decides**. In the meantime, the Werft does nothing
further on that order.

```bash
sqlite3 werft/runtime/werft.db "SELECT task_id, status FROM task_pipeline WHERE status='review_pending'"
```

Then check why it failed — usually one of the four traps above — and
either fix the specification (the build then continues) or archive the
task.

Make this part of your daily routine. In one reference system, two tasks
once sat there for two days simply because nobody looked.

---

# Part 3 — When Something Stalls

## The Sequence

```bash
# 1. Is anything even running?
ps aux | grep -E "master_loop|spec_generator" | grep -v grep

# 2. What is the loop doing?
tail -30 /tmp/master_loop_persistent.log

# 3. Are the models reachable?
curl -s http://localhost:11434/api/tags | grep -c '"name"'

# 4. Is the Werft waiting on me?
sqlite3 werft/runtime/werft.db "SELECT count(*) FROM task_pipeline WHERE status='review_pending'"
```

On Windows, the equivalents are: process list via `Get-CimInstance
Win32_Process`, log via `Get-Content ... -Wait`, model reachability via
`Invoke-RestMethod http://localhost:11434/api/tags`; everything else is
the same, since the `sqlite3` CLI syntax is platform-independent.

## Common Cases

| Symptom | Cause | What to Do |
|---|---|---|
| nothing moves, no errors | tasks sitting in `review_pending` | see step 4, decide |
| order stays on `eingegangen` | no usable specification — the Werft deliberately doesn't guess | add an acceptance criterion |
| task fails again every few minutes | acceptance criterion unmeetable or wrong | read `debugger_findings` |
| code is built but never delivered | not all of the order's tasks are `merged` | check the order's task list |
| a correction has no effect | candidate is from the old spec version | wait one cycle |
| process keeps exiting | **normal** if the Werft's own code changed | log reads "restarting in 3s" |
| models are competing, everything is sluggish | several roles on the same Ollama host | check the per-role host assignment |

## Reading the Retry Counter Correctly

The Debugger's escalation messages sometimes cite a misleading number
("after 11 retries"). The count is tied to the task ID, **not to the type
of error** — a long-resolved old finding keeps counting toward a
completely new, unrelated failure on the same task. So: take the number as
a hint, and the **error text** as the truth.

---

# Part 4 — Limits and Ground Rules

**The Werft only builds what is verifiable.** Without an executable
acceptance criterion, there is no acceptance. Vague wishes ("improve error
handling") lead to generic code that fails, and the task keeps getting
regenerated. If no concrete test criterion can be derived from a
description, it isn't an order yet.

**It only changes unfamiliar code once it understands it.** Before an
order can touch code the Werft doesn't already know, the map has to be
accurate — `systemmap` and `call_graph_kanten`. Otherwise the Planner has
to guess which parts are affected.

**It is slow, and that's the price.** A single pass queries several local
models one after another. An order takes minutes to hours, not seconds.
In exchange, every step is traceable and recorded in the database.

**What belongs to the Werft, and what doesn't.** Code the Werft built is
never patched by hand — that's what a correction order is for. A quick
manual edit to generated code bypasses the very check the Werft exists to
perform. Changes to the Werft itself — pipeline, prompts, tooling — are a
separate matter and aren't affected by this rule.

---

# Part 5 — Cheat Sheet

```bash
# Start
cd werft
nohup bash core/start_werft.sh > /tmp/master_loop_persistent.log 2>&1 &
nohup ./core/venv/bin/python3 core/spec_generator.py >> /tmp/spec_generator.log 2>&1 &

# Stop (wrapper first!)
pkill -f start_werft.sh && pkill -f "from master_loop" && pkill -f spec_generator.py

# Place an order
xdg-open http://localhost:8891

# Status
sqlite3 werft/runtime/werft.db "SELECT status, count(*) FROM bestellungen GROUP BY status"
sqlite3 werft/runtime/werft.db "SELECT status, count(*) FROM task_pipeline GROUP BY status"
sqlite3 werft/runtime/werft.db "SELECT count(*) FROM task_pipeline WHERE status='review_pending'"
tail -f /tmp/master_loop_persistent.log

# Diagnostics
sqlite3 werft/runtime/werft.db "SELECT * FROM debugger_findings ORDER BY id DESC LIMIT 10"
sqlite3 werft/runtime/werft.db "SELECT * FROM mentor_actions_log ORDER BY id DESC LIMIT 10"
```

**Ports:** Spec-Generator 8891 · Qdrant 6333 · Ollama 11434 (default; further
instances may run on other ports)
**Database:** `werft/runtime/werft.db` — 30+ tables
**Knowledge layer:** Qdrant collection `werft_wissen_1024`
**Roles per cycle:** order intake → Judge → Coordinator → requeue → Planner
→ Summarizer/Coder → Debugger → Analyst → delivery → Strategist → mentor
auto-correction
