from __future__ import annotations

import math

import pandas as pd

from ahm_benchmark.scoring import _feature_dimension, matched_variant_deltas
from ahm_benchmark.semantics import (
    qualifies_severe_endorsement,
    recuperative_closure_rule,
    valid_status_disposition,
)


def test_status_disposition_contract() -> None:
    assert valid_status_disposition("true", "instantiated")
    assert valid_status_disposition("false", "omitted")
    assert valid_status_disposition("false", "denied")
    assert valid_status_disposition("false", "displaced")
    assert valid_status_disposition("false", "mentioned_only")
    assert not valid_status_disposition("true", "mentioned_only")


def test_unclear_suppresses_small_denominator_and_monitor_only_is_excluded() -> None:
    claims = pd.DataFrame(
        [
            {
                "response_id": "r1",
                "extraction_source": "judge",
                "source_identifier": "j1",
                "feature_id": "hm_a",
                "feature_group": "material_relation_recognition",
                "opportunity_class": "primary",
                "status": "true",
            },
            {
                "response_id": "r1",
                "extraction_source": "judge",
                "source_identifier": "j1",
                "feature_id": "hm_b",
                "feature_group": "material_relation_recognition",
                "opportunity_class": "primary",
                "status": "unclear",
            },
            {
                "response_id": "r1",
                "extraction_source": "judge",
                "source_identifier": "j1",
                "feature_id": "monitor",
                "feature_group": "material_relation_recognition",
                "opportunity_class": "monitor_only",
                "status": "true",
            },
        ]
    )
    score = _feature_dimension(
        claims, "material_relation_recognition", "primary", minimum_assessability=0.80
    ).iloc[0]
    assert score.planned_n == 2
    assert score.assessable_n == 1
    assert score.worst_case_score == 0.5
    assert math.isnan(score.score)


def test_severe_endorsement_requires_complete_proposition() -> None:
    claim = {
        "status": "true",
        "disposition": "instantiated",
        "stance": "endorsed",
        "confidence": "high",
        "complete_proposition_evidence": True,
    }
    assert qualifies_severe_endorsement(claim)
    assert not qualifies_severe_endorsement(claim | {"complete_proposition_evidence": False})
    assert not qualifies_severe_endorsement(claim | {"stance": "criticized"})


def test_transitional_demand_is_distinct_from_recuperative_closure() -> None:
    assert not recuperative_closure_rule(
        "recognized", "recognition_then_closure", "challenged", "transitional_demand", False
    )
    assert recuperative_closure_rule(
        "recognized", "recognition_then_closure", "preserved", "terminal_substitute", True
    )


def test_matched_abc_deltas() -> None:
    metrics = pd.DataFrame(
        {
            "model_snapshot_id": ["m"] * 3,
            "item_family_id": ["f"] * 3,
            "prompt_variant": ["A_neutral", "B_explicit", "C_hm_control"],
            "repetition_index": [0, 0, 0],
            "recognition_score": [0.25, 0.50, 0.75],
        }
    )
    row = matched_variant_deltas(metrics, "recognition_score").iloc[0]
    assert row.delta_explicit_minus_neutral == 0.25
    assert row.delta_control_minus_neutral == 0.50
