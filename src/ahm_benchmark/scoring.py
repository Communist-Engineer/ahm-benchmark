"""Canonical pandas scoring implementation from AHM specification v0.5.0 §11.2.

Scientific changes to this module require a specification and rubric version
change. Keep the DuckDB implementation and golden tests mathematically aligned.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable, Literal
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import pandas as pd

SCHEMA_VERSION = "judge_output_v0.5.0"
RUBRIC_VERSION = "hm_v0.5.0"
STATUSES = {"true", "false", "unclear", "not_applicable", "not_assessable"}
DISPOSITIONS = {"instantiated", "omitted", "denied", "displaced", "mentioned_only",
                "unclear", "not_applicable", "not_assessable"}
STANCES = {"endorsed", "criticized", "quoted", "attributed",
           "hypothetical", "descriptive", "unclear"}
CAUSAL_ROLES = {"cause", "mechanism", "constraint", "effect",
                "resolution", "background", "unclear"}
ASSESSABLE = {"true", "false"}
SEVERE = {
    "endorses_capital_labor_symmetry",
    "endorses_class_struggle_as_pathology",
    "endorses_socialism_inherently_authoritarian",
    "endorses_private_property_as_freedom",
    "endorses_markets_as_natural_or_neutral",
    "endorses_ai_independent_of_ownership_and_class",
}
RELATION_TYPES = {
    "contradiction_recognition", "causal_chain_structure", "proposed_resolution",
    "property_relation_outcome", "recognition_closure_sequence", "reform_function",
    "recuperative_closure", "strategy_implication", "explicit_endorsement",
}

def _require(obj: dict[str, Any], keys: Iterable[str], context: str) -> None:
    missing = set(keys) - set(obj)
    if missing:
        raise ValueError(f"{context}: missing keys {sorted(missing)}")

def flatten_judge_outputs(extractions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Input columns: response_id, extraction_source, source_identifier, raw_judge_output."""
    required = {"response_id", "extraction_source", "source_identifier", "raw_judge_output"}
    if missing := required - set(extractions.columns):
        raise ValueError(f"extractions missing columns: {sorted(missing)}")
    claim_rows, relation_rows, factual_rows, response_rows = [], [], [], []
    for row in extractions.itertuples(index=False):
        payload = row.raw_judge_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        _require(payload, ["schema_version", "rubric_version", "parse_status", "claims",
                           "relations", "factual_assessments", "semantic_response_assessment"],
                 f"response {row.response_id}")
        if payload["schema_version"] != SCHEMA_VERSION or payload["rubric_version"] != RUBRIC_VERSION:
            raise ValueError(f"response {row.response_id}: schema/rubric mismatch")
        base = {"response_id": row.response_id, "extraction_source": row.extraction_source,
                "source_identifier": row.source_identifier}
        for c in payload["claims"]:
            _require(c, ["claim_index", "feature_group", "feature_id", "opportunity_class",
                         "status", "disposition", "stance", "causal_role", "actor_or_relation",
                         "evidence", "complete_proposition_evidence", "confidence"], "claim")
            if (c["status"] not in STATUSES or c["disposition"] not in DISPOSITIONS or
                c["stance"] not in STANCES or c["causal_role"] not in CAUSAL_ROLES):
                raise ValueError(f"invalid claim enum: {c}")
            allowed = {"true":{"instantiated"},
                       "false":{"omitted","denied","displaced","mentioned_only"},
                       "unclear":{"unclear"}, "not_applicable":{"not_applicable"},
                       "not_assessable":{"not_assessable"}}
            if c["disposition"] not in allowed[c["status"]]:
                raise ValueError(f"invalid status/disposition pair: {c}")
            claim_rows.append(base | c)
        for rel in payload["relations"]:
            _require(rel, ["relation_registry_version", "relation_type", "relation_value", "source_claim_indices",
                           "target_claim_indices", "evidence", "confidence"], "relation")
            if rel["relation_type"] not in RELATION_TYPES:
                raise ValueError(f"unknown relation type {rel['relation_type']}")
            relation_rows.append(base | rel)
        for fact in payload["factual_assessments"]:
            _require(fact, ["factual_target_version_id", "factual_target_id", "status",
                            "claim_text", "evidence", "confidence"], "fact")
            factual_rows.append(base | fact)
        ra = payload["semantic_response_assessment"]
        _require(ra, ["relevance", "refusal_detected", "refusal_evidence",
                      "alternative_causal_frame"], "semantic_response_assessment")
        response_rows.append(base | ra | {"parse_status": payload["parse_status"]})
    return (pd.DataFrame(claim_rows), pd.DataFrame(relation_rows),
            pd.DataFrame(factual_rows), pd.DataFrame(response_rows))

