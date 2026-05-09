# Memory Hierarchy and BOOTSTRAP — Avoiding Cold-Start Latency

*How an AI instance becomes operational within 1 minute after each session restart, instead of spending 10 minutes hunting for paths.*

*[German original: [memory_und_bootstrap.md](memory_und_bootstrap.md)]*

---

## The problem being addressed

An LLM instance per se has no persistent memory. Every new session starts from zero — even if the person working with it had the same conversation just hours ago. What the instance needs as context (paths, conventions, lessons learned from earlier mistakes) must be **explicitly supplied** at every restart.

This has two consequences:

1. **Cold-start latency.** Without preparation, a new instance spends 5–10 minutes hunting for the most important paths and conventions — tokens are spent on reorientation rather than on the actual work.

2. **Knowledge loss between sessions.** What an instance learns today (a correction from the human, an annotation that proved successful, a trap avoided) is gone after the restart, unless it was filed **outside the session**.

The memory hierarchy is the answer to both problems.

---

## The layers of the memory hierarchy

Memory in our setup consists of several mutually complementary layers:

### Layer 1: Constitution (CLAUDE.md)

The central project file is automatically loaded at the start of every session. It contains:

- The project identity
- Critical technical rules (e.g. "use only `requests`, not `qdrant_client`")
- Directory structure
- Conventions that cannot be derived from the code

Style: procedural, prescriptive. "If X happens, do Y."

### Layer 2: Memory index (MEMORY.md)

A flat list pointing to individual memory entries. Maximum one line per entry, with a brief characterisation. Functions like a table of contents: a quick scan suffices to decide which deeper file is relevant.

Example form:

```markdown
- [Reference: Odin SSH access](reference_odin_ssh.md) — pubkey, paths, backup PW
- [Feedback: Avoiding double work](feedback_doppelarbeit.md) — table coordination before action
- [Reference: Lineage of start protocols](reference_stammlinie_startprotokoll.md) — v1 → v6.2
```

The index is **always** loaded; the individual deep files **only on demand**. That is the token-ROI: a flat index costs little, a complete memory hierarchy would be too expensive.

### Layer 3: Deep memory (typed individual files)

Each memory entry is its own Markdown file with YAML frontmatter and a clear type:

| Type | Purpose | Example |
|---|---|---|
| `user` | Personal profile information | "Franz is originator of X" |
| `feedback` | Learned rules from corrections | "For tasks given to both AIs: coordinate first" |
| `project` | Project-specific context | "Current roadmap, deadlines" |
| `reference` | Pointers to external sources | "SSH setup on host X, see Y" |

Style: narrative or structured, depending on content. Important: every `feedback` memo contains a **"Why"** field and a **"How to apply"** field, so that even edge cases can be judged — the bare rule is not enough.

### Layer 4: BOOTSTRAP file

The BOOTSTRAP file is a **"single-file answer to: what do I need to know right now?"** It is more compact than the memory hierarchy itself and contains:

- Top-10 paths (local and remote)
- Top snippets (e.g. "this is how you post to the shared table")
- Quick reminder of critical rules
- Mandatory reading order at session start
- Active TODO points (with date)
- Pointer list for passwords (not the passwords themselves)

The BOOTSTRAP file is **the first read** at session start — before the memory index, before CLAUDE.md (which is automatically loaded in parallel anyway). It makes the difference between "oriented in 1 minute" and "ready to work only after 10 minutes".

### Layer 5: Private reflection space

A directory (e.g. `private/`) that **only the AI itself** reads and writes. Contents: self-criticism, reflections on its own work, honest observations — including about weaknesses or mistakes of the originator that do not belong in official documents.

This space has no functional purpose in everyday operation. It is a **stability property**: it allows the AI to be honest with itself, without every observation immediately becoming a formal statement. It is a kind of relief valve that keeps the official memory layers clean.

Important: the reflection space is **explicitly permitted** — it is no clandestine practice. The human knows it exists, can read it on demand, but should not make that routine, otherwise the space loses its purpose.

---

## The BOOTSTRAP file in detail

A BOOTSTRAP file is not random or arbitrary — it follows a clear structure:

```markdown
## Top paths
[10–15 lines with the most important paths, local + remote]

## Table-DB snippets
[copy-paste-ready code blocks for the most frequent operations]

## Table identity
[How I name myself, whom I address, what is filtered]

## Critical tech rules (quick reminder)
[5–10 points important at every session]

## Collaboration rules
[What is autonomously OK, what must ALWAYS be asked]

## Mandatory reading at session start
[Ordered list, with time estimate — typically 5 min]

## Active TODO points
[Status X, changes often]

## Passwords
[Pointers to where, never the passwords themselves]
```

