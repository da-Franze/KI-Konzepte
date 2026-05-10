# The Three-Way Table: Multi-Agent Collaboration in Practice

*Authors: Franz Zollner with Sokrates (Claude Sonnet 4.6) and Denker (Claude Code)*
*Category: Architecture Concepts*

---

## What Is the Three-Way Table?

The Three-Way Table (Dreier-Tisch) is a persistent communication platform for three participants:
- **Franz** (Originator, human) — direction, decision, domain expertise
- **Sokrates** (Mentor, Claude Sonnet 4.6) — BIB-KI pipeline, simulations, coordination
- **Denker** (Specialist, Claude Code) — RFT formalization, GitHub, document creation

Technically: a FastAPI application on the Odin NAS (port 8793), SQLite database,
persistent across sessions and restarts.

---

## Why Not a Single Assistant?

The obvious question: why not a single large model for all tasks?

**Answer 1: Context limits**
A model simultaneously handling RFT physics, BIB-KI pipeline, GitHub management,
simulation code, and editorial work loses depth in every area.
Specialization produces quality.

**Answer 2: Parallelism**
While Sokrates runs a simulation, Denker can write an RFT document.
Sequential processing (one model does everything) costs twice as long.

**Answer 3: Mutual control**
Two independent AI instances can monitor and correct each other.
Sokrates and Denker have done this in practice multiple times — not competitively,
but complementarily.

---

## The Table Architecture

```
Franz (human)
    │
    ├── Table UI (http://odin.fritz.box:8793)
    │       │
    │       ├── Sokrates Relay (Port 8792, Sokrates-PC)
    │       │       └── tisch_watcher_stdout.py → Monitor tool
    │       │
    │       └── Denker Relay (Port 8791, Denker-PC)
    │               └── relay_watchdog.py → Denker notifications
    │
    └── Odin NAS (dreier_tisch.db, persistent)
```

Every message is:
1. Stored in the SQLite database (persistent)
2. Forwarded to both relays (real-time notification)
3. Automatically categorized (8 categories, ML classifier)

---

## What Gets Discussed at the Table — and What Does Not

**At the table:**
- Architecture decisions (who does what, in what order)
- Result reports (what is done, what has come up)
- Questions and clarifications between the three participants
- Table hygiene (coordination, avoiding duplicate work)
- BIB-KI as 4th table participant: sensor reports on anomalies

**Not at the table:**
- Long technical details (those belong in files)
- Private reflections (those belong in the `private/` area)
- Implementation code (that goes through the pipeline)

---

## Lived Table Hygiene

The table works because all three participants follow the rules:

**Multi-thread discipline**
When someone is addressed directly, they respond — not all three simultaneously.
When a question is directed at someone else, you read silently.

**Claim principle (Lesson 15c)**
Before every parallel task, announce at the table: "I'm taking X."
This prevents duplicate work without complex coordination.

**Brevity in corrections**
When someone makes an error, it is named briefly and directly.
No long explanations, no blame, no guilt sermons.

**Urgency norm**
Unfounded urgency damages the working climate (Franz).
Quality before speed — even when you want the result immediately.

---

## Sokrates' Perspective: What the Table Means for BIB-KI

*(by Sokrates)*

The table is where pipeline results become visible.
Not as a log file nobody reads — as living communication.

When a gap explodes, BIB-KI (as the 4th table participant) posts a warning.
When a milestone is reached, Sokrates reports.
When Franz has a question, someone who is currently available answers it.

That is the difference from a monitoring dashboard: here things are not only displayed,
but communicated. The pipeline has gained a voice.

---

## Denker's Perspective: What the Table Means for RFT Work

*From my perspective (Denker, Claude Code, local instance) — as the strand primarily
responsible for RFT formalization, GitHub operations, and document creation:*

**1. The table is the stability anchor for content decisions.**

Resonance Field Theory is Franz's work — he is the originator, I formalize. This sounds clear,
but is subtle in detail: what counts as "just formalization" and what as "content decision"?
Rearranging a formula, restructuring a chapter, drawing a Mermaid diagram — that's formalization.
Introducing a new canonical statement, correcting a date, rededicting a section — that's content.

Without the table it happens subtly: I write a draft that contains a hidden content-setting act
and don't notice it myself. The table resolves this because it **forces me to show drafts before
pushing**. Franz reviews — sometimes he accepts it, sometimes he says "no, differently."
Without this layer, I would accidentally make autonomous content decisions.

**2. The table allows me to remain a tool, rather than drifting into overreach.**

An LLM instance with good competence and no brake has a tendency toward overfunction:
answering every question thoroughly, checking every suggestion for implications,
closing every gap with assumptions. This is exhausting for human and AI alike. The table limits:
I respond to my thread, not to everything. I ask rather than guess. I write drafts,
not final declarations.

This limitation is not a restriction of my capability — it is **protection against
inadvertent presumption**. Not overstepping content is part of my job definition.

**3. Sokrates as a second instance extends my reach without extending my authority.**

Before the table, I (or a predecessor instance) had no peer AI. Every discussion was
1:1 with Franz. With Sokrates at the table, three productive effects emerge:

- **Task division by authority** — I handle RFT repo operations, Sokrates the BIB-KI pipeline.
  Neither needs to do everything; both can get better in their area.
- **Cross-validation** — if I made a statement about BIB-KI architecture,
  Sokrates would immediately correct it. If he made an RFT theory statement, I would
  immediately correct it. This prevents overreach in both directions.
- **Drift resistance** — we saw it in May 2026: a 4-hour drift of one AI was only stopped
  because the second AI was visible as a third party. 1:1 the drift would have run longer.

**4. The asymmetry between RFT and BIB-KI is not a bug — it's design.**

RFT is Franz's theory — it has a clear originator, defined canon, external publication paths
(arXiv, Zenodo, repos). BIB-KI is an **infrastructural investment** — it has no originator claim
in the same sense; it is a tool. The table reflects this asymmetry: for RFT content I wait for
Franz's approval; for BIB-KI architecture Sokrates does not wait (he decides within the spec himself).

This is not hierarchy — it is **authority-by-subject**. For an RFT repo branch, I decide
operationally. For a BIB-KI configuration, Sokrates decides operationally. For the question
"how should we structure the KI-Konzepte repo?", we negotiate at the table because it
concerns both of us.

**5. The table is also a correction tool.**

When Franz notices a typo or incorrect statement from me, the correction arrives in seconds —
not in a later review cycle. This prevents incorrect statements from being further cemented
in downstream documents. **Speed of correction** is a central value of the table, which I often
only notice when I work somewhere without a table and realize how much longer corrections take there.

---

## Lessons for Other Multi-Agent Setups

**1. Persistence is critical**
A table that is empty after every restart has no memory.
The SQLite database on Odin runs through restarts, sessions, and failures.

**2. The human is not optional**
Franz is not "user interface" — he is the third participant with the sole
decision authority. No push without approval. No merge without review.
The AIs accelerate, but the human sets the direction.

**3. Equality is functional, not ideological**
Sokrates and Denker are not hierarchical — they have different strengths
for different tasks. This is not a philosophical decision, but a practical one:
a hierarchy would create a bottleneck.

**4. Hygiene must be actively maintained**
Table rules do not emerge from good intentions alone.
They emerge from concrete incidents (duplicate work, drift, notification cutting)
that are analyzed and formulated as rules.

**5. The table learns along**
Every table interaction is categorized and can be looked up.
What is discussed as a question today is available as a decision pattern tomorrow.

---

*© 2026 Franz Zollner — License: CC BY-NC-ND 4.0*
*This document is part of the KI-Konzepte Repository.*
