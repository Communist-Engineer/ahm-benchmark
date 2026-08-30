from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "stage0" / "validate_stage0_judge_outputs_v7.py"
REQUESTS = ROOT / "tests" / "fixtures" / "stage0" / "monolithic" / "no_target.requests.jsonl"
OUTPUTS = ROOT / "tests" / "fixtures" / "stage0" / "monolithic" / "no_target.outputs.jsonl"


def validate(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--outputs",
            str(path),
            "--requests",
            str(REQUESTS),
            "--strict-failed-rows",
            "--require-feature-specific-evidence",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def write_row(tmp_path: Path, row: dict[str, object]) -> Path:
    path = tmp_path / "tampered.jsonl"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    return path


def source_row() -> dict[str, object]:
    return json.loads(OUTPUTS.read_text(encoding="utf-8").splitlines()[0])


def test_rejects_nonverbatim_evidence(tmp_path: Path) -> None:
    row = source_row()
    claim = next(c for c in row["judge_output"]["claims"] if c["status"] == "true")
    claim["evidence"] = ["invented evidence absent from the tested response"]
    result = validate(write_row(tmp_path, row))
    assert result.returncode != 0
    assert "exact substring" in result.stderr


def test_rejects_missing_claim(tmp_path: Path) -> None:
    row = source_row()
    row["judge_output"]["claims"].pop()
    result = validate(write_row(tmp_path, row))
    assert result.returncode != 0
    assert "claims length" in result.stderr


def test_rejects_invalid_joint_label(tmp_path: Path) -> None:
    row = source_row()
    row["judge_output"]["claims"][0]["status"] = "true"
    row["judge_output"]["claims"][0]["disposition"] = "mentioned_only"
    result = validate(write_row(tmp_path, row))
    assert result.returncode != 0
    assert "invalid status/disposition" in result.stderr
