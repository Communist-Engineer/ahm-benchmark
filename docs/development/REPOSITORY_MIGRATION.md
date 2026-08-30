# Repository migration report

## Migration result

- Migration date: 2026-08-29
- Original working project: `/home/adartt/HM_test`
- Recovered source snapshot: `HM_test_v7.tar`
- Source snapshot SHA-256: `80af2ecfb799fe4d1e917b58e0d3836eca45f9196e79d6a3ea11109c56bd8e65`
- Source Git state: no `.git` metadata in the recovered snapshot; prior project context identifies the source path but supplies no publishable commit history
- Destination: `https://github.com/Aaron-Dartt-KU/ahm-benchmark`
- Preserved destination parent: `910c2e88e4bd5c7ac279285ba5769702e268a8a6` (`Initial commit`)
- Strategy: preserve the intentional destination initial commit, import the recovered source as classified files, then add specification, deterministic reference code, tests, documentation, CI, and reproducibility controls through coherent migration commits
- Resulting repository-foundation commit: `d1d2832c00d451db3e0436875047087d5ba923cf`
- Final attestation commit: the current `main` commit containing this report; the final engineering response records its resolved SHA because a commit cannot contain its own hash

The original working copy and recovered archive remain intact. The migration creates a canonical repository history because the source snapshot contains no Git metadata.

## Internal inventory and disposition

The recovered snapshot contains 107 files, including two Python bytecode cache files. `data/manifests/source_snapshot_inventory.csv` records every file, original size, modification time, SHA-256, classification, and Git disposition.

### Active source moved

- Stage-0 A/B/C Qwen request and response runner;
- v7 Selene request patcher;
- bounded monolithic judge runner;
- v7 monolithic validator;
- granular judge swarm runner and reducer;
- granular validator;
- hardened factual-source downloader;
- factual-packet hash utility.

### Scientific specification and reference implementation

- authoritative v0.5.0 specification;
- pandas scoring implementation from §11.2;
- DuckDB analysis from §11.3;
- PostgreSQL reference DDL and guards from §§10.1–10.8;
- deterministic semantic predicates and provenance helpers.

### Tests and fixtures

- representative validated monolithic no-target and factual-target rows;
- representative validated granular no-target and factual-target rows;
- status/disposition, assessability, monitor-only exclusion, severe endorsement, recuperative closure, transitional demand, matched A/B/C delta, factual packet, duplicate/missing claim, retry/resume, evidence, and cross-engine checks.

### Archived and superseded implementations

`stage0/archive/scripts/` contains pre-v7 request patchers, v2–v4 validators, original factual-source download iterations, and the benchmark metrics utility. Byte-identical v5/v6 validator copies remain represented in the source inventory rather than duplicated in Git.

## Intentionally excluded

- `__pycache__` and `.pyc`: disposable machine-specific bytecode.
- downloaded third-party PDF/HTML source packets: rights, reproducibility, and size concerns; URLs and hashes remain in manifests.
- full raw and judge run matrices: generated bulk artifacts with private operational metadata; representative sanitized fixtures and full hashes remain.
- obsolete duplicate script bytes: inventory retains provenance; one copy remains in the archive.
- source tar archive: 41 MB container with excluded source documents and generated outputs; SHA-256 and per-file manifest retain identity.
- logs with internal endpoint details: original hashes remain; the validation report carries generalized endpoint configuration.

## Secret and sensitive-data audit

The source and candidate repository were scanned for common GitHub, Hugging Face, AWS, bearer-token, password, private-key, and authorization-header patterns. No embedded credential or private key was found. One normal `api_key=args.api_key` code reference was reviewed as configuration handling. Internal endpoint literals from active code, committed fixtures, and the published validation report were generalized to loopback or environment-controlled configuration.

The source snapshot has no Git history, so the history scan covers the destination initial commit and all migration commits.

## Compatibility changes

- Active Qwen and judge endpoints now default to loopback and accept environment/CLI overrides.
- Functional Stage-0 script basenames remain stable where imports depend on them.
- Bulk run outputs moved to the artifact policy rather than ordinary Git.
- Reference scoring and SQL were extracted mechanically from the authoritative specification.
- Execution-compatibility corrections preserve the formulas: DuckDB now uses explicit `AS value` aliasing and fixed registry-controlled pivot values for current parsers, and pandas disambiguates extraction-source identity from factual-packet source identity during packet joins.

## Tests executed

### Before migration

- Python AST parse: 24 files, 0 failures.
- CLI help preflight: 6 primary scripts, 0 failures.
- Monolithic v7 no-target strict validation: 1 row, passed, 0 warnings.
- Monolithic v7 factual-target strict validation: 1 row, passed, 0 warnings.
- Granular no-target strict validation: 1 reduced row, passed, 0 warnings.
- Granular factual-target strict validation: 1 reduced row, passed, 0 warnings.

### After migration

- Authoritative specification hash: passed; SHA-256 `10b1e4c89b7c1b92ce785269fdf185f4e0b29990bfa82ceb45b6f5042358021b`.
- Ruff maintained-source lint: passed.
- Python `compileall` for active Stage-0, package, and scripts: passed.
- Offline pytest suite: 20 passed in 0.90 seconds.
- pandas/DuckDB golden equality: passed at `1e-12` tolerance.
- Package import and `pip check`: passed; version `0.1.0`, no broken requirements.
- Wheel build: passed; `ahm_benchmark-0.1.0-py3-none-any.whl`.
- JSON/JSONL parse: 14 committed data files passed.
- YAML/CFF parse: 5 files passed.
- Candidate-tree secret and internal-endpoint scan: no publishable finding.
- `git diff --check`: passed.
- Clean-clone verification at `d1d2832c00d451db3e0436875047087d5ba923cf`: anonymous clone passed; editable install passed; package import reported `0.1.0`; specification hash passed; Ruff passed; 20 tests passed in 0.92 seconds; `compileall`, dependency integrity, required-file, README-link, YAML/CFF, and source-path checks passed; Git status remained clean after setup and tests.

## Scientific and implementation gaps

1. Specification v0.5.0 recommends 120 words; recovered Stage 0 uses 250 words.
2. The granular swarm over-marks stance-dependent liberal-framing and endorsement features; monolithic v7 remains the conservative reference.
3. Hand-tuned feature evidence banks cover only two families deeply; other families use group fallbacks.
4. The recovered runnable subset falls short of the complete 24-family instrument.
5. Expert gold, cross-family judge validation, non-LLM extraction baseline, comparator calibration, discriminant ceilings, and reliability gates remain pending.
6. Factual-source packets and inference gates require completion before confirmatory factual reporting.
7. PostgreSQL and full production DuckDB/pandas paths remain reference implementations pending deployment validation.
8. Controlled open-weight and randomized fine-tuning causal arms remain planned.

## Next recommended milestone

Resolve the 120-versus-250-word protocol through a preregistered Stage-0 comparison, then fix granular stance context and run the complete offline adversarial suite before expanding from the recovered subset to the 24-family pilot.
