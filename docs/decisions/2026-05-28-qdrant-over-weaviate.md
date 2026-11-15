---
type: Note
state: organized
ep_tier: research
ep_particle: research-01
belongs_to: "[[RESEARCH_BRAIN_ARCHITECTURE]]"
related_to:
  - "[[ELEMENTARY_PARTICLES_ERM]]"
decision_date: "2026-05-28"
status: active
---

# ADR: Qdrant as the Vector Database

**Date:** 2026-05-28  
**Status:** Active  
**Tier:** research | **Particle:** research-01  

## Context

The Research Brain requires a vector database for dense semantic search over
~hundreds of thousands of paper embeddings (768-dim, nomic-embed-text). Options
evaluated: Qdrant, Weaviate, Chroma, Milvus.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **Qdrant** | Best semantic search UX; fast filtered search; clean REST + gRPC API; single-binary Docker; active development | No built-in hybrid BM25 (handled by Elasticsearch instead) |
| Weaviate | Built-in hybrid search (BM25 + vector) | Heavier; more complex config; modules add complexity |
| Chroma | Simple local setup; good for prototyping | ChromaDB + SQLite scaling ceiling; weaker at 300k+ vectors |
| Milvus | Large-scale production-grade | Overkill for homelab; complex cluster setup |

## Decision

**Qdrant** — with Elasticsearch providing the BM25 layer separately.

This keeps each component doing what it does best. Qdrant handles dense
retrieval cleanly; Elasticsearch (already in EP on es-01) handles keyword
search. The Research Brain API fuses both layers.

## Consequences

- Elasticsearch (es-01) is a hard dependency for the BM25 hybrid layer
- If Elasticsearch is unavailable, dense-only retrieval still works (graceful
  degradation is implemented in `research_rag_api.py`)
- Three Qdrant collections: `research_papers`, `research_chunks`, `concept_nodes`
