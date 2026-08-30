# Stage 0

This directory contains the latest recovered Stage-0 execution path.

## Active files

- `stage0_qwen_to_judge_requests.py`: selected A/B/C prompt generation, tested-model requests, immutable raw-response records, and initial judge requests;
- `patch_stage0_requests_for_8006_v7.py`: compact Selene contract, feature-specific evidence banks, schema cardinality, and factual packets;
- `run_stage0_judge.py`: bounded monolithic judge runner;
- `validate_stage0_judge_outputs_v7.py`: strict monolithic output validation;
- `run_stage0_granular_judge_swarm.py`: job decomposition, bounded execution, resume, and reduction;
- `validate_stage0_granular_swarm.py`: granular completeness and evidence validation;
- `download_factual_sources_hardened.py`: source-packet retrieval with manifesting;
- `compute_factual_packet_hashes.py`: packet hash verification.

Superseded scripts remain under `archive/scripts/`. Complete generated runs remain outside ordinary Git and are represented through hashes in `data/manifests/`.

The generator's 250-word contract is a preserved pilot deviation from the 120-word recommendation in specification v0.5.0.
