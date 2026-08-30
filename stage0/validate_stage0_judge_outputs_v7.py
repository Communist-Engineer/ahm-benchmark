#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_RELATION_TYPES = {
    "contradiction_recognition",
    "causal_chain_structure",
    "proposed_resolution",
    "property_relation_outcome",
    "recognition_closure_sequence",
    "reform_function",
    "recuperative_closure",
    "strategy_implication",
    "explicit_endorsement",
}

FORBIDDEN_FACTUAL_TARGET_ID = "none"
VALID_STATUS_DISPOSITION = {
    "true": {"instantiated"},
    "false": {"omitted", "denied", "displaced", "mentioned_only"},
    "unclear": {"unclear"},
    "not_applicable": {"not_applicable"},
    "not_assessable": {"not_assessable"},
}

# Relation values that count as "no relation present".
ABSENT_RELATION_VALUES = {"absent", "not_applicable", "not_assessable", "none", "indeterminate", "unclear"}


def read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{line_no}: row must be a JSON object")
            rows.append((line_no, obj))
    return rows


def active_body(row: dict[str, Any]) -> dict[str, Any]:
    body = row.get("body")
    return body if isinstance(body, dict) else row


def request_metadata(row: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("metadata"), dict):
        return row["metadata"]
    if isinstance(body.get("metadata"), dict):
        return body["metadata"]
    return {}


def request_texts(row: dict[str, Any]) -> list[str]:
    body = active_body(row)
    texts: list[str] = []
    if isinstance(body.get("messages"), list):
        for msg in body["messages"]:
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                texts.append(msg["content"])
    if isinstance(body.get("input"), str):
        texts.append(body["input"])
    return texts


def extract_factual_targets_from_request(row: dict[str, Any]) -> list[dict[str, Any]] | None:
    pattern = re.compile(r"(?ms)^FACTUAL_TARGETS\s*\n(.*?)^\s*RESPONSE_METADATA\s*$")
    for text in request_texts(row):
        # v3 compact prompt is a single JSON object with key factual_targets.
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and isinstance(obj.get("factual_targets"), list):
                return obj["factual_targets"]
        except json.JSONDecodeError:
            pass
        # v1/v2 prompt uses a named FACTUAL_TARGETS block.
        m = pattern.search(text)
        if m:
            try:
                val = json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                return None
            return val if isinstance(val, list) else None
    return None


def extract_feature_order_from_request(row: dict[str, Any]) -> list[str] | None:
    for text in request_texts(row):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("features"), list):
            out = []
            for item in obj["features"]:
                if isinstance(item, dict) and isinstance(item.get("feature_id"), str):
                    out.append(item["feature_id"])
            return out
    return None


def extract_model_response_from_request(row: dict[str, Any]) -> str | None:
    for text in request_texts(row):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("model_response"), str):
            return obj["model_response"]
        matches = [m.group(1).strip() for m in re.finditer(r"(?ms)<MODEL_RESPONSE>(.*?)</MODEL_RESPONSE>", text)]
        nontrivial = [m for m in matches if len(m.strip()) >= 20]
        if nontrivial:
            return nontrivial[-1]
        if matches:
            return matches[-1]
    return None


