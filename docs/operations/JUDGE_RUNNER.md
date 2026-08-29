# Judge runner operations

## Observed Selene deployment envelope

The recovered Stage-0 work used AtlaAI Selene 70B behind an OpenAI-compatible vLLM endpoint.

- observed generation throughput: about 1.8 tokens/second/request;
- maximum useful concurrent requests: 4;
- bounded worker default for the granular runner: 4;
- long per-attempt timeout: 5,400 seconds for granular jobs;
- deterministic decoding: temperature 0, top-p 1.

These values describe one deployment. Set endpoints through `AHM_JUDGE_BASE_URL`; tune operational limits through CLI arguments and versioned run configuration.

## Reliability properties

`run_stage0_judge.py` provides:

- deterministic `custom_id` selection;
- optional model override;
- bounded worker pool;
- append plus flush plus `fsync` output;
- request-body hashes;
- per-attempt timing and status;
- retries with recorded provenance;
- resume by completed `custom_id`;
- dry-run selection and hashing;
- explicit protection against accidental duplicate append.

The granular runner uses the same principles at `job_id` granularity, then reduces jobs into the v0.5.0 row contract. Total in-flight requests across simultaneous runners must respect the deployment ceiling.

## Operational sequence

1. Create an immutable run manifest.
2. Patch canonical judge requests through v7.
3. Run the smallest representative dry run.
4. Run a one-row live smoke.
5. Validate with strict evidence and cardinality flags.
6. Execute the bounded full run with `--resume`.
7. Validate, hash, and archive raw artifacts.

The optional GitHub live-smoke workflow targets a labeled self-hosted runner and requires explicit dispatch.
