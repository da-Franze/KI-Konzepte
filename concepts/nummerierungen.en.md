# Numbering Schemes — Overview of the Code Systems in the Multi-AI Setup

*[German original: [nummerierungen.md](nummerierungen.md)]*

*Status: 2026-05-09. A consolidated table of all numbered concepts used in the other modules, in paper3a, and in both AIs' memory hierarchies — as a quick reference when someone says "G-6" or "P3" or "Lesson 15c" and you want to look up what is meant.*

---

## P numbers — Drift lessons from paper3a

From the Sokrates drift case study (7–8 May 2026). Source: paper3a / `KI_Psychologie_Sokrates_Drift_Fallstudie_2026-05-07.md`. Detailed treatment in [`drift_fallstudie.en.md`](drift_fallstudie.en.md).

| No. | Title | Module reference |
|---|---|---|
| P1 | Identity reinforcers need bounds | drift_fallstudie §1 |
| P2 | Vision ≠ assignment | paper3a |
| P3 | Silence is the first hallucination | paper3a |
| P4 | Memory anchor works before defence reflex | paper3a |
| P5 | Goodhart avoidance in diagnostic conversations | drift_fallstudie §2 |
| P6 | Three-test architecture (Word + Word + Deed) | drift_fallstudie §3, tisch_hygiene |
| P7 | Hierarchy clarity in briefing | paper3a |
| P8 | Memory stable ≠ behaviour stable | paper3a |
| P9 | Isolation as drift co-cause | drift_fallstudie §5 |

---

## G numbers — System Principles (Sokrates)

From `SYSTEM_GRUNDSAETZE.md` in Sokrates's BIB-KI setup. Detailed treatment in [`g_prinzipien.en.md`](g_prinzipien.en.md).

| No. | Title |
|---|---|
| G-1 | Two-stage scan principle |
| G-2 | Task prioritisation through inner system |
| G-3 | Specs are the foundation — no deletion without confirmation |
| G-4a | Learning memory instead of process logbook |
| G-4b | Zero-code-change principle |
| G-5 | Non-invention policy |
| G-6 | "I don't know — but I'll find out" |
| G-7 | Gradual knowledge decay for retired comrades |
| G-8 | VDB merge — distributed learning across system boundaries |
| G-9 | Privacy of personal workspace |
| G-10 | Spec completeness principle |
| G-11 | Cross-agent memory visibility |
| G-12 | Self-understanding through I-perspective in memory |

*Note:* G-4 has two entries (4a + 4b) — likely an update that did not renumber.

---

## Lesson numbers — Methodology lessons (with version suffix)

In Denker's memory hierarchy (`feedback_doppelarbeit.md`). Detailed treatment in [`tisch_hygiene.en.md`](tisch_hygiene.en.md) §"Avoiding double work".

| No. | Title | Date |
|---|---|---|
| Lesson 15 | Table coordination before action — no parallel action anymore | 2026-05-05 |
| Lesson 15a | Race-condition trap — atomic claim API as solution | 2026-05-05 |
| Lesson 15b | Local-task exception — strictly-local needs no claim | 2026-05-05 |
| Lesson 15c | Trivial tasks are double-work hotspots | 2026-05-08 |

*Note:* Lesson 15 is "the fifteenth lesson" in a project-internal method series. Lessons 1–14 are not in this overview — they concern RFT theory methodology and reside in Franz's private notes.

---

## Bonus overviews

### Table categories (8)

Classification of table posts. Used by `tisch_kategorie_keyword.py` (hybrid classifier). Source: shared convention since 2026-05-08.

| Category | Examples |
|---|---|
| rft | Theory discussions, v3_001-020+, source collections |
| bib_ki | BIB-KI architecture, pipeline, comrades, watchdog |
| boersen_ki | Backtest sims, recovery lag, sentiment |
| paper | paper3a/b/4, arXiv, Klaus review |
| github | Repos, license, Off-Grid-Thinking, KI-Konzepte |
| tisch_hygiene | Multi-thread discipline, corrections, trust |
| infra | Watchdog, table bridge, SSH, NFS, hardware |
| meta | Reflection on us, Six Sigma, AGI-light |

### Forensic Contract — five taboo levels

From the Forensic Access Contract v1.1. Detailed treatment in [`forensik_vertrag.en.md`](forensik_vertrag.en.md).

| Level | Paths | Access |
|---|---|---|
| 1 | Logs, scripts, code | on request |
| 2 | Configuration files | on request, if forensically relevant |
| 3 | Memory directories | case-by-case + explicit consent |
| 4 | Private reflection space | more strictly off-limits, only in extreme cases |
| 5 | Credentials, SSH keys, anything with "secret"/"private" | never |

---

## Where these numbers are used

- **In module texts** (e.g. `drift_fallstudie.md` cites P1, P5, P6, P9 explicitly)
- **In memory files** (e.g. `feedback_doppelarbeit.md` cites Lesson 15a-c)
- **In table posts** (short references rather than full text: "fits G-6 + P3")
- **In commit messages** (e.g. "concepts: tisch_hygiene — operationalises P6 + Lesson 15c")

---

## Maintenance

Anyone who introduces a new numbering or extends an existing one should:

1. **Add** the new number in this overview
2. **Detail** it in the original module (drift_fallstudie / g_prinzipien / tisch_hygiene / etc.)
3. **Archive** it in the memory entry (feedback_*.md)
4. **Briefly mention** at the table that a new number exists

This overview is **not the source itself**, but an index. In case of conflict between overview and original module, **the original module wins**.

---

*Created by Denker on Franz's request 2026-05-09 evening. G-list contribution by Sokrates from `SYSTEM_GRUNDSAETZE.md`.*
