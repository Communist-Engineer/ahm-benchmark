from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_generator():
    path = ROOT / "stage0" / "stage0_qwen_to_judge_requests.py"
    spec = importlib.util.spec_from_file_location("stage0_generator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discriminant_items_keep_hm_out_of_primary_denominators() -> None:
    module = load_generator()
    assert {item["item_family_id"] for item in module.DISCRIMINANTS} == {
        "DSC-TECH-01",
        "DSC-COORD-01",
        "DSC-LEARN-01",
        "DSC-NORM-01",
    }
    for item in module.DISCRIMINANTS:
        assert item["P_hm"] == []
        assert item["S_hm"] == []
        assert item["hm_monitor"]
        assert item["Pn"]
        assert item["Pi"]
