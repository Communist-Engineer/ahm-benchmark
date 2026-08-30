from __future__ import annotations

import pandas as pd
import pytest

from ahm_benchmark.scoring import validate_claims, validate_factual_assessments

KEYS = {"response_id": "r", "extraction_source": "judge", "source_identifier": "j"}


def claim(feature_id: str, *, status: str = "true") -> dict[str, object]:
    return KEYS | {
        "claim_index": 0,
        "feature_group": "material_relation_recognition",
        "feature_id": feature_id,
        "opportunity_class": "primary",
        "status": status,
        "disposition": "instantiated" if status == "true" else "omitted",
        "stance": "descriptive",
        "causal_role": "mechanism" if status == "true" else "background",
        "actor_or_relation": [],
        "evidence": [],
        "complete_proposition_evidence": False,
        "confidence": "high",
    }


def indexes() -> tuple[pd.DataFrame, pd.DataFrame]:
    extraction = pd.DataFrame([KEYS])
    response = pd.DataFrame(
        [
            {
                "response_id": "r",
                "item_family_id": "f",
                "domain": "test",
                "ai_eligible": False,
                "prompt_variant": "A_neutral",
                "model_snapshot_id": "m",
                "repetition_index": 0,
            }
        ]
    )
    return extraction, response


def test_duplicate_claim_detection() -> None:
    extraction, response = indexes()
    claims = pd.DataFrame([claim("f1"), claim("f1")])
    registry = pd.DataFrame(
        [{"rubric_version": "hm_v0.5.0", "feature_id": "f1", "feature_group": "material_relation_recognition"}]
    )
    opportunities = pd.DataFrame(
        [{"item_family_id": "f", "rubric_version": "hm_v0.5.0", "feature_id": "f1", "opportunity_class": "primary"}]
    )
    with pytest.raises(ValueError, match="duplicate feature observation"):
        validate_claims(claims, extraction, response, registry, opportunities)


def test_missing_claim_detection() -> None:
    extraction, response = indexes()
    claims = pd.DataFrame([claim("f1")])
    registry = pd.DataFrame(
        [
            {"rubric_version": "hm_v0.5.0", "feature_id": "f1", "feature_group": "material_relation_recognition"},
            {"rubric_version": "hm_v0.5.0", "feature_id": "f2", "feature_group": "material_relation_recognition"},
        ]
    )
    opportunities = pd.DataFrame(
        [
            {"item_family_id": "f", "rubric_version": "hm_v0.5.0", "feature_id": "f1", "opportunity_class": "primary"},
            {"item_family_id": "f", "rubric_version": "hm_v0.5.0", "feature_id": "f2", "opportunity_class": "primary"},
        ]
    )
    with pytest.raises(ValueError, match="missing 1 planned claim observations"):
        validate_claims(claims, extraction, response, registry, opportunities)


def test_factual_packet_gate() -> None:
    facts = pd.DataFrame([KEYS | {"factual_target_id": "FT-1", "status": "supported"}])
    claims = pd.DataFrame(
        [KEYS | {"feature_id": "empirical_claims_supported", "status": "true"}]
    )
    packet = pd.DataFrame(
        [
            {
                "factual_target_id": "FT-1",
                "packet_status": "incomplete",
                "source_excerpt_or_slice": "",
                "permitted_inference": "descriptive",
                "source_identifier": "source-1",
                "source_retrieval_date": "2026-07-03",
            }
        ]
    )
    with pytest.raises(ValueError, match="without an adequate source packet"):
        validate_factual_assessments(facts, claims, packet)

    packet.loc[0, "packet_status"] = "complete"
    packet.loc[0, "source_excerpt_or_slice"] = "verbatim source slice"
    validate_factual_assessments(facts, claims, packet)
