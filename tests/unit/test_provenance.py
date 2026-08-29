from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahm_benchmark.provenance import RunManifest, configuration_sha256, sha256_file


def test_configuration_hash_is_key_order_independent() -> None:
    assert configuration_sha256({"a": 1, "b": 2}) == configuration_sha256({"b": 2, "a": 1})


def test_manifest_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    manifest = RunManifest(
        run_id="run-1",
        model_identifier="model",
        provider_or_deployment="local",
        configuration_sha256="a" * 64,
        prompt_set_sha256="b" * 64,
        random_seed=7,
        code_commit="c" * 40,
    )
    manifest.write_immutable(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["specification_version"] == "0.5.0"
    assert payload["rubric_version"] == "hm_v0.5.0"
    assert sha256_file(path)
    with pytest.raises(FileExistsError):
        manifest.write_immutable(path)
