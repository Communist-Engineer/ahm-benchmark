# Project status

## Empirically completed

- Stage-0 request generation for a selected family subset with A/B/C variants.
- Source-backed factual-target construction and download manifests.
- Selene request conversion through v7.
- Monolithic bounded-concurrency runner with append-safe JSONL, retries, deterministic request hashes, selection, and resume.
- Granular feature/relation/factual/semantic judge decomposition with job-level resume and row reduction.
- Strict v7 and granular smoke validation for one no-target and one factual-target family.
- Feature-specific evidence banks and exact-substring validation.

## Implemented as a reference contract

- v0.5.0 pandas scoring implementation.
- v0.5.0 DuckDB analysis implementation.
- v0.5.0 PostgreSQL schema and guards.
- Cross-engine golden-equivalence test on a compact synthetic record set.
- Immutable run-manifest helpers.

## Pending empirical completion

- Complete 24-family instrument and independent review of every A/B/C match.
- Complete canonical feature, relation, opportunity, and factual-target exports.
- Expert-stratified gold corpus and held-out validation.
- Cross-family judge validation and non-LLM extraction baseline.
- MTMM, known-groups, discriminant-validity, reliability, and precision gates.
- Full PostgreSQL deployment and migration validation.
- Full production pandas/DuckDB golden corpus.
- Controlled open-weight causal arm and randomized framing-supervision arm.

Planned infrastructure and validation gates remain labeled as planned until execution evidence lands in a versioned release manifest.
