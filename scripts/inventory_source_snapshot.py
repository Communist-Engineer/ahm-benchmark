#!/usr/bin/env python3
"""Build the deterministic inventory for the recovered HM_test snapshot."""

from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

ACTIVE = {
    "stage0_qwen_to_judge_requests.py": "stage0/stage0_qwen_to_judge_requests.py",
    "patch_stage0_requests_for_8006_v7.py": "stage0/patch_stage0_requests_for_8006_v7.py",
    "run_stage0_judge.py": "stage0/run_stage0_judge.py",
    "run_stage0_granular_judge_swarm.py": "stage0/run_stage0_granular_judge_swarm.py",
    "validate_stage0_judge_outputs_v7.py": "stage0/validate_stage0_judge_outputs_v7.py",
    "validate_stage0_granular_swarm.py": "stage0/validate_stage0_granular_swarm.py",
    "compute_factual_packet_hashes.py": "stage0/compute_factual_packet_hashes.py",
}
ARCHIVED = {
    "patch_stage0_judge_requests.py",
    "patch_stage0_requests_for_8006.py",
    "patch_stage0_requests_for_8006_v2.py",
    "patch_stage0_requests_for_8006_v3.py",
    "patch_stage0_requests_for_8006_v4.py",
    "patch_stage0_requests_for_8006_v5.py",
    "patch_stage0_requests_for_8006_v6.py",
    "validate_stage0_judge_outputs_v2.py",
    "validate_stage0_judge_outputs_v3.py",
    "validate_stage0_judge_outputs_v4.py",
    "download_factual_sources.py",
    "download_factual_sources_hardened.old.py",
    "_bench_metrics.py",
}
DATA = {
    "factual_targets.stage0.source_backed.json",
    "factual_targets.stage0.source_backed.downloaded.json",
    "factual_targets.stage0.source_backed.partial.json",
    "factual_targets.stage0.download_manifest.json",
}
FIXTURES = {
    "judge_requests.selene_litellm.8006.no_target.v7.jsonl",
    "judge_outputs.selene_litellm.8006.no_target.v7.one.jsonl",
    "judge_requests.selene_litellm.8006.hous.v7.jsonl",
    "judge_outputs.selene_litellm.8006.hous.v7.one.jsonl",
    "judge_swarm_outputs.notarget.all.smoke.jsonl",
    "judge_swarm_reduced.notarget.all.smoke.jsonl",
    "judge_swarm_outputs.hous.all.smoke.jsonl",
    "judge_swarm_reduced.hous.all.smoke.jsonl",
}
DUPLICATES = {
    "validate_stage0_judge_outputs_v5.py",
    "validate_stage0_judge_outputs_v6.py",
    "download_factual_sources_hardened.py",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(relative: str, name: str) -> tuple[str, str, str]:
    if "__pycache__" in relative or name.endswith(".pyc"):
        return "generated disposable artifact", "excluded", "machine bytecode cache"
    if "factual_sources_stage0/" in relative:
        return "generated but useful artifact", "manifest_only", "third-party source packet"
    if name in ACTIVE:
        return "canonical source", "active_copy", ACTIVE[name]
    if name == "download_factual_sources_hardened_v2.py":
        return "canonical source", "active_copy", "stage0/download_factual_sources_hardened.py"
    if name in ARCHIVED:
        return "obsolete/superseded but historically useful", "archived_copy", f"stage0/archive/scripts/{name}"
    if name in DUPLICATES:
        return "obsolete/superseded but historically useful", "manifest_only", "byte-identical copy retained once"
    if name in DATA:
        return "research provenance", "data_copy", f"stage0/data/{name}"
    if name in {"factual_targets.stage0.download_manifest.dry_run.json", "factual_targets.stage0.download_manifest.fred_test.json"}:
        return "research provenance", "archived_copy", f"stage0/archive/{name}"
    if name == "stage0_judge_iteration_report.md":
        return "research provenance", "generalized_copy", "docs/validation/STAGE0_JUDGE_ITERATION_V7.md"
    if name in FIXTURES:
        return "generated but useful artifact", "sanitized_fixture", "tests/fixtures/stage0"
    if name.endswith(".log"):
        return "machine-specific", "manifest_only", "contains internal operational metadata"
    if name.endswith((".jsonl", ".json")):
        return "generated but useful artifact", "manifest_only", "bulk or superseded run artifact"
    if name.endswith(".py"):
        return "obsolete/superseded but historically useful", "manifest_only", "superseded source"
    return "unknown", "manifest_only", "retained in source archive pending review"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    rows = []
    for path in sorted(p for p in args.source_root.rglob("*") if p.is_file()):
        relative = path.relative_to(args.source_root).as_posix()
        category, disposition, destination_or_reason = classify(relative, path.name)
        rows.append(
            {
                "source_path": relative,
                "size_bytes": path.stat().st_size,
                "modified_utc": datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc
                ).isoformat(),
                "sha256": sha256(path),
                "classification": category,
                "git_disposition": disposition,
                "destination_or_reason": destination_or_reason,
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"INVENTORY_WRITTEN files={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
