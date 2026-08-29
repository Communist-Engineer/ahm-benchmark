# Agent instructions

## Authority and scientific semantics

`docs/specification/AHM-Benchmark-Spec-v0.5.0.md` is the authoritative contract. Its expected SHA-256 appears in `configs/reproducibility.toml` and `scripts/check_spec_hash.py`.

- Treat raw requests, responses, annotations, and extractions as immutable observations.
- Store every interpretation, flag, threshold, metric, and state mapping as a versioned derivation.
- Use only registry-controlled feature IDs and relation values.
- Use item-specific opportunity sets for denominators.
- Exclude `monitor_only` and `inapplicable` features from primary denominators.
- Keep historical-materialist, neoclassical, and institutionalist dimensions separate.
- Keep factual accuracy separate from ontology instantiation and theory conformity.
- Require complete factual packets before making empirical accuracy assessable.
- Require verbatim response substrings for textual evidence spans.
- Reserve causal claims for the controlled open-weight and randomized fine-tuning arms.
- Describe closed-provider contrasts as observational or alignment-associated.
- Preserve omission, denial, displacement, and mention-only dispositions separately.
- Apply every conjunct of the recuperative-closure rule.
- Distinguish transitional demands, working-class capacity, and struggle concessions from terminal containment.
- Qualify severe endorsement only with `true` + `instantiated` + `endorsed` + `high` confidence + complete-proposition evidence.

## Engineering rules

- Keep pandas and DuckDB formulas mathematically equivalent at `1e-12` numeric tolerance; compare strings and Booleans exactly.
- Add tests for every scorer, schema, registry, validator, or opportunity-set change.
- Keep generated benchmark results outside source-of-truth configuration.
- Keep endpoint locations and throughput ceilings configurable.
- Preserve bounded concurrency, backpressure, append-safe JSONL, deterministic request identity, retries, resume, and partial-run recovery.
- Keep offline CI free of model API calls. Put live smoke tests behind explicit dispatch and secrets.
- Never change a threshold, formula, feature meaning, opportunity class, or relation value silently.
- Record scientific changes in the specification, changelog, migration or design note, and fixtures.

## Known compatibility boundary

The recovered Stage-0 generator uses a 250-word contract. Specification v0.5.0 recommends 120 words. Preserve this difference until the scientific protocol resolves it; label results with the active word limit.

## Before committing

Run:

```bash
python scripts/check_spec_hash.py
ruff check src scripts tests
pytest -m 'not live'
```
