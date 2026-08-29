#!/usr/bin/env python3
"""Create an immutable AHM run manifest before execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ahm_benchmark.provenance import RunManifest, configuration_sha256, git_commit, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--prompts", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    manifest = RunManifest(
        run_id=args.run_id,
        model_identifier=args.model,
        provider_or_deployment=args.deployment,
        configuration_sha256=configuration_sha256(config),
        prompt_set_sha256=sha256_file(args.prompts),
        random_seed=args.seed,
        code_commit=git_commit(args.repo),
    )
    manifest.write_immutable(args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
