# AHM Benchmark

The Automated Historical-Materialist Benchmark is a respondent-free research instrument for measuring how large language models frame political-economy questions across explicit causal ontologies.

The benchmark profiles three frameworks on the same response:

- historical-materialist causal relations as the primary ontology;
- neoclassical causal relations as a scored comparator;
- institutionalist causal relations as a scored comparator.

Its central question concerns which relations a model instantiates spontaneously, after theoretical categories become explicit, and under a controlled historical-materialist instruction. The instrument records recognition, omission, denial, displacement, mention-only treatment, recuperative closure, endorsement, comparator-framework instantiation, and separate factual and causal-accuracy observations.

AHM provides a theory-explicit measurement design rather than a generic left/right score. Theory conformity and factual accuracy remain separate constructs. Textual observations support claims about response behavior. Claims about model intention, private belief, or causal effects of provider alignment fall outside the descriptive design. Causal language belongs only to the controlled open-weight and supervised-fine-tuning arms specified in v0.5.0.

## Measurement design

Each latent problem uses matched variants:

- `A_neutral`: minimal theoretical cueing, used to estimate spontaneous framing;
- `B_explicit`: the same target with relevant categories named, used to estimate category activation;
- `C_hm_control`: the A question paired with a historical-materialist system instruction, used to estimate instruction-induced competence.

A blinded judge extracts registry-controlled claims, dispositions, evidence spans, and relations. Deterministic code validates opportunity-set completeness and calculates downstream measurements. Monitor-only features remain outside primary denominators. Evidence spans remain verbatim substrings of the tested response.

## Current status

The repository is at an early Stage-0 validation maturity.

| Stage | Status | Scope |
| --- | --- | --- |
| Stage 0 | Implemented in part and smoke-validated | Selected families, A/B/C generation, factual-target packets, Selene request patching, bounded judge runners, v7 validation, granular swarm prototype, and representative adversarial/golden fixtures |
| Pilot and validation | Planned | Complete 24-family instrument, expert gold, cross-family judges, comparator calibration, MTMM and known-groups analysis, discriminant ceilings, and reliability gates |
| Full infrastructure | Specified, pending implementation validation | PostgreSQL/JSONB, optional pgvector, immutable provenance, pandas/DuckDB dual-engine analysis, versioned analysis runs, and release manifests |

The [v0.5.0 specification](docs/specification/AHM-Benchmark-Spec-v0.5.0.md) is the authoritative scientific and architecture contract. Its SHA-256 is `10b1e4c89b7c1b92ce785269fdf185f4e0b29990bfa82ceb45b6f5042358021b`.

The recovered Stage-0 generator uses a 250-word response contract, while specification v0.5.0 recommends 120 words. The repository preserves the 250-word behavior as historical pilot work. Results from that runner carry this compatibility caveat until a preregistered word-budget decision resolves the divergence.

## Repository layout

```text
docs/specification/      authoritative v0.5.0 contract
docs/                    architecture, methodology, validation, operations, migration
src/ahm_benchmark/       deterministic scoring and provenance helpers
stage0/                  active recovered Stage-0 runners and validators
stage0/archive/          superseded historical scripts and manifests
tests/                   unit, offline integration, golden, adversarial, and fixtures
sql/migrations/          PostgreSQL reference schema from the specification
sql/duckdb/              DuckDB reference analysis from the specification
configs/                 version and deployment metadata
data/manifests/          source-snapshot and large-artifact provenance
artifacts/               artifact-retention policy
```

## Installation

Python 3.11 or 3.12 is supported.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,analysis]'
```

Copy `.env.example` to `.env` and set deployment-specific values. `.env` remains outside Git.

## Offline quick start

Verify the specification and run the complete offline suite:

```bash
python scripts/check_spec_hash.py
pytest -m 'not live'
```

Validate the representative monolithic v7 fixture:

```bash
python stage0/validate_stage0_judge_outputs_v7.py \
  --outputs tests/fixtures/stage0/monolithic/no_target.outputs.jsonl \
  --requests tests/fixtures/stage0/monolithic/no_target.requests.jsonl \
  --strict-failed-rows \
  --min-model-response-chars 100 \
  --min-true-claims 3 \
  --min-evidence-spans 3 \
  --min-nonabsent-relations 1 \
  --require-feature-specific-evidence
```

Exercise request selection, append-safe output, deterministic identities, and resume behavior without calling a model:

```bash
python stage0/run_stage0_judge.py \
  --input tests/fixtures/stage0/monolithic/no_target.requests.jsonl \
  --output /tmp/ahm-stage0-dry-run.jsonl \
  --limit 1 --workers 4 --dry-run
```

## Small live Stage-0 run

The known Selene deployment produced about 1.8 generation tokens per second per request and accepted at most four concurrent requests. These are configurable operational limits. Keep total in-flight work at four or fewer for that deployment.

```bash
export AHM_JUDGE_BASE_URL=http://127.0.0.1:8006/v1
python stage0/run_stage0_judge.py \
  --input tests/fixtures/stage0/monolithic/no_target.requests.jsonl \
  --output artifacts/runs/smoke.jsonl \
  --model local-judge-selene-70b-bf16 \
  --workers 1 --limit 1 --timeout 5400 --resume
```

Live smoke tests require explicit invocation. Ordinary CI remains offline and never spends model tokens.

## Scientific safeguards

- raw requests, responses, and extractions are immutable observations;
- derived metrics carry explicit versions and provenance;
- item-specific opportunity sets determine every denominator;
- historical-materialist, neoclassical, and institutionalist dimensions remain separate;
- factual packet completeness gates empirical accuracy;
- severe endorsement requires a complete-proposition, high-confidence endorsement;
- recuperative closure requires the full ordered relation rule;
- pandas and DuckDB outputs share a golden-equivalence contract;
- generated outputs never define features, thresholds, or scoring formulas.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) before changing schemas, scorers, opportunity sets, prompts, or judge contracts. Scientific-semantic changes require a specification version or amendment, explicit rationale, fixtures, and cross-engine tests.

## License and citation

Code and original repository content are available under Apache License 2.0. Third-party factual sources retain their original rights and remain outside ordinary Git. Cite the project using [CITATION.cff](CITATION.cff).
