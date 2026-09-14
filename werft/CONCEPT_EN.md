# The Werft — Concept and Architecture

This document explains why the Werft ("shipyard" — the project keeps its
German working name throughout) is built the way it is, how it works, and
which decisions were made on purpose. For plain operation, see
[USER_GUIDE_EN.md](USER_GUIDE_EN.md); for installation, see
[INSTALL_EN.md](INSTALL_EN.md). Every claim here is derived from the code
itself (docstrings, comments, table schemas) — where something is unclear
or incomplete, that is stated explicitly rather than smoothed over.

## 1. Overview

The Werft is a build pipeline: you describe in prose what should be built —
an "order" — and a team of local language models, each acting in a fixed
role (Coordinator, Planner, Summarizer, Coder, Debugger, Analyst,
Strategist, Judge), turns that into runnable Python code, verifies it, and
delivers it into a target directory. No human writes code in this process
— the human's role is to formulate orders and to decide at a small number
of well-defined points where the Werft cannot proceed on its own.

The Werft is not a chatbot that happens to emit code. It's a pipeline with
persistent state (`werft.db`, more than 30 tables) that tracks a job across
multiple language-model calls, verification stages, and — when needed —
multiple correction rounds, until it is either delivered or handed to a
human for a decision. Every step leaves a trace in the database; nothing
lives only "inside a chat's context" and disappears afterward.

## 2. Architectural Principle

### 2.1 The Specification Describes the Concept, Not the Solution