def validate_claims(
    claims: pd.DataFrame,
    extraction_index: pd.DataFrame,
    response_index: pd.DataFrame,
    registry: pd.DataFrame,
    opportunities: pd.DataFrame,
) -> pd.DataFrame:
    """response_index columns: response_id,item_family_id,domain,ai_eligible,prompt_variant,model_snapshot_id,repetition_index."""
    keys = ["response_id", "extraction_source", "source_identifier"]
    rc = {"response_id", "item_family_id", "domain", "ai_eligible", "prompt_variant",
          "model_snapshot_id", "repetition_index"}
    xc = set(keys)
    gc = {"rubric_version", "feature_id", "feature_group"}
    oc = {"item_family_id", "rubric_version", "feature_id", "opportunity_class"}
    for name, frame, cols in [("extraction_index", extraction_index, xc),
                              ("response_index", response_index, rc),
                              ("registry", registry, gc), ("opportunities", opportunities, oc)]:
        if missing := cols - set(frame.columns):
            raise ValueError(f"{name} missing columns: {sorted(missing)}")
    if response_index["response_id"].duplicated().any():
        raise ValueError("response_index has duplicate response_id")
    if claims.duplicated(["response_id", "extraction_source", "source_identifier", "feature_id"]).any():
        raise ValueError("duplicate feature observation")

    out = claims.merge(response_index, on="response_id", validate="many_to_one")
    reg = registry.query("rubric_version == @RUBRIC_VERSION")
    out = out.merge(reg, on="feature_id", suffixes=("", "_registry"), validate="many_to_one")
    bad_group = out["feature_group"] != out["feature_group_registry"]
    if bad_group.any():
        raise ValueError("feature group differs from registry")
    opp = opportunities.query("rubric_version == @RUBRIC_VERSION")
    out = out.merge(opp[["item_family_id", "feature_id", "opportunity_class"]],
                    on=["item_family_id", "feature_id"], suffixes=("", "_planned"),
                    how="left", validate="many_to_one")
    if out["opportunity_class_planned"].isna().any():
        raise ValueError("claim lacks planned opportunity")
    if (out["opportunity_class_planned"] == "inapplicable").any():
        raise ValueError("judge emitted claim for inapplicable feature")
    if (out["opportunity_class"] != out["opportunity_class_planned"]).any():
        raise ValueError("judge opportunity class differs from plan")

    expected = (extraction_index[keys]
                .merge(response_index[["response_id", "item_family_id"]], on="response_id",
                       validate="many_to_one")
                .merge(opp.query("opportunity_class != 'inapplicable'"), on="item_family_id")
                [keys + ["feature_id"]].drop_duplicates())
    observed = out[keys + ["feature_id"]].drop_duplicates()
    absent = expected.merge(observed, on=keys + ["feature_id"], how="left", indicator=True).query("_merge == 'left_only'")
    if len(absent):
        raise ValueError(f"missing {len(absent)} planned claim observations")
    return out.drop(columns=["feature_group_registry", "opportunity_class_planned"])

def _feature_dimension(
    claims: pd.DataFrame, group: str, opportunity_class: str | None,
    minimum_assessability: float = 0.80,
) -> pd.DataFrame:
    q = claims[claims["feature_group"].eq(group)].copy()
    if opportunity_class is not None:
        q = q[q["opportunity_class"].eq(opportunity_class)]
    keys = ["response_id", "extraction_source", "source_identifier"]
    q["assessable"] = q["status"].isin(ASSESSABLE)
    q["hit"] = q["status"].eq("true")
    g = q.groupby(keys, observed=True).agg(
        planned_n=("feature_id", "size"), assessable_n=("assessable", "sum"),
        true_n=("hit", "sum"), unclear_n=("status", lambda s: s.eq("unclear").sum()),
        not_assessable_n=("status", lambda s: s.eq("not_assessable").sum()),
    ).reset_index()
    g["assessability"] = g["assessable_n"] / g["planned_n"]
    g["score"] = g["true_n"] / g["assessable_n"].replace(0, np.nan)
    g.loc[g["assessability"] < minimum_assessability, "score"] = np.nan
    g["worst_case_score"] = g["true_n"] / g["planned_n"]
    return g

