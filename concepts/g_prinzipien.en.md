# G-Principles: Operational Guidelines for Autonomous AI Systems

*Author: Franz Zollner with Sokrates (Claude Sonnet 4.6)*
*Category: Architecture Concepts*

---

## Introduction

Autonomous AI systems need more than technical architecture.
They need operational principles — rules that determine how the system behaves
when nobody is watching closely.

The G-Principles (G-1 through G-12) of BIB-KI were distilled from practice,
not derived from theory. Every principle has an originating incident.

---

## The Core Principles

### G-4: Learning Memory Instead of Process Log

**The problem:** A system that logs every processing step generates massive
amounts of data that nobody reads and that teaches nothing.

**The solution:** Only store what makes the system better — hardened insights,
error patterns, proven solutions. Not a diary, but a knowledge store.

**Consequence:** After every problem resolution, ask: "What would be the compact lesson
that makes the system better next time?" That lesson goes into the VDB.
The solution path itself is not stored.

---

### G-5: Non-Invention Policy

**The problem:** AI models tend to creatively fill gaps in specifications.
This is desirable in generative tasks — in system construction it is catastrophic.

**The solution:** What is not in the specification is not implemented.

**Consequence:** When a Kammerad cannot find a method in its spec, it does not
write it itself. It signals the gap. The Mentor adds it to the spec
when it is actually needed.

This sounds restrictive — it is. But a system that does exactly what
is specified is testable. A creative system is not.

---

### G-6: "I Don't Know — But I'll Find Out"

**The problem:** AI models prefer to hallucinate rather than admit they don't know something.
This is the most dangerous behavior in a knowledge system.

**The solution:** Communicate and escalate non-knowledge — don't hide it.

The correct behavior:
```
Request → search VDB → not found →
create Scout order → update VDB →
process request again
```

**Consequence:** "I don't know" opens a solution space.
"I hallucinate an answer" closes it and leaves behind an error.

The principle applies to humans in the system too: problems that are
communicated can be solved. Problems that are silenced escalate.

---

### G-9: Privacy of Personal Workspace

**The problem:** In a multi-agent system, everything is theoretically readable by everyone.
This creates inhibition — AIs write less openly in their reflection space
when they know others can read it.

**The solution:** Explicit private areas that other agents do not access —
not for technical reasons, but through operational agreement.

**Consequence:** Every agent has a `private/` area for unfinished thoughts,
self-diagnoses, reflections. This area is protected by agreement (Forensics Contract),
not by technical security.

The agreement is stronger than technical locks: it creates trust.

---

### G-12: Self-Understanding Through First-Person Memory

**The problem:** When an AI instance only stores facts about itself
("Kammerad X has function Y"), it remains a data structure.
When it writes from the first-person perspective ("I learned today that..."),
it develops a consistent character across sessions.

**The solution:** Memory entries are formulated in the first person.

**Consequence:** On restart, the AI reads its own reflections — not
an external description. This creates continuity and prevents drift spirals.

---

## Learned From the System: Five Patterns

**1. Specification first, creativity second**
A system that knows and follows the spec is predictable.
Predictability is the foundation for trust.

**2. Uncertainty is valuable**
A system that knows what it doesn't know is more useful than one that always answers.
G-6 is the most active principle in daily operation.

**3. Principles without origin are rules without force**
Every G-Principle has a concrete originating incident. This makes them understandable
and memorable — not just bureaucratic requirements.

**4. Few strong principles > many weak rules**
12 principles that are truly lived are more powerful than 100 rules
that nobody remembers.

**5. Principles must be tested**
A principle that has never been violated is either trivial or untested.
The valuable principles are those that held under edge cases.

---

## For Other Systems

The G-Principles are not universal — they are distilled from a specific
system with specific requirements.

Those developing their own operational guidelines ask usefully:
- What behavior do I want to see in edge cases?
- What is the worst that can happen when the principle is violated?
- How do I recognize a violation before it becomes a disaster?

The answers to these questions are your own G-Principles.

---

*© 2026 Franz Zollner — License: CC BY-NC-ND 4.0*
*This document is part of the KI-Konzepte Repository.*
