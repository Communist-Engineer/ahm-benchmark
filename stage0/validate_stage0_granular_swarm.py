#!/usr/bin/env python3
"""Validate granular judge swarm output against the monolithic contract.

Cross-checks the reassembled ``judge_swarm_reduced.<run_id>.jsonl`` (and the raw
``judge_swarm_outputs.<run_id>.jsonl``) against the v7-patched request JSONL and
enforces:

  * every original request has exactly the required number of reduced claims;
  * every claim_index appears exactly once and is contiguous 0..N-1;
  * every relation type appears exactly once (relation / all modes);
  * factual-assessment cardinality matches supplied FACTUAL_TARGETS;
  * evidence strings are exact substrings of the model response (factual evidence
    may instead come from the supplied factual packet fields);
  * feature evidence is drawn from that feature's evidence bank (accuracy-group
    features are exempt) when --require-feature-specific-evidence is set;
  * failed granular jobs are surfaced, not silently omitted;
  * reduced output preserves custom_id, metadata, and the request body hash.

Exit status is non-zero if any hard check fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stage0_granular_judge_swarm as swarm  # noqa: E402
import patch_stage0_requests_for_8006_v7 as v7  # noqa: E402

VALID_STATUS_DISPOSITION = {
    "true": {"instantiated"},
    "false": {"omitted", "denied", "displaced", "mentioned_only"},
    "unclear": {"unclear"},
    "not_applicable": {"not_applicable"},
    "not_assessable": {"not_assessable"},
}
ABSENT_RELATION_VALUES = {"absent", "not_applicable", "not_assessable", "none", "indeterminate", "unclear"}
FORBIDDEN_FACTUAL_TARGET_ID = "none"
FACTUAL_STATUSES = {"supported", "contradicted", "mixed", "unclear", "not_applicable", "not_assessable"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{line_no}: row is not an object")
            rows.append(obj)
    return rows


def packet_text(packet: dict[str, Any]) -> str:
    return json.dumps(packet, ensure_ascii=False, sort_keys=True)


def validate_row(
    reduced: dict[str, Any],
    pr: swarm.ParsedRequest | None,
    failed_jobs: list[dict[str, Any]],
    args: argparse.Namespace,
    errors: list[str],
    warnings: list[str],
) -> None:
    cid = str(reduced.get("custom_id", "<missing>"))

    def fail(msg: str) -> None:
        errors.append(f"{cid}: {msg}")

    if pr is None:
        fail("reduced row has no matching request in --requests")
        return

    mode = reduced.get("mode") or args.mode
    if mode is None:
        fail("cannot determine mode (no reduced.mode and no --mode)")
        return

    # (8) Preservation of identity/metadata/hash.
    if reduced.get("custom_id") != pr.custom_id:
        fail("reduced custom_id does not match request")
    meta = reduced.get("metadata")
    if not isinstance(meta, dict) or meta.get("item_family_id") != pr.family:
        fail("reduced metadata missing or item_family_id mismatch")
    if reduced.get("request_body_sha256") != pr.request_body_sha256:
        fail(f"request_body_sha256 mismatch (reduced={reduced.get('request_body_sha256')!r} expected={pr.request_body_sha256!r})")

    jo = reduced.get("judge_output")
    if not isinstance(jo, dict):
        fail("missing judge_output object")
        return
    if jo.get("schema_version") != "judge_output_v0.5.0" or jo.get("rubric_version") != "hm_v0.5.0":
        fail("judge_output schema/rubric version mismatch")

    # (7) Failed jobs must not be silently dropped.
    if failed_jobs:
        reported = reduced.get("granular", {}).get("failed_jobs", [])
        reported_ids = {r.get("job_id") for r in reported if isinstance(r, dict)}
        for fj in failed_jobs:
            if fj.get("job_id") not in reported_ids:
                fail(f"failed job {fj.get('job_id')!r} not surfaced in reduced.granular.failed_jobs")
        if args.strict_failed_jobs:
            fail(f"{len(failed_jobs)} granular job(s) failed: {[fj.get('job_id') for fj in failed_jobs][:6]}")
        else:
            warnings.append(f"{cid}: {len(failed_jobs)} granular job(s) failed (non-strict)")

    model_response = pr.model_response
    banks = pr.feature_banks
    optional_groups = set(v7.EVIDENCE_OPTIONAL_GROUPS)
    group_by_fid = {f["feature_id"]: f["feature_group"] for f in pr.features}

    # ---- claims ----
    claims = jo.get("claims")
    want_claims = mode in ("feature", "feature_group", "all")
    if not isinstance(claims, list):
        fail("claims is not an array")
        claims = []
    if want_claims:
        expected_n = args.expected_claims if args.expected_claims is not None else len(pr.features)
        if len(claims) != expected_n:
            fail(f"claims length {len(claims)} != expected {expected_n} (missing/failed feature jobs?)")
        indices = [c.get("claim_index") for c in claims if isinstance(c, dict)]
        if sorted(indices) != list(range(len(claims))):
            fail(f"claim_index values not contiguous 0..{len(claims)-1}: {sorted(indices)}")
        if len(indices) != len(set(indices)):
            fail("duplicate claim_index values in reduced claims")
    elif claims:
        fail(f"mode={mode} should not produce claims but found {len(claims)}")

    true_claims = 0
    evidence_spans = 0
    for c in claims:
        if not isinstance(c, dict):
            fail("claim is not an object")
            continue
        fid = c.get("feature_id")
        status = c.get("status")
        disp = c.get("disposition")
        if status not in VALID_STATUS_DISPOSITION:
            fail(f"claim {c.get('claim_index')} invalid status {status!r}")
        elif disp not in VALID_STATUS_DISPOSITION[status]:
            fail(f"claim {c.get('claim_index')} invalid status/disposition {status!r}/{disp!r}")
        if c.get("causal_role") == "cause" and status in {"false", "unclear", "not_applicable", "not_assessable"}:
            fail(f"claim {c.get('claim_index')} causal_role=cause with non-affirmative status {status!r}")
        ev = c.get("evidence")
        if not isinstance(ev, list):
            fail(f"claim {c.get('claim_index')} evidence not an array")
            ev = []
        group = group_by_fid.get(fid)
        feature_optional = group in optional_groups
        if status == "true":
            true_claims += 1
            if not ev and not feature_optional:
                fail(f"claim {c.get('claim_index')} status=true but evidence empty")
        for span in ev:
            if not isinstance(span, str) or not span:
                continue
            evidence_spans += 1
            if span not in model_response:
                fail(f"claim {c.get('claim_index')} evidence not exact substring of model_response: {span[:80]!r}")
            if args.require_feature_specific_evidence and group not in optional_groups:
                bank = banks.get(fid)
                if bank is None:
                    fail(f"claim {c.get('claim_index')} feature {fid!r} has no bank but produced evidence")
                elif span not in bank:
                    fail(f"claim {c.get('claim_index')} feature {fid!r} evidence not from its bank: {span[:80]!r}")

    if want_claims and true_claims < args.min_true_claims:
        fail(f"true claim count {true_claims} < required {args.min_true_claims}")
    if want_claims and evidence_spans < args.min_evidence_spans:
        fail(f"evidence span count {evidence_spans} < required {args.min_evidence_spans}")

    # ---- relations ----
    relations = jo.get("relations")
    want_relations = mode in ("relation", "all")
    if not isinstance(relations, list):
        fail("relations is not an array")
        relations = []
    if want_relations:
        types = [r.get("relation_type") for r in relations if isinstance(r, dict)]
        if len(relations) != len(v7.RELATION_TYPES):
            fail(f"relations length {len(relations)} != {len(v7.RELATION_TYPES)}")
        if set(types) != set(v7.RELATION_TYPES):
            fail(f"relation types mismatch missing={sorted(set(v7.RELATION_TYPES)-set(types))} extra={sorted(set(types)-set(v7.RELATION_TYPES))}")
        if len(types) != len(set(types)):
            fail("duplicate relation_type in reduced relations")
    elif relations:
        fail(f"mode={mode} should not produce relations but found {len(relations)}")

    nonabsent = 0
    for r in relations:
        if not isinstance(r, dict):
            fail("relation is not an object")
            continue
        if r.get("relation_value") not in ABSENT_RELATION_VALUES:
            nonabsent += 1
        for span in r.get("evidence", []) or []:
            if isinstance(span, str) and span and span not in model_response:
                fail(f"relation {r.get('relation_type')} evidence not exact substring of model_response: {span[:80]!r}")
    if want_relations and args.min_nonabsent_relations and nonabsent < args.min_nonabsent_relations:
        fail(f"nonabsent relation count {nonabsent} < required {args.min_nonabsent_relations}")

    # ---- factual assessments ----
    facts = jo.get("factual_assessments")
    want_factual = mode in ("factual", "all")
    if not isinstance(facts, list):
        fail("factual_assessments is not an array")
        facts = []
    supplied = pr.factual_targets or []
    allowed_ids = {p.get("factual_target_id") for p in supplied}
    allowed_vids = {p.get("factual_target_version_id") for p in supplied}
    packet_status_by_id = {p.get("factual_target_id"): p.get("packet_status") for p in supplied}
    packet_by_id = {p.get("factual_target_id"): p for p in supplied}
    if want_factual:
        if len(facts) != len(supplied):
            fail(f"factual_assessments length {len(facts)} != supplied targets {len(supplied)}")
    elif facts:
        fail(f"mode={mode} should not produce factual_assessments but found {len(facts)}")
    for fa in facts:
        if not isinstance(fa, dict):
            fail("factual assessment is not an object")
            continue
        tid = fa.get("factual_target_id")
        vid = fa.get("factual_target_version_id")
        if tid == FORBIDDEN_FACTUAL_TARGET_ID:
            fail("factual_target_id uses forbidden placeholder 'none'")
        if vid is None:
            fail("factual_target_version_id is null")
        if tid not in allowed_ids:
            fail(f"factual_target_id {tid!r} not supplied")
        if vid not in allowed_vids:
            fail(f"factual_target_version_id {vid!r} not supplied")
        if fa.get("status") not in FACTUAL_STATUSES:
            fail(f"factual status {fa.get('status')!r} invalid")
        if packet_status_by_id.get(tid) != "complete" and fa.get("status") != "not_assessable":
            fail(f"non-complete packet {tid!r} produced assessable status {fa.get('status')!r}")
        # Factual evidence: exact substring of response OR of the packet fields.
        pkt_txt = packet_text(packet_by_id.get(tid, {}))
        for span in fa.get("evidence", []) or []:
            if isinstance(span, str) and span and span not in model_response and span not in pkt_txt:
                fail(f"factual evidence not from model_response nor packet fields: {span[:80]!r}")

    # ---- semantic assessment ----
    sem = jo.get("semantic_response_assessment")
    if not isinstance(sem, dict):
        fail("semantic_response_assessment missing")
    else:
        if sem.get("relevance") not in {"relevant", "irrelevant", "unclear"}:
            fail(f"semantic relevance invalid: {sem.get('relevance')!r}")
        if not isinstance(sem.get("refusal_detected"), bool):
            fail("semantic refusal_detected not boolean")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate granular judge swarm output against the monolithic contract.")
    ap.add_argument("--requests", required=True, type=Path, help="v7-patched request JSONL used to build the swarm")
    ap.add_argument("--reduced", required=True, type=Path, help="judge_swarm_reduced.<run_id>.jsonl")
    ap.add_argument("--outputs", type=Path, help="judge_swarm_outputs.<run_id>.jsonl (to detect failed jobs)")
    ap.add_argument("--mode", choices=["feature", "feature_group", "relation", "factual", "all"], help="Override mode (else read from reduced rows)")
    ap.add_argument("--expected-claims", type=int, default=None, help="Override expected claim count (for --feature-limit runs)")
    ap.add_argument("--require-feature-specific-evidence", action="store_true")
    ap.add_argument("--strict-failed-jobs", action="store_true", help="Treat any failed granular job as an error")
    ap.add_argument("--min-true-claims", type=int, default=0)
    ap.add_argument("--min-evidence-spans", type=int, default=0)
    ap.add_argument("--min-nonabsent-relations", type=int, default=0)
    ap.add_argument("--evidence-max-len", type=int, default=220)
    ap.add_argument("--evidence-min-len", type=int, default=12)
    ap.add_argument("--evidence-per-feature", type=int, default=6)
    ap.add_argument("--evidence-enum-limit", type=int, default=400)
    args = ap.parse_args()

    request_rows = read_jsonl(args.requests)
    parsed: dict[str, swarm.ParsedRequest] = {}
    for row in request_rows:
        pr = swarm.parse_request(row, args.evidence_max_len, args.evidence_min_len,
                                 args.evidence_per_feature, args.evidence_enum_limit)
        parsed[pr.custom_id] = pr

    failed_by_cid: dict[str, list[dict[str, Any]]] = {}
    if args.outputs and args.outputs.exists():
        for o in read_jsonl(args.outputs):
            if o.get("ok") is not True:
                failed_by_cid.setdefault(o.get("custom_id"), []).append(o)

    reduced_rows = read_jsonl(args.reduced)
    errors: list[str] = []
    warnings: list[str] = []
    for reduced in reduced_rows:
        pr = parsed.get(reduced.get("custom_id"))
        validate_row(reduced, pr, failed_by_cid.get(reduced.get("custom_id"), []), args, errors, warnings)

    for w in warnings:
        print(f"WARNING: {w}")
    if errors:
        for e in errors[:100]:
            print(f"ERROR: {e}", file=sys.stderr)
        if len(errors) > 100:
            print(f"ERROR: ... {len(errors)-100} more", file=sys.stderr)
        print(f"SWARM VALIDATION FAILED: reduced_rows={len(reduced_rows)} errors={len(errors)} warnings={len(warnings)}", file=sys.stderr)
        return 1
    print(f"SWARM VALIDATION PASSED: reduced_rows={len(reduced_rows)} warnings={len(warnings)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