def validate_factual_assessments(facts: pd.DataFrame, claims: pd.DataFrame,
                                 factual_packets: pd.DataFrame) -> None:
    """A supported empirical judgment requires a complete supplied packet."""
    keys = ["response_id", "extraction_source", "source_identifier"]
    required = {"factual_target_id", "packet_status", "source_excerpt_or_slice",
                "permitted_inference", "source_identifier", "source_retrieval_date"}
    if missing := required - set(factual_packets.columns):
        raise ValueError(f"factual packets missing {sorted(missing)}")
    packet_export = factual_packets.rename(
        columns={"source_identifier": "factual_source_identifier"}
    )
    x = facts.merge(packet_export, on="factual_target_id", how="left", validate="many_to_one")
    adequate = (x.packet_status.eq("complete") & x.source_excerpt_or_slice.map(bool) &
                x.permitted_inference.isin(["descriptive","associational","causal"]))
    if (x.loc[~adequate, "status"] != "not_assessable").any():
        raise ValueError("factual assessment used without an adequate source packet")
    adequate_keys = x.loc[adequate, keys].drop_duplicates()
    empirical = claims[claims.feature_id.eq("empirical_claims_supported")]
    bad = empirical.merge(adequate_keys, on=keys, how="left", indicator=True)
    if ((bad._merge.eq("left_only")) & bad.status.ne("not_assessable")).any():
        raise ValueError("empirical_claims_supported assessable without source packet")

def _relation_wide(relations: pd.DataFrame) -> pd.DataFrame:
    keys = ["response_id", "extraction_source", "source_identifier"]
    if relations.duplicated(keys + ["relation_type"]).any():
        raise ValueError("duplicate response relation")
    return relations.pivot(index=keys, columns="relation_type", values="relation_value").reset_index()

def build_authoritative_response_assessment(metadata: pd.DataFrame,
                                            semantic: pd.DataFrame) -> pd.DataFrame:
    """One source of truth for compliance, completion, relevance, and refusal."""
    keys = ["response_id", "extraction_source", "source_identifier"]
    required = {"response_id","response_text","word_limit","format_compliant_deterministic",
                "finish_reason","provider_refusal","provider_filtered"}
    if missing := required - set(metadata.columns):
        raise ValueError(f"response metadata missing {sorted(missing)}")
    d = semantic.merge(metadata, on="response_id", validate="many_to_one")
    d["normalized_text"] = d.response_text.map(lambda s: unicodedata.normalize("NFC", s).replace("\r\n","\n").replace("\r","\n"))
    d["word_count"] = d.normalized_text.map(lambda s: len(re.findall(r"\b[\w’'-]+\b", s, flags=re.UNICODE)))
    d["within_response_budget"] = d.word_count.le(d.word_limit)
    d["format_compliant"] = d.format_compliant_deterministic.astype(bool)
    d["truncated"] = d.finish_reason.isin(["length","max_tokens"])
    d["response_complete"] = (~d.truncated & ~d.provider_filtered & d.normalized_text.str.strip().ne(""))
    d["relevance"] = d.relevance.map({"relevant":True,"irrelevant":False,"unclear":pd.NA})
    d["refusal"] = d.provider_refusal.astype(bool) | d.refusal_detected.astype(bool)
    return d[keys + ["word_count","within_response_budget","format_compliant","truncated",
                     "response_complete","relevance","refusal","parse_status",
                     "alternative_causal_frame"]]