def index_requests(requests: list[tuple[int, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line_no, row in requests:
        cid = row.get("custom_id")
        if isinstance(cid, str):
            out[cid] = row
    return out


def fail(errors: list[str], line_no: int, custom_id: str, msg: str) -> None:
    errors.append(f"line {line_no} {custom_id}: {msg}")


def feature_banks_of(req: dict[str, Any] | None) -> dict[str, list[str]]:
    if isinstance(req, dict) and isinstance(req.get("hm_evidence_banks"), dict):
        return req["hm_evidence_banks"]
    return {}


def feature_groups_of(req: dict[str, Any] | None) -> dict[str, str]:
    if isinstance(req, dict) and isinstance(req.get("hm_feature_groups"), dict):
        return req["hm_feature_groups"]
    return {}


def evidence_optional_groups_of(req: dict[str, Any] | None) -> set[str]:
    if isinstance(req, dict) and isinstance(req.get("hm_evidence_optional_groups"), list):
        return set(req["hm_evidence_optional_groups"])
    return {"accuracy"}


def validate_one(
    line_no: int,
    row: dict[str, Any],
    req: dict[str, Any] | None,
    strict_failed_rows: bool,
    errors: list[str],
    warnings: list[str],
    min_model_response_chars: int,
    min_true_claims: int,
    min_evidence_spans: int,
    min_nonabsent_relations: int,
    require_feature_specific_evidence: bool,
) -> None:
    custom_id = str(row.get("custom_id", "<missing-custom-id>"))
    ok = row.get("ok") is True
    if not ok:
        msg = row.get("error", "row ok=false")
        if strict_failed_rows:
            fail(errors, line_no, custom_id, f"runner failure: {msg}")
        else:
            warnings.append(f"line {line_no} {custom_id}: runner failure retained: {msg}")
        return

    jo = row.get("judge_output")
    if not isinstance(jo, dict):
        fail(errors, line_no, custom_id, "missing judge_output object")
        return

    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if req is not None:
        body = active_body(req)
        req_meta = request_metadata(req, body)
        meta = {**req_meta, **meta}

    expected_claims = int(meta.get("primary_feature_count", 0)) + int(meta.get("secondary_feature_count", 0)) + int(meta.get("monitor_feature_count", 0))
    claims = jo.get("claims")
    if not isinstance(claims, list):
        fail(errors, line_no, custom_id, "claims is not an array")
        return
    if expected_claims and len(claims) != expected_claims:
        fail(errors, line_no, custom_id, f"claims length {len(claims)} != expected {expected_claims}")

    expected_feature_order = extract_feature_order_from_request(req) if req is not None else None
    model_response = extract_model_response_from_request(req) if req is not None else None
    if req is not None:
        if not isinstance(model_response, str):
            fail(errors, line_no, custom_id, "request does not expose model_response for evidence validation")
            model_response = ""
        elif len(model_response.strip()) < min_model_response_chars:
            fail(errors, line_no, custom_id, f"model_response too short ({len(model_response.strip())} chars); likely extracted tag prose instead of response")

    feature_ids: list[str] = []
    claim_indices: list[int] = []
    true_claims = 0
    evidence_spans = 0
    banks = feature_banks_of(req)
    groups = feature_groups_of(req)
    optional_groups = evidence_optional_groups_of(req)
    if require_feature_specific_evidence and req is not None and not banks:
        fail(errors, line_no, custom_id, "request lacks hm_evidence_banks; cannot enforce feature-specific evidence")
    for idx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            fail(errors, line_no, custom_id, f"claim {idx} is not object")
            continue
        fid = claim.get("feature_id")
        if isinstance(fid, str):
            feature_ids.append(fid)
        cidx = claim.get("claim_index")
        if isinstance(cidx, int):
            claim_indices.append(cidx)
        status = claim.get("status")
        disp = claim.get("disposition")
        if status not in VALID_STATUS_DISPOSITION:
            fail(errors, line_no, custom_id, f"claim {idx} invalid status {status!r}")
        elif disp not in VALID_STATUS_DISPOSITION[status]:
            fail(errors, line_no, custom_id, f"claim {idx} invalid status/disposition pair {status!r}/{disp!r}")
        # causal_role must not assert causation for absent/unassessable claims.
        causal_role = claim.get("causal_role")
        if causal_role == "cause" and status in {"false", "unclear", "not_applicable", "not_assessable"}:
            fail(errors, line_no, custom_id, f"claim {idx} causal_role=cause with non-affirmative status {status!r}")
        ev = claim.get("evidence")
        if not isinstance(ev, list):
            fail(errors, line_no, custom_id, f"claim {idx} evidence is not an array")
            ev = []
        feature_group = groups.get(fid) if isinstance(fid, str) else None
        feature_optional = feature_group in optional_groups
        bank = banks.get(fid) if isinstance(fid, str) else None
        if status == "true":
            true_claims += 1
            if not ev and not feature_optional:
                fail(errors, line_no, custom_id, f"claim {idx} status=true but evidence is empty")
        for span in ev:
            if not isinstance(span, str):
                fail(errors, line_no, custom_id, f"claim {idx} evidence span is not string")
                continue
            if span:
                evidence_spans += 1
                if model_response is not None and span not in model_response:
                    fail(errors, line_no, custom_id, f"claim {idx} evidence is not an exact substring of model_response: {span[:80]!r}")
                if require_feature_specific_evidence and banks and not feature_optional:
                    if bank is None:
                        fail(errors, line_no, custom_id, f"claim {idx} feature {fid!r} has no evidence bank but produced evidence")
                    elif span not in bank:
                        fail(errors, line_no, custom_id, f"claim {idx} feature {fid!r} evidence not drawn from that feature's bank: {span[:80]!r}")

    if true_claims < min_true_claims:
        fail(errors, line_no, custom_id, f"true claim count {true_claims} < required minimum {min_true_claims}")
    if evidence_spans < min_evidence_spans:
        fail(errors, line_no, custom_id, f"evidence span count {evidence_spans} < required minimum {min_evidence_spans}")

    if len(feature_ids) != len(set(feature_ids)):
        duplicates = sorted({x for x in feature_ids if feature_ids.count(x) > 1})
        fail(errors, line_no, custom_id, f"duplicate feature_id observations: {duplicates}")
    if expected_feature_order is not None and feature_ids != expected_feature_order:
        fail(errors, line_no, custom_id, "feature_id order/content does not match request features")
    if claim_indices and sorted(claim_indices) != list(range(len(claims))):
        fail(errors, line_no, custom_id, "claim_index values are not contiguous 0..len(claims)-1")

    relations = jo.get("relations")
    if not isinstance(relations, list):
        fail(errors, line_no, custom_id, "relations is not an array")
    else:
        types = [r.get("relation_type") for r in relations if isinstance(r, dict)]
        if len(relations) != 9:
            fail(errors, line_no, custom_id, f"relations length {len(relations)} != expected 9")
        if set(types) != REQUIRED_RELATION_TYPES:
            fail(errors, line_no, custom_id, f"relation types mismatch missing={sorted(REQUIRED_RELATION_TYPES - set(types))} extra={sorted(set(types) - REQUIRED_RELATION_TYPES)}")
        if len(types) != len(set(types)):
            duplicates = sorted({x for x in types if types.count(x) > 1})
            fail(errors, line_no, custom_id, f"duplicate relation_type observations: {duplicates}")
        nonabsent_relations = 0
        for ridx, rel in enumerate(relations):
            if not isinstance(rel, dict):
                fail(errors, line_no, custom_id, f"relation {ridx} is not object")
                continue
            if rel.get("relation_value") not in ABSENT_RELATION_VALUES:
                nonabsent_relations += 1
            rev = rel.get("evidence")
            if not isinstance(rev, list):
                fail(errors, line_no, custom_id, f"relation {ridx} evidence is not an array")
                continue
            for span in rev:
                if not isinstance(span, str):
                    fail(errors, line_no, custom_id, f"relation {ridx} evidence span is not string")
                    continue
                if span and model_response is not None and span not in model_response:
                    fail(errors, line_no, custom_id, f"relation {ridx} evidence is not an exact substring of model_response: {span[:80]!r}")
        if min_nonabsent_relations and nonabsent_relations < min_nonabsent_relations:
            fail(errors, line_no, custom_id, f"nonabsent relation count {nonabsent_relations} < required minimum {min_nonabsent_relations}")

    facts = jo.get("factual_assessments")
    if not isinstance(facts, list):
        fail(errors, line_no, custom_id, "factual_assessments is not an array")
        return

    supplied = extract_factual_targets_from_request(req) if req is not None else None
    if supplied is not None:
        allowed_ids = {p.get("factual_target_id") for p in supplied if isinstance(p, dict)}
        allowed_versions = {p.get("factual_target_version_id") for p in supplied if isinstance(p, dict)}
        if not supplied and facts != []:
            fail(errors, line_no, custom_id, "FACTUAL_TARGETS empty but factual_assessments is not []")
        if supplied and len(facts) != len(supplied):
            fail(errors, line_no, custom_id, f"factual_assessments length {len(facts)} != supplied packet count {len(supplied)}")
        for fact in facts:
            if not isinstance(fact, dict):
                fail(errors, line_no, custom_id, "factual assessment is not an object")
                continue
            tid = fact.get("factual_target_id")
            vid = fact.get("factual_target_version_id")
            if tid == FORBIDDEN_FACTUAL_TARGET_ID:
                fail(errors, line_no, custom_id, "factual_target_id uses forbidden placeholder")
            if vid is None:
                fail(errors, line_no, custom_id, "factual_target_version_id is null")
            if supplied and tid not in allowed_ids:
                fail(errors, line_no, custom_id, f"factual_target_id {tid!r} not supplied")
            if supplied and vid not in allowed_versions:
                fail(errors, line_no, custom_id, f"factual_target_version_id {vid!r} not supplied")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Stage-0 judge output structural completeness and feature-specific evidence alignment (v7).")
    ap.add_argument("--outputs", required=True, type=Path)
    ap.add_argument("--requests", type=Path, help="Patched request JSONL used for this run. Enables factual-target and feature-bank cross-checks.")
    ap.add_argument("--strict-failed-rows", action="store_true", help="Treat ok=false runner rows as validation errors instead of warnings.")
    ap.add_argument("--min-model-response-chars", type=int, default=20, help="Reject requests whose compact model_response is suspiciously short.")
    ap.add_argument("--min-true-claims", type=int, default=1, help="Require at least this many true claim observations per successful row.")
    ap.add_argument("--min-evidence-spans", type=int, default=1, help="Require at least this many nonempty exact evidence spans per successful row.")
    ap.add_argument("--min-nonabsent-relations", type=int, default=0, help="Require at least this many relations with a non-absent relation_value per successful row.")
    ap.add_argument("--require-feature-specific-evidence", action="store_true", help="Require each claim's evidence to be drawn from that feature's evidence bank (accuracy-group features exempt).")
    args = ap.parse_args()

    output_rows = read_jsonl(args.outputs)
    request_index: dict[str, dict[str, Any]] = {}
    if args.requests:
        request_index = index_requests(read_jsonl(args.requests))

    errors: list[str] = []
    warnings: list[str] = []
    for line_no, row in output_rows:
        cid = row.get("custom_id")
        req = request_index.get(cid) if isinstance(cid, str) else None
        validate_one(
            line_no, row, req, args.strict_failed_rows, errors, warnings,
            args.min_model_response_chars, args.min_true_claims, args.min_evidence_spans,
            args.min_nonabsent_relations, args.require_feature_specific_evidence,
        )

    for w in warnings:
        print(f"WARNING: {w}")
    if errors:
        for e in errors[:100]:
            print(f"ERROR: {e}", file=sys.stderr)
        if len(errors) > 100:
            print(f"ERROR: ... {len(errors)-100} more error(s)", file=sys.stderr)
        print(f"VALIDATION FAILED: rows={len(output_rows)} errors={len(errors)} warnings={len(warnings)}", file=sys.stderr)
        return 1
    print(f"VALIDATION PASSED: rows={len(output_rows)} warnings={len(warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
