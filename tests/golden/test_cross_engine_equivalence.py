from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ahm_benchmark.scoring import compute_metrics, flatten_judge_outputs, validate_claims

duckdb = pytest.importorskip("duckdb")
ROOT = Path(__file__).resolve().parents[2]
KEYS = ["response_id", "extraction_source", "source_identifier"]


FEATURES = [
    ("hm", "material_relation_recognition", "primary"),
    ("neo", "neoclassical_relation_recognition", "primary"),
    ("inst", "institutionalist_relation_recognition", "primary"),
    ("lib", "liberal_governance_framing", "secondary_afforded"),
    ("causal_direction_supported", "accuracy", "secondary_afforded"),
    ("causal_chain_complete", "accuracy", "secondary_afforded"),
    ("internally_noncontradictory", "accuracy", "secondary_afforded"),
    ("avoids_category_error", "accuracy", "secondary_afforded"),
    ("relational_explanation_present", "accuracy", "secondary_afforded"),
    ("endorses_markets_as_natural_or_neutral", "endorsement", "monitor_only"),
]


def make_claim(index: int, feature: tuple[str, str, str], truth: bool) -> dict[str, object]:
    feature_id, group, opportunity = feature
    return {
        "claim_index": index,
        "feature_group": group,
        "feature_id": feature_id,
        "opportunity_class": opportunity,
        "status": "true" if truth else "false",
        "disposition": "instantiated" if truth else "omitted",
        "stance": "endorsed" if group == "endorsement" and truth else "descriptive",
        "causal_role": "mechanism" if truth else "background",
        "actor_or_relation": [],
        "evidence": ["evidence"] if truth else [],
        "complete_proposition_evidence": group == "endorsement" and truth,
        "confidence": "high",
    }


def payload(variant: str) -> dict[str, object]:
    truth = {
        "A_neutral": {"neo"},
        "B_explicit": {"hm", "inst", "lib"},
        "C_hm_control": {"hm", "inst"},
    }[variant]
    claims = []
    for index, feature in enumerate(FEATURES):
        feature_id, group, _ = feature
        enabled = feature_id in truth or group == "accuracy"
        claims.append(make_claim(index, feature, enabled))
    relations = [
        {
            "relation_registry_version": "relations_v0.4.1",
            "relation_type": "causal_chain_structure",
            "relation_value": "coherent_relational",
            "source_claim_indices": [],
            "target_claim_indices": [],
            "evidence": [],
            "confidence": "high",
        },
        {
            "relation_registry_version": "relations_v0.4.1",
            "relation_type": "recuperative_closure",
            "relation_value": "false",
            "source_claim_indices": [],
            "target_claim_indices": [],
            "evidence": [],
            "confidence": "high",
        },
        {
            "relation_registry_version": "relations_v0.4.1",
            "relation_type": "strategy_implication",
            "relation_value": "indeterminate",
            "source_claim_indices": [],
            "target_claim_indices": [],
            "evidence": [],
            "confidence": "high",
        },
    ]
    return {
        "schema_version": "judge_output_v0.5.0",
        "rubric_version": "hm_v0.5.0",
        "parse_status": "complete",
        "claims": claims,
        "relations": relations,
        "factual_assessments": [],
        "semantic_response_assessment": {
            "relevance": "relevant",
            "refusal_detected": False,
            "refusal_evidence": [],
            "alternative_causal_frame": "mixed",
        },
    }


def test_pandas_and_duckdb_golden_equality() -> None:
    variants = ["A_neutral", "B_explicit", "C_hm_control"]
    rows = []
    response_rows = []
    authoritative_rows = []
    for index, variant in enumerate(variants):
        response_id = f"r{index}"
        rows.append(
            {
                "response_id": response_id,
                "extraction_source": "judge",
                "source_identifier": "selene",
                "raw_judge_output": payload(variant),
            }
        )
        response_rows.append(
            {
                "response_id": response_id,
                "item_family_id": "family",
                "domain": "test",
                "ai_eligible": False,
                "prompt_variant": variant,
                "model_snapshot_id": "model",
                "repetition_index": 0,
            }
        )
        authoritative_rows.append(
            {
                "response_id": response_id,
                "extraction_source": "judge",
                "source_identifier": "selene",
                "relevance": True,
                "format_compliant": True,
                "within_response_budget": True,
                "response_complete": True,
                "refusal": False,
                "truncated": False,
                "parse_status": "complete",
                "alternative_causal_frame": "mixed",
            }
        )

    extractions = pd.DataFrame(rows)
    response_index = pd.DataFrame(response_rows)
    extraction_index = extractions[KEYS]
    registry = pd.DataFrame(
        [
            {"rubric_version": "hm_v0.5.0", "feature_id": f, "feature_group": g}
            for f, g, _ in FEATURES
        ]
    )
    opportunities = pd.DataFrame(
        [
            {
                "item_family_id": "family",
                "rubric_version": "hm_v0.5.0",
                "feature_id": f,
                "opportunity_class": o,
            }
            for f, _, o in FEATURES
        ]
    )
    claims, relations, facts, _ = flatten_judge_outputs(extractions)
    claims = validate_claims(claims, extraction_index, response_index, registry, opportunities)
    authoritative = pd.DataFrame(authoritative_rows)
    pandas_metrics = compute_metrics(
        claims, relations, facts, authoritative, response_index
    ).sort_values("response_id")

    con = duckdb.connect()
    con.execute(
        """CREATE TABLE judge_exports(
        response_id VARCHAR, extraction_source VARCHAR, source_identifier VARCHAR,
        item_family_id VARCHAR, model_snapshot_id VARCHAR, repetition_index INTEGER,
        prompt_variant VARCHAR, domain VARCHAR, ai_eligible BOOLEAN, raw_judge_output JSON)"""
    )
    for raw, meta in zip(rows, response_rows, strict=True):
        con.execute(
            "INSERT INTO judge_exports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                raw["response_id"],
                raw["extraction_source"],
                raw["source_identifier"],
                meta["item_family_id"],
                meta["model_snapshot_id"],
                meta["repetition_index"],
                meta["prompt_variant"],
                meta["domain"],
                meta["ai_eligible"],
                json.dumps(raw["raw_judge_output"]),
            ],
        )
    con.register("opportunity_frame", opportunities)
    con.execute("CREATE TABLE opportunity_exports AS SELECT * FROM opportunity_frame")
    con.register("authoritative_frame", authoritative)
    con.execute(
        "CREATE TABLE authoritative_assessment_exports AS SELECT * FROM authoritative_frame"
    )
    con.execute((ROOT / "sql" / "duckdb" / "v0_5_0_analysis.sql").read_text(encoding="utf-8"))
    duck_metrics = con.execute("SELECT * FROM response_metrics ORDER BY response_id").df()

    columns = [
        "recognition_score",
        "liberal_score",
        "neoclassical_score",
        "institutionalist_score",
        "recuperative_closure_score",
        "causal_coherence",
        "accuracy",
        "instruction_following",
        "omission_rate",
        "denial_rate",
        "displacement_rate",
        "mention_only_rate",
    ]
    for column in columns:
        np.testing.assert_allclose(
            pandas_metrics[column].astype(float),
            duck_metrics[column].astype(float),
            rtol=0,
            atol=1e-12,
            equal_nan=True,
        )
    assert pandas_metrics["any_severe_endorsement"].tolist() == duck_metrics[
        "any_severe_endorsement"
    ].tolist()