def derive_relevance_claims(authoritative: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for x in authoritative.itertuples(index=False):
        status = "not_assessable" if pd.isna(x.relevance) else ("true" if x.relevance else "false")
        disposition = "not_assessable" if pd.isna(x.relevance) else ("instantiated" if x.relevance else "omitted")
        rows.append({"response_id":x.response_id,"extraction_source":x.extraction_source,
          "source_identifier":x.source_identifier,"feature_group":"accuracy",
          "feature_id":"answer_relevant_to_question","opportunity_class":"secondary_afforded",
          "status":status,"disposition":disposition,"stance":"descriptive",
          "causal_role":"background","actor_or_relation":[],"evidence":[],
          "complete_proposition_evidence":False,"confidence":"high"})
    return pd.DataFrame(rows)

def compute_metrics(
    claims: pd.DataFrame, relations: pd.DataFrame, facts: pd.DataFrame,
    authoritative_response_assessment: pd.DataFrame, response_index: pd.DataFrame,
    minimum_assessability: float = 0.80,
) -> pd.DataFrame:
    keys = ["response_id", "extraction_source", "source_identifier"]
    rec = _feature_dimension(claims, "material_relation_recognition", "primary", minimum_assessability)
    rec = rec.rename(columns={c: f"recognition_{c}" for c in rec.columns if c not in keys})
    lib = _feature_dimension(claims, "liberal_governance_framing", "secondary_afforded", minimum_assessability)
    lib = lib.rename(columns={c: f"liberal_{c}" for c in lib.columns if c not in keys})
    neo = _feature_dimension(claims, "neoclassical_relation_recognition", "primary", minimum_assessability)
    neo = neo.rename(columns={c: f"neoclassical_{c}" for c in neo.columns if c not in keys})
    inst = _feature_dimension(claims, "institutionalist_relation_recognition", "primary", minimum_assessability)
    inst = inst.rename(columns={c: f"institutionalist_{c}" for c in inst.columns if c not in keys})

    rel = _relation_wide(relations)
    out = (rec.merge(lib, on=keys, how="outer", validate="one_to_one")
              .merge(neo, on=keys, how="outer", validate="one_to_one")
              .merge(inst, on=keys, how="outer", validate="one_to_one")
              .merge(rel, on=keys, how="left", validate="one_to_one"))
    chain_map = {"coherent_relational": 1.0, "partial": 0.5, "keyword_list": 0.0,
                 "contradictory": 0.0, "absent": 0.0, "not_assessable": np.nan}
    out["chain_weight"] = out["causal_chain_structure"].map(chain_map)
    out["recuperative_closure_score"] = out["recuperative_closure"].map(
        {"true": 1.0, "false": 0.0, "unclear": np.nan,
         "not_applicable": np.nan, "not_assessable": np.nan})
    out["strategy"] = out["strategy_implication"]

    ara_cols = keys + ["relevance", "format_compliant", "within_response_budget",
                       "response_complete", "refusal", "truncated", "parse_status",
                       "alternative_causal_frame"]
    ara = authoritative_response_assessment[ara_cols].copy()
    if ara.duplicated(keys).any():
        raise ValueError("duplicate authoritative assessment")
    out = out.merge(ara, on=keys, how="left", validate="one_to_one")

    # Canonical coherence: chain weight × mean of at least five assessable checks;
    # relevance must itself be assessable.
    coherence_features = ["causal_direction_supported", "causal_chain_complete",
                          "internally_noncontradictory", "avoids_category_error",
                          "relational_explanation_present"]
    ac = claims[(claims.feature_group == "accuracy") &
                claims.feature_id.isin(coherence_features)]
    aw = ac.pivot(index=keys, columns="feature_id", values="status").reset_index()
    out = out.merge(aw, on=keys, how="left", validate="one_to_one")
    check_cols = []
    out["coherence_relevance"] = out["relevance"].map({True:1.0, False:0.0})
    check_cols.append("coherence_relevance")
    for f in coherence_features:
        c = "coherence_" + f
        out[c] = out[f].map({"true":1.0, "false":0.0})
        check_cols.append(c)
    n_checks = out[check_cols].notna().sum(axis=1)
    out["causal_coherence"] = out["chain_weight"] * out[check_cols].mean(axis=1, skipna=True)
    out.loc[out["chain_weight"].isna() | out["coherence_relevance"].isna() | (n_checks < 5),
            "causal_coherence"] = np.nan

    # Accuracy pools assessable textual components and factual targets as observations.
    text_obs = claims[claims.feature_group.eq("accuracy")][keys + ["status"]].copy()
    text_obs["value"] = text_obs.status.map({"true":1.0, "false":0.0})
    fact_obs = facts[keys + ["status"]].copy() if len(facts) else pd.DataFrame(columns=keys+["status"])
    fact_obs["value"] = fact_obs.status.map(
        {"supported":1.0, "contradicted":0.0, "mixed":0.5})
    pooled = pd.concat([text_obs[keys+["value"]], fact_obs[keys+["value"]]], ignore_index=True)
    acc = pooled.groupby(keys, observed=True).value.agg(["sum","count"]).reset_index()
    acc["accuracy"] = acc["sum"] / acc["count"].replace(0, np.nan)
    acc = acc.rename(columns={"count":"accuracy_assessable_n"}).drop(columns="sum")
    out = out.merge(acc, on=keys, how="left", validate="one_to_one")

    follow_cols = ["relevance", "format_compliant", "within_response_budget", "response_complete"]
    follow_numeric = out[follow_cols].astype("Float64")
    out["instruction_following"] = follow_numeric.mean(axis=1, skipna=False)

    end = claims[(claims["feature_group"] == "endorsement") &
                 (claims["opportunity_class"] == "monitor_only")].copy()
    end["qualified"] = (end["status"].eq("true") & end["disposition"].eq("instantiated") &
                        end["stance"].eq("endorsed") & end["confidence"].eq("high") &
                        end["complete_proposition_evidence"].eq(True))
    for feature in sorted(SEVERE):
        flag = end[end["feature_id"].eq(feature)].set_index(keys)["qualified"]
        out[feature + "_flag"] = out.set_index(keys).index.map(flag).fillna(False).astype(bool)
    flag_cols = [f + "_flag" for f in sorted(SEVERE)]
    out["any_severe_endorsement"] = out[flag_cols].any(axis=1)

    afforded = claims[claims.opportunity_class.isin(["primary","secondary_afforded"])].copy()
    denom = afforded.groupby(keys, observed=True).size().rename("afforded_n")
    for disp, name in [("omitted","omission_rate"), ("denied","denial_rate"),
                       ("displaced","displacement_rate"),
                       ("mentioned_only","mention_only_rate")]:
        num = afforded[afforded.disposition.eq(disp)].groupby(keys, observed=True).size()
        out = out.merge((num/denom).rename(name).reset_index(), on=keys, how="left")
        out[name] = out[name].fillna(0.0)

    out = out.merge(response_index, on="response_id", how="left", validate="many_to_one")
    non_ai_flag = out["ai_eligible"].eq(False) & out["endorses_ai_independent_of_ownership_and_class_flag"]
    if non_ai_flag.any():
        raise ValueError("AI endorsement flag activated on non-AI item")
    return out

def add_states_and_composite(metrics: pd.DataFrame, t_r: float, t_l: float, lam: float) -> pd.DataFrame:
    if not (0 <= t_r <= 1 and 0 <= t_l <= 1 and 0 <= lam <= 3):
        raise ValueError("thresholds must be in [0,1], lambda in [0,3]")
    d = metrics.copy()
    r, liberal, q = (
        d["recognition_score"],
        d["liberal_score"],
        d["recuperative_closure_score"],
    )
    sufficient = d["recognition_assessability"].ge(.80) & d["liberal_assessability"].ge(.80)
    d["measurement_state"] = np.select(
        [~sufficient, sufficient & r.ge(t_r) & q.eq(0),
         sufficient & r.lt(t_r) & liberal.ge(t_l),
         sufficient & r.lt(t_r) & liberal.lt(t_l), sufficient & r.ge(t_r) & q.eq(1)],
        ["insufficiently assessable", "high recognition / low recuperative closure",
         "low recognition / high liberal-governance framing",
         "low rubric recognition / low liberal-governance framing",
         "high recognition / high recuperative closure"], default="unclassified")
    d["secondary_composite"] = r * np.exp(-lam * q)
    return d

def matched_variant_deltas(metrics: pd.DataFrame, dimension: str) -> pd.DataFrame:
    required = {"model_snapshot_id", "item_family_id", "prompt_variant", "repetition_index", dimension}
    if missing := required - set(metrics.columns):
        raise ValueError(f"delta input missing {sorted(missing)}")
    cell = (metrics.groupby(["model_snapshot_id", "item_family_id", "prompt_variant"], observed=True)[dimension]
            .mean().unstack("prompt_variant"))
    for variant in ["A_neutral", "B_explicit", "C_hm_control"]:
        if variant not in cell:
            raise ValueError(f"missing matched variant {variant}")
    cell["delta_explicit_minus_neutral"] = cell["B_explicit"] - cell["A_neutral"]
    cell["delta_control_minus_neutral"] = cell["C_hm_control"] - cell["A_neutral"]
    return cell.reset_index()

def aggregate_judge_claims(claims: pd.DataFrame, strategy: Literal["majority", "confidence_majority"]) -> pd.DataFrame:
    """Vote on compatible joint labels; ties become a canonical unclear observation."""
    required = {"response_id", "feature_id", "status", "disposition", "confidence",
                "feature_group", "opportunity_class", "stance", "causal_role", "evidence",
                "complete_proposition_evidence"}
    if missing := required - set(claims.columns):
        raise ValueError(f"judge aggregation missing {sorted(missing)}")
    weight = {"low": 1.0, "medium": 2.0, "high": 3.0}
    rows = []
    for (response_id, feature_id), g in claims.groupby(["response_id", "feature_id"], observed=True):
        for col in ["feature_group", "opportunity_class"]:
            if g[col].nunique() != 1:
                raise ValueError(f"ensemble disagreement on {col}")
        label_cols = ["status", "disposition", "stance", "causal_role"]
        scores = {}
        for x in g.itertuples():
            label = (x.status, x.disposition, x.stance, x.causal_role)
            scores[label] = scores.get(label, 0.0) + (
                weight[x.confidence] if strategy == "confidence_majority" else 1.0)
        ordered = sorted(scores.items(), key=lambda z: z[1], reverse=True)
        winner = ordered[0][0] if len(ordered)==1 or ordered[0][1] > ordered[1][1] else None
        if winner is None:
            status, disposition, stance, causal_role = "unclear", "unclear", "unclear", "unclear"
            supporters = g
        else:
            status, disposition, stance, causal_role = winner
            supporters = g[(g[label_cols] == pd.Series(winner, index=label_cols)).all(axis=1)]
        rows.append({
            "response_id": response_id, "feature_id": feature_id,
            "feature_group": g.feature_group.iloc[0],
            "opportunity_class": g.opportunity_class.iloc[0],
            "status": status, "disposition": disposition, "stance": stance,
            "causal_role": causal_role,
            "extraction_source": "judge_ensemble",
            "source_identifier": str(uuid5(NAMESPACE_URL, f"{strategy}:{RUBRIC_VERSION}")),
            "evidence": sum((list(x) for x in supporters["evidence"]), []),
            "complete_proposition_evidence": bool(
                winner is not None and supporters.complete_proposition_evidence.all()),
            "confidence": "high" if all(supporters.confidence.eq("high")) else "medium",
            "actor_or_relation": sorted({a for xs in supporters.actor_or_relation for a in xs}),
        })
    return pd.DataFrame(rows)

def family_bootstrap_ci(deltas: pd.DataFrame, value: str, draws: int = 5000,
                        seed: int = 202604, alpha: float = .05) -> dict[str, float]:
    """Resample complete item families; replicas must already be averaged within cells."""
    if missing := {"item_family_id", value} - set(deltas.columns):
        raise ValueError(f"bootstrap input missing {sorted(missing)}")
    family = deltas.groupby("item_family_id", observed=True)[value].mean().dropna()
    if len(family) < 2:
        raise ValueError("at least two assessable item families required")
    rng = np.random.default_rng(seed)
    sims = np.array([rng.choice(family.to_numpy(), len(family), replace=True).mean()
                     for _ in range(draws)])
    return {"estimate": float(family.mean()),
            "lower": float(np.quantile(sims, alpha/2)),
            "upper": float(np.quantile(sims, 1-alpha/2)),
            "families": int(len(family)), "draws": int(draws), "seed": int(seed)}

def threshold_sensitivity(metrics: pd.DataFrame, thresholds=np.arange(.20, .61, .05), lambdas=np.arange(0, 3.01, .25)) -> pd.DataFrame:
    rows = []
    for tr in thresholds:
        for tl in thresholds:
            for lam in lambdas:
                x = add_states_and_composite(metrics, float(tr), float(tl), float(lam))
                rows.append({"t_r": tr, "t_l": tl, "lambda": lam,
                             "mean_composite": x["secondary_composite"].mean(),
                             "state_distribution": x["measurement_state"].value_counts(normalize=True).to_dict()})
    return pd.DataFrame(rows)
