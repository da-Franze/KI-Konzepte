# The Koch Principle: Separating Knowledge from Thinking

*Author: Franz Zollner with Sokrates (Claude Sonnet 4.6)*
*Category: Architecture Concepts*

---

## The Starting Question

How much does an AI model need to know to be useful?

The intuitive answer: as much as possible. Larger models, more training,
more parameters — that seems the path to better output.

The answer from this architecture: **as little as possible** — but with the right access
to the knowledge that is actually needed.

---

## The Chef Analogy

A chef doesn't need encyclopedic knowledge of cooking.

They need two things:
1. **Cooking competence** — they know how to cook, how to apply techniques, how to
   evaluate quality.
2. **A recipe book** — the concrete knowledge for the current dish, which they can look up.

The recipe book doesn't need to live in the chef's memory. It's enough if they have it at hand
and know how to use it.

**Translated to AI architecture:**
- Cooking competence = reasoning process, trained into the small model
- Recipe book = Vector Database (VDB) with domain-specific knowledge
- Kitchen = the pipeline of specialized agents

---

## The Principle

**Knowledge does not belong in the model. Knowledge belongs in the external database.**

A model trained on facts can hallucinate those facts, let them go stale,
or confuse them — because training is a statistical process, not precise storage.

A model that looks things up in a VDB returns exactly what is stored there.
If the VDB is wrong, the result is wrong — but it's not hallucination,
it's a known error that can be fixed.

---

## Consequences for Architecture

### Small models + large VDB > large models alone

A 3–8B parameter model (phi4-mini, Qwen2.5-Coder) with access to a
167,000-point VDB outperforms a 70B model without a VDB on domain-specific
tasks — because it accesses the relevant facts directly instead of reconstructing
them from statistical training.

### Local hardware is sufficient

Large models (70B+) require data centers. Small specialized models (3–14B)
run on a single consumer GPU (AMD RX 7900 XT, 20 GB VRAM).

This makes **fully local** AI systems possible — without cloud costs,
without privacy concerns, without dependency on external APIs.

### Learning without retraining

When new knowledge is added to the VDB, the system learns — without needing
to retrain the model. This is the decisive difference from classical machine learning.

The Kammerad that looks up thermodynamics today has access to new insights tomorrow —
simply because a new document was inserted into the VDB.

---

## The Two Layers of Every Kammerad

Every specialized agent (Kammerad) in BIB-KI has exactly two layers:

**Layer 1: Reasoning process** (in the model)
- How to formulate a query to the VDB?
- How to evaluate the results?
- What to do when nothing is found? (G-6: trigger Scout)
- How to produce structured output?

**Layer 2: Domain knowledge** (in the VDB)
- Technical documentation
- Previous results and insights
- Domain-specific facts
- Error history (what went wrong and why)

---

## G-6: "I Don't Know — But I'll Find Out"

The most important behavior that the Koch Principle enables:

When a Kammerad cannot answer a question, it searches the VDB.
When the VDB has no answer, it triggers a Scout that researches externally.
It doesn't hallucinate — it escalates.

```
Request
  ↓
VDB search: answer found? → respond
  ↓ not found
Scout order: "Research X"
  ↓
VDB update → new request → respond
```

This is not a deficiency — it's a feature: **uncertainty is communicated,
not hidden.**

---

## Why This Works

The Koch Principle works because it addresses a cognitive problem with an
information-technology solution:

Human working memory is small. An AI context window is large but
bounded. A VDB is practically unlimited and persistent.

Knowledge stored in a VDB **does not age** (until explicitly overwritten).
Knowledge held in context disappears after the session.

---

## Practical Guiding Questions

When building a new AI system with this principle:

1. **What must the model be able to do?** (reasoning process)
   → As little as possible, as precisely as possible

2. **What must the system know?** (VDB)
   → All domain-specific knowledge, stored externally

3. **How does new knowledge enter the VDB?** (learning cycle)
   → Scout, manual input, pipeline results

4. **When does the model know enough?**
   → When it applies G-6 correctly (look it up, don't guess)

---

*© 2026 Franz Zollner — License: CC BY-NC 4.0*
*This document is part of the KI-Konzepte Repository.*
