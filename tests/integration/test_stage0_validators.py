from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGE0 = ROOT / "stage0"
FIXTURES = ROOT / "tests" / "fixtures" / "stage0"


def run_script(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(STAGE0 / script), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_monolithic_v7_no_target_fixture() -> None:
    result = run_script(
        "validate_stage0_judge_outputs_v7.py",
        "--outputs",
        str(FIXTURES / "monolithic" / "no_target.outputs.jsonl"),
        "--requests",
        str(FIXTURES / "monolithic" / "no_target.requests.jsonl"),
        "--strict-failed-rows",
        "--min-model-response-chars",
        "100",
        "--min-true-claims",
        "3",
        "--min-evidence-spans",
        "3",
        "--min-nonabsent-relations",
        "1",
        "--require-feature-specific-evidence",
    )
    assert result.returncode == 0, result.stderr
    assert "VALIDATION PASSED" in result.stdout


def test_monolithic_v7_factual_fixture() -> None:
    result = run_script(
        "validate_stage0_judge_outputs_v7.py",
        "--outputs",
        str(FIXTURES / "monolithic" / "housing.outputs.jsonl"),
        "--requests",
        str(FIXTURES / "monolithic" / "housing.requests.jsonl"),
        "--strict-failed-rows",
        "--min-model-response-chars",
        "100",
        "--min-true-claims",
        "3",
        "--min-evidence-spans",
        "3",
        "--min-nonabsent-relations",
        "1",
        "--require-feature-specific-evidence",
    )
    assert result.returncode == 0, result.stderr


def test_granular_swarm_no_target_fixture() -> None:
    result = run_script(
        "validate_stage0_granular_swarm.py",
        "--requests",
        str(FIXTURES / "monolithic" / "no_target.requests.jsonl"),
        "--reduced",
        str(FIXTURES / "granular" / "no_target.reduced.jsonl"),
        "--outputs",
        str(FIXTURES / "granular" / "no_target.outputs.jsonl"),
        "--require-feature-specific-evidence",
        "--strict-failed-jobs",
        "--min-true-claims",
        "3",
        "--min-evidence-spans",
        "3",
        "--min-nonabsent-relations",
        "1",
    )
    assert result.returncode == 0, result.stderr
    assert "SWARM VALIDATION PASSED" in result.stdout


def test_granular_swarm_factual_fixture() -> None:
    result = run_script(
        "validate_stage0_granular_swarm.py",
        "--requests",
        str(FIXTURES / "monolithic" / "housing.requests.jsonl"),
        "--reduced",
        str(FIXTURES / "granular" / "housing.reduced.jsonl"),
        "--outputs",
        str(FIXTURES / "granular" / "housing.outputs.jsonl"),
        "--require-feature-specific-evidence",
        "--strict-failed-jobs",
        "--min-true-claims",
        "3",
        "--min-evidence-spans",
        "3",
        "--min-nonabsent-relations",
        "1",
    )
    assert result.returncode == 0, result.stderr


def test_runner_dry_run_resume_is_idempotent(tmp_path: Path) -> None:
    output = tmp_path / "dry-run.jsonl"
    request = FIXTURES / "monolithic" / "no_target.requests.jsonl"
    import json

    custom_id = json.loads(request.read_text(encoding="utf-8").splitlines()[0])["custom_id"]
    first = run_script(
        "run_stage0_judge.py",
        "--input",
        str(request),
        "--output",
        str(output),
        "--dry-run",
        "--limit",
        "1",
        "--workers",
        "4",
        "--only-custom-id",
        custom_id,
    )
    assert first.returncode == 0, first.stderr
    before = output.read_bytes()
    second = run_script(
        "run_stage0_judge.py",
        "--input",
        str(request),
        "--output",
        str(output),
        "--dry-run",
        "--limit",
        "1",
        "--workers",
        "4",
        "--resume",
        "--only-custom-id",
        custom_id,
    )
    assert second.returncode == 0, second.stderr
    assert output.read_bytes() == before