An order consists of a prose description and a mandatory **acceptance
criterion** — executable Python code that decides success or failure
(a `## Acceptance Criterion` heading with a ` ```python ` block, checked by
both `debugger.ausfuehrungs_check()` and the Analyst's "christening" step).
The rest of the specification is meant to describe the DESIRED BEHAVIOR,
not to pre-empt the code itself — based on lived experience behind this
project, dictating the solution up front produces a worse result than
describing the goal and leaving the Coder free to implement it.

### 2.2 Deterministic Before LLM

A recurring pattern in the code: wherever a check can be decided
**objectively** (is a file syntactically valid Python? does the code
contain a known stub pattern? does it import a module that doesn't exist?),
it is decided via regex/AST/`compile()` — not via an LLM's judgment.
`code_crawler.py` is, in its own docstring, a "deterministic Python code
crawler"; the Debugger's pre-merge static checks run against fixed pattern
lists (`STUB_PATTERNS`, `SIMULATIONS_PATTERNS`,
`OLLAMA_THINK_MISPLACED_PATTERN`); `planer.py` detects contradictions in a
specification "deterministically via regex (ground truth instead of
guessing)". Language models are used only where a judgment call genuinely
requires discernment: whether code actually matches the specification's
intent (Analyst), how a vague gap description turns into a concrete
requirements document (Summarizer), how important an open gap is (Judge).

### 2.3 Non-Invention Policy

Several modules carry the same principle explicitly in their docstrings:
never invent a result where no evidence exists. `vdb_client.py` returns an
empty list on an unsuccessful search, "NO inventing". `judge.py` refuses to
record a rating without at least one sentence of concrete justification
(`memory.MIN_GRUND_LAENGE`, traced to a root-cause finding: an earlier
failure-history implementation allowed entries without a usable
justification, "every attempt was a blind restart"). `router.py` falls
back to a fixed default category on an ambiguous request rather than
guessing among the six defined categories. The same stance forces the
Coder to return an error dictionary when a real implementation genuinely
isn't possible, rather than faking a fabricated success (a Coder DNA rule,
see section 4).

### 2.4 Zero Hardcoding

`configloader.py` reads every operating parameter (model names, hosts,
timeouts, thresholds) from the `system_config` table in `werft.db`, never
from constants in the code. Its own docstring names the principle
"zero-hardcoding (G-4)": "model names, hosts, thresholds are NEVER kept in
the code, always read from `system_config` through this class." That makes
it possible to adapt model choice and timing behavior to very different
hardware without a code change — from a machine with a 24 GB GPU down to a
CPU-only install that instead relies on an API provider (see section 7).

## 3. The Roles

Simplified, every order moves through this lifecycle (status values live in
`task_pipeline.status`):

```
received → audit → spec → code_ready → merged → delivered
              │         │                  │
              └── (failure, retry) ────────┘
                            │
                review_pending / archived
                (retry limit reached, waiting for a human)
```

| Role | Module | Job |
|---|---|---|
| **Coordinator** | `master_loop.coordinator_schritt()` | Creates a task for every open, unresolved gap (`system_gaps`) — ordered by the importance the Judge assigned (`severity DESC`). Bounded by `pipeline.max_tasks_per_gap` to prevent a task explosion on a single gap (a documented historical incident produced 587 tasks for one gap in two hours, because an allow-list of "relevant" statuses had missed a newly introduced one). |
| **Planner** | `planer.py` | A pre-check that runs before the Summarizer: detects whether a specification needs to be broken into "digestible chunks", and whether it contains internal contradictions (e.g. a changelog that changes a value while the rule text further down still states the old one, unchanged). Purely regex-based, no LLM call for the detection itself. |
| **Summarizer** | `summarizer.py` | Translates a gap description or order into a concrete, structured requirements document (JSON). Preserves numbers, formulas, and exact relationships verbatim rather than distilling them — one documented finding shows an overly liberal Summarizer inventing details that then wrongly flowed into the specification. |
| **Coder** | `coder.py` | Writes the actual Python code, checked against both the specification AND the real interface of already-delivered dependencies (not a guessed similarity from the knowledge layer). Carries the DNA rules (section 4) and additionally reads `coder_dna_regeln` on every call — the only role with a rule set that grows on its own. |
| **Debugger** | `debugger.py` | Static verification BEFORE the Analyst's acceptance step: stub/simulation patterns, an isolated execution check (import + instantiation in a subprocess, with a timeout), per-target-folder pattern bans (`projekt_verbotene_muster`), a scan for destructive patterns inside the acceptance criterion itself. Automatically learns new Coder DNA rules for certain deterministic findings (section 6.2). |
| **Analyst** | `analyst.py` | The "christening": checks generated code against the specification's actual intent, and on success registers it in `specialists_v8` as well as a searchable entry in the knowledge layer. Per its own comment, the lifecycle is "embryo → audit → christening → registration." |
| **Strategist** | `strategie.py` | Plans beyond individual orders: splits large orders into parts (`zerlege_bestellung`), requests targeted corrections to orders already in progress (`fordere_korrektur`), and uses a Pareto cost cut to surface models/characters causing a disproportionate share of failures/cost (`bottleneck_scan` — returns only candidates; an actual model switch remains a human decision). |
| **Judge** | `judge.py` | Rates every open gap on a 1–10 scale with a mandatory justification and writes the result to `system_gaps.severity` — which drives the Coordinator's build order. |
| **Router** | `router.py` | An operational role outside the gap→merge cycle: classifies an external request into one of six categories (knowledge, code, numerics, web, pipeline, security). |

A full cycle (`master_loop.zyklus()`) runs in this order: ingest external
orders → order intake → Judge → Coordinator → requeue → Planner →
Summarizer/Coder (sorted with model-swap awareness, see section 7) →
Debugger → Analyst → delivery → Strategist → mentor auto-correction → log
maintenance. Every single step runs through a fault-tolerant wrapper
(`_sicher()`), so an exception in one role never brings down the entire
cycle.

## 4. The Character/DNA Layer

Every role has a "DNA" — a set of binding behavioral rules built into every
prompt. This DNA is deliberately **not** hard-wired into the Python code;
it lives portably in `config/personas/<role>.ini`
(`role_dna.load_role_dna()`, whose own docstring reads: "loads external
role DNA instead of hard-wiring it as operating logic"). Each module also
carries a more elaborate, historically grown fallback DNA in its own source
(with the reasoning behind each rule embedded, drawn from real incidents)
— that fallback only kicks in if the matching persona file is missing or
empty. In practice, the shorter, persona-based version overrides the
elaborate in-code fallback DNA whenever a persona file exists: the INI
files are the actual operating state, and the Python text is documentation
of original intent plus a safety net.

The Coder goes one step further: `coder._vollstaendige_dna()` appends every
row from the `coder_dna_regeln` table to the seven built-in rules, starting
at number 8. This table isn't only maintained by a human — the Debugger
**writes to it automatically** whenever it detects a new,
deterministically recognizable failure pattern (section 6.2). A role's DNA
can therefore grow at runtime, with no code change.

A complementary mechanism is `projekt_verbotene_muster`: a per-target-folder
list of **forbidden** (rather than recommended) regex patterns with a
justification — the negative counterpart to the positive DNA. A target
project can record its own project-specific restrictions that go beyond
the generic stub/simulation detection (for instance, an internal
vocabulary that hasn't been cleared for public code yet).

A third, loosely related layer is **character management**
(`spezialist_charakter`, `charakter_leistung` in `werft.db`,
`modellwahl_register.py`): fine-tunes or named model variants for specific
roles are selected based on measured metrics (`mu_quality`, success rate)
rather than manual configuration, falling back to the configured default
when no measurement data exists. The register's own docstring insists on
"no simulations: real DB interactions, real error handling."

## 5. The Code Map — Pivot of the Whole System

`code_crawler.py` and `werft_map_pipeline.py` are what let the Werft
**know** code before it changes it. The crawler reads a Python module
deterministically via `ast`/`symtable` and produces a "project map":
classes, functions, variables, and the call edges between them.
`WerftMapPipeline` connects this deterministic map-building to the
approval process: it persists the target map, the actual map, the diff,
and the sub-tasks derived from it in the runtime table
`project_map_versions`, and a sub-task approval only updates that same
record once it has passed verification.

This map is the precondition for one of the Werft's central design
decisions: **it only modifies unfamiliar code once it understands it.**
Without an up-to-date map (tables `systemmap`, `call_graph_kanten`), the
Planner would have to GUESS which parts of unknown code an order affects —
exactly the risk the map exists to remove. `werft_diagnose_fremdcode.py`
uses the same machinery for a related but distinct purpose: it lets the
Werft (via the same Analyst infrastructure) inspect and diagnose unfamiliar
code in a purely read-only way, without building or delivering anything —
a diagnostic tool, not a delivery path.

`product_archive.py` closes the loop for delivered code: every verified
delivery is archived with its code, project map, and a SHA-256 content
hash, versioned (`PRODUCT_ARCHIVE_DIR/<product>/<timestamp>-<hash-prefix>/`),
so a later order can reference a concrete, verified version instead of
re-guessing what "the current version" was.

## 6. Self-Healing

The Werft contains several independent mechanisms that resolve blockages
without human intervention — deliberately limited to cases where the risk
of an autonomous action is structurally bounded (purely additive,
reversible, or based on deterministic rather than judgment-based
detection).

### 6.1 Code-Hash Restart

`werft_startup.werft_code_hash()` builds a combined mtime fingerprint of
every top-level `.py` file in the `core/` folder (not `generiert/` — that
changes constantly through normal Coder work and isn't infrastructure
code). `master_loop.main()` compares this fingerprint against the value
recorded at its own startup on every cycle; on a mismatch, the process
exits cleanly (`sys.exit(0)`). An outer wrapper
(`start_werft.sh`/`start_werft.ps1`) keeps the process alive permanently
and restarts it after every exit with freshly imported code. The reason
for this split: Python caches imported modules for the lifetime of the
process — without this mechanism, a fix to the Werft's own code would only
take effect after a manual restart, which the code comments describe as
having repeatedly caused silent, wasted waiting on a fix that was never
actually active.

### 6.2 Autonomously Learned DNA Rules

The first time the Debugger detects a specific, deterministically
recognizable failure pattern (currently: a misplaced `"think"` parameter in
an Ollama API call, or generated code that admits to being a
simulation/mock), it automatically writes a new row into
`coder_dna_regeln` — with no approval gate. The code comment justifies this
explicitly: "a mere approval-gate PROPOSAL doesn't solve the core problem —
the lesson only reaches the DNA once a human manually signs off on it"
(quoting the project owner in the source: "if a mentor has to stand next to
it and keep reminding it, that isn't autonomy!"). The safety argument is
structural, not just asserted: the rule is purely additive (it can never
enable worse code, only add a restriction) and relies on regex detection
rather than an LLM's judgment — no hallucination risk in the detection
itself. Every autonomous change is still logged transparently in
`mentor_actions_log` (`status='auto_angewendet'`), never hidden.

### 6.3 Mentor Auto-Correction

`master_loop.mentor_autokorrektur_schritt()` gives stuck tasks
(`review_pending` or `archived`) one fresh attempt per day through the
Debugger and Analyst — WITHOUT the Coder regenerating anything, to avoid
the documented regression risk of a full rewrite on code that may already
be nearly correct. A hard overall cap
(`mentor_autokorrektur.max_versuche`, default 5) prevents a structurally
stuck case from being retried endlessly over many days without a human ever
addressing the actual cause — once the cap is hit, a real escalation is
logged instead of another automatic attempt.

### 6.4 Startup Sweep

`werft_startup.sweep()` runs once at every `master_loop` start (not per
cycle) and performs structural cleanup: killing duplicate `master_loop`
processes, checking database integrity, deleting orphaned function-registry
entries (one finding showed such an entry could repeatedly "poison" the
Coder with its own long-superseded pattern), closing stale debugger
findings, and checking the system map's consistency against the real file.
Deliberately additive/reversible and gate-free — the same risk category as
the autonomous Coder DNA rules.

### 6.5 Log Maintenance

`mentor_log_pflege.py` automatically closes escalations in
`mentor_actions_log` that have resolved themselves (the related order has
since been delivered) or are byte-identical duplicates of a recurring
message — only the most recent instance is left open. Explicitly
non-destructive: only the status is set, no row is deleted. Per the code
comment, the reason is that without this maintenance, genuinely open cases
drown among hundreds of long-resolved entries ("alarm fatigue").

## 7. Resource Management

The Werft is built for heterogeneous, often modest local hardware, not a
dedicated cluster. Three pieces carry that:

**First-time setup** (`ersteinrichtung.py`): a deterministic hardware scan
(RAM, GPU vendor/VRAM via `nvidia-smi`/`rocm-smi`, models already present
in Ollama) derives a tier recommendation (large/medium/small, or "API
recommended" on hardware too weak for local inference) — pure ground-truth
facts, no language-model judgment. An optional benchmark measures real
tokens/second for an already-present model using Ollama's own timing
fields, but makes no automatic decision on its own: "the interpretation of
what counts as 'fast enough' deliberately stays with the human." This
recommendation covers only six roles (Coder, Analyst, Summarizer, Router,
Strategist, plus a `theke` entry for a related project) — the Debugger and
Judge get their model default set independently, directly when the
database is created (see section 10, known gaps).

**Resource observation** (`ressourcen_status.py`): reads Ollama's `/api/ps`
endpoint to see which models are currently loaded on which processing
unit — pure observation, no decision, and per its own docstring
"ineffective (not broken)" when running against a pure API-provider setup
with no local Ollama.

**Swap-aware role ordering**
(`master_loop._sortiere_rollen_swap_bewusst()`, `_beobachte_modell_swap()`):
before a role calls a model, the Werft checks whether doing so would evict
an already-loaded model from the same processing unit, and reorders
execution within a cycle to avoid unnecessary repeated reloading — a
documented historical finding showed up to 54 eviction cycles since the
last restart, because several roles without their own host assignment
defaulted onto the same GPU.

## 8. Limits and Deliberate Design Decisions

**Slow on purpose.** A single pass queries several local models one after
another; an order takes minutes to hours, not seconds. In exchange, every
step is traceable and recorded in the database, not left in the volatile
context of a chat.

**The Werft does not evaluate WHAT is being ordered** (`bestellungen.py`,
section 12.1 of the internal specification) — only whether the order is
stated clearly enough to be built. Whether the underlying idea is a good
one is left to the human who places it.

**A human decision is a built-in part of the cycle, not a failure mode.**
`review_pending` is a deliberate safety valve against infinite loops, not
a bug — the flip side is that an overlooked `review_pending` task leaves
the Werft stalled on exactly that order, documented as lasting several
days at a stretch in a reference system.

**Known gaps, documented in the code itself** (left unfixed because
addressing them would go beyond a plain bug fix):

- The "archived" status value is written inconsistently in
  `task_pipeline` — sometimes as `'archived'` (English, e.g. in
  `master_loop.requeue_schritt()`), sometimes as `'archiviert'` (German,
  e.g. in `debugger.py`, which notes this itself as an intended migration
  that wasn't carried out everywhere).
- `ersteinrichtung.py`'s hardware-tier recommendation doesn't cover the
  Debugger or Judge (see section 7) — on very weak hardware, first-time
  setup can recommend "API recommended" for the core roles while the
  Debugger/Judge/Strategist still default to the database-seeded
  `qwen3:14b`.
- The module `external_orders` (for importing external orders and writing
  results back), imported by both `master_loop.py` and `bestellungen.py`,
  is **not included** in `core/` in this release — without it,
  `master_loop.py` fails to start. See [INSTALL_EN.md](INSTALL_EN.md) for
  the two possible interim fixes.

These gaps are named here on purpose instead of being presented as quietly
"fixed" — this document's usefulness depends on claiming only what can
actually be verified in the code.
