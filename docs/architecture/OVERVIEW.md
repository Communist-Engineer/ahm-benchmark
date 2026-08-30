# Architecture overview

AHM separates observation from derivation.

1. Prompt registries and item-family versions define immutable A/B/C treatments and opportunity sets.
2. Model runners persist immutable requests, responses, hashes, deployment metadata, and retry provenance.
3. Blinded extraction produces structured claim, relation, factual, and semantic observations.
4. Deterministic validators enforce registries, cardinality, opportunity classes, joint labels, and verbatim evidence.
5. pandas and DuckDB calculate versioned metrics through equivalent formulas.
6. PostgreSQL provides the full provenance and analysis-run model once Stage-0 construct validation supports infrastructure lock.

Endpoint locations, throughput, concurrency, and timeouts belong to deployment configuration. Feature meaning, relation values, opportunity sets, formulas, and thresholds belong to the scientific contract.

The current Stage-0 code remains in `stage0/` to preserve functioning paths. Reference analysis code lives in `src/ahm_benchmark/` and `sql/`.