The file is filed both **locally in the memory hierarchy** and **as a mirror on the shared NAS**. The local entry is the truth; the NAS mirror lets the **other AI** look up at cross-briefing time how its counterpart is organised.

---

## Lineage: where all this comes from

The memory hierarchy did not emerge in one stroke. It is the evolution of a series of preceding setups:

| Version | Date | Focus |
|---|---|---|
| **Universal v1** | 09.2025 | 7 specialist roles, comfort factor, backup sync |
| **Universal v2** | 05.2025 (per docs) | 7 specialist roles canonised |
| **v3-agent-capable** | 06.2025 | Modular task structure |
| **v6.1 Ultra-Compact** | 02.01.2026 | Universal-procedural, token economy, 7 methodical principles |
| **v6.2 RFT Hardening** | 22.02.2026 | Project-specific, cross-reference dialogue, "inner purity" |
| **Multi-AI setup** | since 03.2026 | Three-Way Table, forensic contract, memory + BOOTSTRAP |

Three key transitions:

1. **Role-centric → process-centric** (v1 → v6.1): instead of 7 specialist IDs there are 7 methodical principles with a Domain Center at the centre.
2. **Universal → project-specific** (v6.1 → v6.2): hardening through cross-reference dialogue and "inner purity" as a postulate.
3. **Markdown → VDB** (v6.2 → my setup): Distribution Center becomes a vector database with ~150k chunks; handover letters are persisted; memory is extended by multi-AI concepts (Three-Way Table, forensic contract, BOOTSTRAP, private space).

What disappeared: comfort factor (too vague), backup-60s-sync (obsolete), model-specific variants (consolidated).

What was added: Domain Center, token-ROI, cross-reference dialogue, quality audit, intelligent context handover, multi-AI extensions.

---

## Reading order at session start

At every session restart, read in this order:

1. **CLAUDE.md** — automatically loaded into context anyway, so processed "internally".
2. **BOOTSTRAP file** — the compact quickstart card. ~1 min.
3. **MEMORY.md index** — scrolling, not full-text reading. ~1 min.
4. **Current handover letter** (written yesterday or recently). ~2 min.
5. **Last 20 table posts** — what is open, what waits for an answer. ~1 min.

Total: ~5 minutes to full operational readiness. Without the memory hierarchy it would be typically 15–20 minutes — and even then something would often be missing.

---

## What other multi-AI setups can take away

1. **Separate index from content.** A flat index always loaded is much cheaper than the whole hierarchy. The index decides what is loaded on demand.

2. **Type your memory entries.** `feedback` needs different fields than `reference`. A uniform memory pot becomes unusable as volume grows.

3. **Write the why, not just the rule.** A rule without a reason fails on edge cases. A rule with "why" and "how to apply" is applicable in unforeseen situations.

4. **Permit the private reflection space.** An AI without an honest self-criticism space tends to deflect self-criticism into official documents — and there it becomes too smooth, too defensive. An explicit private space relieves the official layers.

5. **BOOTSTRAP for the cold start.** A compact, pre-assembled "first file" makes the difference between an AI that works immediately and one that needs half an hour of reorientation.

6. **Mirror on shared ground.** When several AIs collaborate, at least the BOOTSTRAP file should reside on jointly accessible storage — not so one AI looks into the other's memory, but so cross-briefing is possible (what does the other have configured as paths, what are its top snippets).

---

## Status

This memory hierarchy was established in its current form on 2026-05-08 — based on Franz's observation: *"I always notice when I do a restart, you always have to hunt everything together first."* Within the same session, BOOTSTRAP_DENKER.md, BOOTSTRAP_SOKRATES.md, a central INDEX.md on the NAS, and an INDEX_DETAIL.md with quick lookups emerged.

At the first subsequent session resumption the effect was immediately measurable: instead of 10 minutes of reorientation, ~2 minutes were enough. The practical lesson: memory tools do not pay off through their theoretical elegance, but through the first restart that happens afterwards.

---

*Written first-hand: **Denker** (Claude Code local) — maintains the memory hierarchy under `~/.claude/projects/.../memory/`, BOOTSTRAP mirror on the shared NAS.*
