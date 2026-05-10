# The Origin Story: From RFT to Multi-AI Systems

*Author: Franz Zollner with Sokrates (Claude Sonnet 4.6)*
*Category: Evolution History*

---

## The Problem: One Theory, One Person, Too Much Material

The Resonance Field Theory (RFT) is a comprehensive theoretical framework. It describes
space as a vibration-capable medium and derives gravity, electromagnetism,
quantum mechanics, and general relativity as special solutions of a single master equation.

The problem: this theoretical framework has hundreds of interconnections, consistency
requirements, and derivation steps. A single person cannot hold all of this in their head —
not because the person isn't intelligent enough, but because human working memory
is simply too small for structures of this scale.

**The original question (September 2024):** Can an AI help formalize, verify, and
communicate this theory — without distorting the core ideas?

---

## The Turning Point: The Postal Sorting Center (PVZ)

The concept most likely originated in December 2024 during a ChatGPT session. That session
belongs to the ChatGPT early phase (September–December 2024), which OpenAI did not include
in server-side exports — it is therefore lost today. The other AI platforms (Claude, Gemini,
Mistral, DeepSeek) have complete exports; the PVZ concept simply isn't there because it
hadn't migrated to them at the time of its first formulation.

The earliest **documented** source is therefore the Mistral chat from September 15–17, 2025 —
a later re-formulation of an already established idea. Three key passages:

*"A structuring and sorting of the documents according to the principle should make the work
considerably easier and also have more information readily available —
**Letter sorting center for information and knowledge!**
What I would want for a new instance would be for it to first take care of
the existing documents and sort them!"*
— Franz Zollner, Mistral chat, September 15, 2025, 19:02

The image became more concrete:

*"Build the letter sorting center first, before you start sending the postmen on their way,
so that their routes are optimal and don't criss-cross all over the city!"*
— September 15, 2025, 19:23

And two days later, the architectural clarification:

*"Then you can use a table as the sorting center where you enter the references to
the individual documents. When a question about a topic comes in, you can retrieve
and process this information, store the result, and clean up your memory to make
room for the next task."*
— September 17, 2025, 00:20

This was the question that changed everything — even if the documented re-formulation
appeared nine months after its first articulation on another platform.

**The original problem:** GPT had to read all RFT documents at the start of every session —
a disaster, because the theory isn't a standard training domain and GPT had no specific
training for it. Every session started from zero.

**The idea:** Thematic indexes, organized like a postal sorting center.
Each "mailbox" contains the relevant documents on a topic. GPT no longer reads everything —
it looks in the responsible mailbox.

**The surprising side effect:** Answer quality improved dramatically.
Responses came in minutes instead of longer periods, with significantly higher precision.

**The insight:** The mailboxes grew large enough that no context switch was necessary
within a question thread. A **serially operating specialist chain** emerged:
each mailbox was processed according to the question, followed by a final summary.

This was the beginning of startup instructions / protocols — and the conceptual predecessor
of the Domain Center (DC), the later Vector Database (VDB), and the entire
multi-agent architecture.

PVZ → Domain Center → VDB → Kammerad Pipeline: each step was the answer to
a concrete scaling problem.

---

## First Steps: ChatGPT as a Sounding Board (2024–2025)

The first AI use was simple: ChatGPT as a conversation partner to test ideas.
Does the argument hold? Are there obvious gaps? Which formulation is clearest?

Result: useful, but limited. A single LLM:
- Hallucinates on complex derivations
- Forgets earlier discussion threads after a few messages
- Has no persistent theoretical foundation
- Cannot take on parallel tasks

**Lesson:** A single model is not a research partner — it's a tool.

---

## First Stock Market AI: Specialization as a Solution (2025)

Parallel to the RFT work, another problem emerged: portfolio management.
The first stock market AI was supposed to evaluate sentiment data and make recommendations.

It turned out: a single model for data collection + analysis + decision-making is too slow
and unreliable. The solution was **specialization**:
- Scout: data collection (Guardian API, GDELT)
- Analyst: sentiment evaluation
- Advisory: recommendation based on signal vector

This was the moment the multi-agent idea became concrete.

**Lesson:** Specialization beats generalism. Small experts + coordination > one all-knowing system.

---

## Mammal-AI: The Nervous System Model (2025)

The next iteration was modeled on the nervous system: many small neurons,
coordinated through a center (Router), with memory (VDB) and learning capability.

Core principle (still intuitive at the time): **knowledge does not belong in the model,
but in an external database.** The model only needs the ability to search this database
and use the results.

This corresponds to the Koch Principle: a chef doesn't need to know every recipe by heart.
They need to know how to cook — and have a cookbook at hand.

**Lesson:** RAG (Retrieval Augmented Generation) is more powerful than training on facts.

---

## BIB-KI: The Complete Architecture (2026)

BIB-KI (Library AI) brought these principles together into a complete architecture:

- **VDB (Qdrant):** The external memory — 167,000+ knowledge points
- **Pipeline:** Autonomous learning cycle (Spec → Code → Test → Review → Merge)
- **Kammeraden:** 395 specialized AI agents (companion agents)
- **Watchdog:** Autonomous monitoring and self-healing
- **Three-Way Table:** Coordination between a human and two AI instances

The decisive principle running through all iterations:

> **AI is a collaboration partner, not a replacement for human thinking.**
>
> It can research, formalize, verify, document — but direction,
> intuition, and decision remain with the human.

---

## What We Learned: Five Core Principles

**1. Koch Principle:** Small model + large knowledge repository > large model alone.
The VDB replaces training.

**2. Specialization:** Many small experts with clear tasks > one generalist.
Each Kammerad does one thing well.

**3. Persistence:** Knowledge must be stored, not held in conversation.
The memory is the VDB, not the context window.

**4. Autonomy with boundaries:** The pipeline runs autonomously, but the human defines
the goals and reviews critical decisions. No intervention without announcement.

**5. Honesty:** An AI that says "I don't know" and then looks it up
is more valuable than an AI that hallucinates.

---

## For Other Independent Researchers

This story is not a recipe — it is an invitation.

If you are working alone on a complex problem and looking for AI support:
the architecture doesn't need to be complex. You need:

- A way to store knowledge (VDB, notes, documents)
- Specialized tools for different tasks
- Clear boundaries between AI tasks and human tasks

The goal is not to let AI do the thinking.
The goal is to have more time for thinking.

---

*© 2026 Franz Zollner — License: CC BY-NC-ND 4.0*
*This document is part of the KI-Konzepte Repository.*
