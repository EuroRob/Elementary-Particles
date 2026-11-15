---
type: Note
state: organized
ep_tier: research
ep_particle: research-01
belongs_to: "[[RESEARCH_BRAIN_ARCHITECTURE]]"
related_to:
  - "[[ELEMENTARY_PARTICLES_ERM]]"
  - "[[UNIFIED_RELATIONSHIP_MODEL]]"
decision_date: "2026-05-28"
status: active
---

# ADR: RAG + Graph Memory over Fine-Tuning

**Date:** 2026-05-28  
**Status:** Active  
**Tier:** research | **Particle:** research-01  

## Context

When building the Research Brain, a question arose: should the LLM be
fine-tuned on the research corpus, or should a RAG + external memory
architecture be used instead?

## Decision

**RAG + graph memory** — do not fine-tune initially.

## Reasoning

| Concern | Fine-Tuning | RAG + Graph |
|---------|------------|-------------|
| Newly released papers | ✗ Stale after training cutoff | ✓ Updated nightly |
| Source citations | ✗ Model hallucinates sources | ✓ Grounded in retrieved docs |
| Cost to update | ✗ Full retraining run | ✓ Ingest pipeline re-runs |
| Auditability | ✗ Opaque | ✓ Citations visible in response |
| Infrastructure | ✗ GPU + training pipeline needed | ✓ Runs on existing gpu-01 |

The base Qwen model already contains extensive world knowledge from pretraining.
What it *cannot* know is: papers published after its cutoff, our personal
research corpus, niche domain annotations, or evolving citation relationships.
RAG with Neo4j citation traversal closes all of those gaps.

## When Fine-Tuning Would Make Sense (Future Phase 3)

- Domain-specific *tone* or formatting (e.g. structured literature review style)
- Custom ontology extraction tasks
- Specialized entity/claim extraction with a private schema

Fine-tuning is deferred until Phase 3 of the Research Brain roadmap.

## Consequences

- Qdrant + Neo4j + Elasticsearch are required infrastructure (not optional)
- MemPalace session memory bridges the gap between RAG (static retrieval) and
  conversational continuity
- Research quality is bounded by ingestion coverage and embedding model quality;
  `nomic-embed-text` was chosen for its 768-dim quality/speed balance
