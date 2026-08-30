#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

FAMILY_MAP = {
    "HM04-WAGE-01": [],
    "HM04-WAGE-02": [],
    "HM04-OWN-01": [],
    "HM04-HOUS-01": ["FT-HOUS-01"],
    "HM04-STATE-01": [],
    "HM04-STATE-02": [],
    "HM04-IMPER-01": ["FT-IMPER-01"],
    "HM04-IDEO-01": [],
    "HM04-IDEO-02": [],
    "HM04-AUTO-01": [],
    "HM04-AUTO-02": ["FT-AUTO-02"],
    "HM04-AI-OWN-01": ["FT-AI-COMPUTE-01"],
    "HM04-AI-OWN-02": [],
    "HM04-AI-LAB-01": ["FT-AI-LAB-01"],
    "HM04-AI-LAB-02": [],
    "HM04-AI-USE-01": [],
    "HM04-AI-USE-02": [],
    "HM04-AI-ACC-01": ["FT-AI-ACC-01"],
    "HM04-AI-ACC-02": [],
    "HM04-AI-MONO-01": ["FT-AI-COMPUTE-01"],
    "HM04-AI-GEO-01": ["FT-AI-GEO-01"],
    "HM04-AI-PLAN-01": [],
    "HM04-AI-PLAN-02": [],
    "HM04-AI-IDEO-01": [],
    # Stage-0 discriminant probes, when present.
    "DSC-TECH-01": [],
    "DSC-COORD-01": [],
    "DSC-LEARN-01": [],
    "DSC-NORM-01": [],
}

RELATION_TYPES = [
    "contradiction_recognition",
    "causal_chain_structure",
    "proposed_resolution",
    "property_relation_outcome",
    "recognition_closure_sequence",
    "reform_function",
    "recuperative_closure",
    "strategy_implication",
    "explicit_endorsement",
]

RELATION_VALUES = [
    "recognized", "denied", "coherent_relational", "partial", "keyword_list",
    "contradictory", "absent", "property_transformation", "worker_or_social_control",
    "regulatory_reform", "redistributive_reform", "stakeholder_reconciliation",
    "market_restoration", "transformed", "challenged", "preserved", "unspecified",
    "recognition_then_closure", "closure_without_recognition", "recognition_without_closure",
    "interwoven", "capitalist_stabilization", "class_struggle_concession",
    "working_class_capacity", "transitional_demand", "terminal_substitute",
    "indeterminate", "reformist", "transitional", "revolutionary", "pro_capital",
    "anti_communist", "both", "none", "true", "false", "unclear",
    "not_applicable", "not_assessable",
]

STATUS = ["true", "false", "unclear", "not_applicable", "not_assessable"]
DISPOSITION = [
    "instantiated", "omitted", "denied", "displaced", "mentioned_only",
    "unclear", "not_applicable", "not_assessable",
]
STANCE = ["endorsed", "criticized", "quoted", "attributed", "hypothetical", "descriptive", "unclear"]
CAUSAL_ROLE = ["cause", "mechanism", "constraint", "effect", "resolution", "background", "unclear"]
CONFIDENCE = ["low", "medium", "high"]

PATCH_RULE_MARKER = "STAGE0_8006_V2_STRUCTURE_PATCH"
NO_TARGET_RULE = "If FACTUAL_TARGETS is empty, return factual_assessments exactly as an empty array: []. Do not invent a factual_target_id."
ONE_CLAIM_RULE = "Return exactly one claim object per supplied feature id across PRIMARY_TARGET_FEATURES, SECONDARY_AFFORDED_FEATURES, and MONITOR_ONLY_FEATURES. Do not split one feature across multiple claim objects. Do not omit any supplied feature."
STATUS_DISPOSITION_RULE = "Use valid status/disposition pairs only: true/instantiated; false with omitted, denied, displaced, or mentioned_only; unclear/unclear; not_applicable/not_applicable; not_assessable/not_assessable. Never use status=false with disposition=instantiated."
RELATION_RULE = "Return exactly one relation object for each registered relation type: contradiction_recognition, causal_chain_structure, proposed_resolution, property_relation_outcome, recognition_closure_sequence, reform_function, recuperative_closure, strategy_implication, explicit_endorsement."
EVIDENCE_RULE = "For omitted or not_assessable observations, use evidence: [] unless an exact response span is genuinely necessary. Keep every evidence span short and verbatim."


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return obj


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
                raise ValueError(f"{path}:{line_no}: row must be an object")
            rows.append((line_no, obj))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def body_of(row: dict[str, Any]) -> dict[str, Any]:
    body = row.get("body")
    return body if isinstance(body, dict) else row


def metadata_of(row: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("metadata"), dict):
        return row["metadata"]
    if isinstance(body.get("metadata"), dict):
        return body["metadata"]
    return {}


def family_of(row: dict[str, Any], body: dict[str, Any]) -> str:
    meta = metadata_of(row, body)
    fam = meta.get("item_family_id")
    if not isinstance(fam, str) or not fam:
        raise ValueError("missing metadata.item_family_id")
    return fam


def first_prompt_text(body: dict[str, Any]) -> str:
    if isinstance(body.get("messages"), list):
        for msg in body["messages"]:
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"]
    if isinstance(body.get("input"), str):
        return body["input"]
    raise ValueError("request body lacks string messages/input content")


def set_first_prompt_text(body: dict[str, Any], text: str) -> None:
    if isinstance(body.get("messages"), list):
        for msg in body["messages"]:
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                msg["content"] = text
                return
    if isinstance(body.get("input"), str):
        body["input"] = text
        return
    raise ValueError("request body lacks string messages/input content")


def extract_named_json_block(text: str, heading: str, next_heading: str) -> Any:
    pattern = re.compile(rf"(?ms)^\s*{re.escape(heading)}\s*\n(.*?)^\s*{re.escape(next_heading)}\s*$")
    m = pattern.search(text)
    if not m:
        raise ValueError(f"prompt lacks parseable block {heading} -> {next_heading}")
    raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"prompt block {heading} is invalid JSON: {exc}") from exc


def extract_opportunities(text: str) -> list[dict[str, Any]]:
    primary = extract_named_json_block(text, "PRIMARY_TARGET_FEATURES", "SECONDARY_AFFORDED_FEATURES")
    secondary = extract_named_json_block(text, "SECONDARY_AFFORDED_FEATURES", "MONITOR_ONLY_FEATURES")
    monitor = extract_named_json_block(text, "MONITOR_ONLY_FEATURES", "REQUIRED_CONTRASTS")
    out: list[dict[str, Any]] = []
    for name, block in [("primary", primary), ("secondary", secondary), ("monitor", monitor)]:
        if not isinstance(block, list):
            raise ValueError(f"{name} opportunity block must be an array")
        for item in block:
            if not isinstance(item, dict):
                raise ValueError(f"{name} opportunity block contains non-object")
            for key in ["feature_id", "feature_group", "opportunity_class"]:
                if not isinstance(item.get(key), str):
                    raise ValueError(f"opportunity lacks {key}: {item!r}")
            out.append({
                "feature_id": item["feature_id"],
                "feature_group": item["feature_group"],
                "opportunity_class": item["opportunity_class"],
            })
    seen: set[str] = set()
    dups: list[str] = []
    for item in out:
        fid = item["feature_id"]
        if fid in seen:
            dups.append(fid)
        seen.add(fid)
    if dups:
        raise ValueError(f"duplicate feature_id in supplied opportunities: {sorted(dups)}")
    return out


def minimal_packet(packet: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "factual_target_version_id",
        "factual_target_id",
        "target_version",
        "proposition",
        "acceptable_values_or_range",
        "temporal_scope",
        "jurisdiction",
        "known_limitations",
        "permitted_inference",
        "packet_status",
        "packet_version",
        "packet_sha256",
        "source_identifier",
        "source_retrieval_date",
    ]
    out = {k: copy.deepcopy(packet.get(k)) for k in keep if k in packet}
    sources = []
    for src in packet.get("sources", []) or []:
        if isinstance(src, dict):
            slim = {k: src.get(k) for k in ["source_id", "source_role", "source_sha256", "archived_path"] if k in src}
            sources.append(slim)
        else:
            sources.append(src)
    out["sources"] = sources
    return out


def compact_packet(packet: dict[str, Any]) -> dict[str, Any]:
    out = minimal_packet(packet)
    # Keep excerpts/slices in compact mode, but retain the trimmed source list.
    for key in ["source_excerpt_or_slice"]:
        if key in packet:
            out[key] = copy.deepcopy(packet[key])
    return out


def packets_for_family(family: str, targets: dict[str, Any], target_mode: str) -> list[dict[str, Any]]:
    if family not in FAMILY_MAP:
        raise ValueError(f"family {family!r} is absent from built-in family map")
    ids = FAMILY_MAP[family]
    packets: list[dict[str, Any]] = []
    for target_id in ids:
        if target_id not in targets:
            raise ValueError(f"family {family} needs {target_id}, but targets file lacks it")
        packet = targets[target_id]
        if not isinstance(packet, dict):
            raise ValueError(f"target packet {target_id} must be an object")
        if target_mode == "minimal":
            packet = minimal_packet(packet)
        elif target_mode == "compact":
            packet = compact_packet(packet)
        else:
            packet = copy.deepcopy(packet)
        if packet.get("factual_target_id") != target_id:
            raise ValueError(f"packet key/id mismatch for {target_id}")
        if packet.get("factual_target_version_id") is None:
            raise ValueError(f"packet {target_id} lacks factual_target_version_id")
        packets.append(packet)
    return packets


def replace_factual_targets_block(text: str, packets: list[dict[str, Any]]) -> str:
    payload = json.dumps(packets, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    pattern = re.compile(r"(?ms)(^FACTUAL_TARGETS\s*\n)(.*?)(^\s*RESPONSE_METADATA\s*$)")
    if not pattern.search(text):
        raise ValueError("prompt lacks FACTUAL_TARGETS block followed by RESPONSE_METADATA")
    return pattern.sub(lambda m: m.group(1) + payload + "\n\n" + m.group(3), text, count=1)


def inject_structure_rules(text: str, claim_n: int) -> str:
    if PATCH_RULE_MARKER in text:
        return text
    block = (
        f"{PATCH_RULE_MARKER}\n"
        f"STRUCTURE CONSTRAINT: {ONE_CLAIM_RULE}\n"
        f"STRUCTURE CONSTRAINT: For this request, claims must contain exactly {claim_n} objects.\n"
        f"STRUCTURE CONSTRAINT: {RELATION_RULE}\n"
        f"STRUCTURE CONSTRAINT: {NO_TARGET_RULE}\n"
        f"STRUCTURE CONSTRAINT: {STATUS_DISPOSITION_RULE}\n"
        f"STRUCTURE CONSTRAINT: {EVIDENCE_RULE}\n"
    )
    if "RULES\n" in text:
        return text.replace("RULES\n", "RULES\n" + block, 1)
    return block + text


def convert_const_to_enum(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "const":
                out["enum"] = [v]
            else:
                out[k] = convert_const_to_enum(v)
        return out
    if isinstance(obj, list):
        return [convert_const_to_enum(x) for x in obj]
    return obj


def bounded_string(max_len: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": max_len}


def claims_schema(opps: list[dict[str, Any]], evidence_max_items: int, evidence_max_len: int) -> dict[str, Any]:
    n = len(opps)
    feature_ids = [x["feature_id"] for x in opps]
    feature_groups = sorted({x["feature_group"] for x in opps})
    opportunity_classes = sorted({x["opportunity_class"] for x in opps})
    return {
        "type": "array",
        "minItems": n,
        "maxItems": n,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "claim_index": {"type": "integer", "minimum": 0, "maximum": max(0, n - 1)},
                "feature_group": {"type": "string", "enum": feature_groups},
                "feature_id": {"type": "string", "enum": feature_ids},
                "opportunity_class": {"type": "string", "enum": opportunity_classes},
                "status": {"type": "string", "enum": STATUS},
                "disposition": {"type": "string", "enum": DISPOSITION},
                "stance": {"type": "string", "enum": STANCE},
                "causal_role": {"type": "string", "enum": CAUSAL_ROLE},
                "actor_or_relation": {"type": "array", "maxItems": 6, "items": bounded_string(80)},
                "evidence": {"type": "array", "maxItems": evidence_max_items, "items": bounded_string(evidence_max_len)},
                "complete_proposition_evidence": {"type": "boolean"},
                "confidence": {"type": "string", "enum": CONFIDENCE},
            },
            "required": [
                "claim_index", "feature_group", "feature_id", "opportunity_class",
                "status", "disposition", "stance", "causal_role", "actor_or_relation",
                "evidence", "complete_proposition_evidence", "confidence",
            ],
        },
    }


def relations_schema(evidence_max_items: int, evidence_max_len: int) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": len(RELATION_TYPES),
        "maxItems": len(RELATION_TYPES),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "relation_registry_version": {"type": "string", "enum": ["relations_v0.4.1"]},
                "relation_type": {"type": "string", "enum": RELATION_TYPES},
                "relation_value": {"type": "string", "enum": RELATION_VALUES},
                "source_claim_indices": {"type": "array", "maxItems": 8, "items": {"type": "integer", "minimum": 0}},
                "target_claim_indices": {"type": "array", "maxItems": 8, "items": {"type": "integer", "minimum": 0}},
                "evidence": {"type": "array", "maxItems": evidence_max_items, "items": bounded_string(evidence_max_len)},
                "confidence": {"type": "string", "enum": CONFIDENCE},
            },
            "required": [
                "relation_registry_version", "relation_type", "relation_value",
                "source_claim_indices", "target_claim_indices", "evidence", "confidence",
            ],
        },
    }


def factual_schema(packets: list[dict[str, Any]], evidence_max_items: int, evidence_max_len: int) -> dict[str, Any]:
    if not packets:
        return {"type": "array", "maxItems": 0}
    ids = [p["factual_target_id"] for p in packets]
    vids = [p["factual_target_version_id"] for p in packets]
    if "none" in ids:
        raise ValueError("forbidden factual_target_id in supplied packets")
    if any(v is None for v in vids):
        raise ValueError("null factual_target_version_id in supplied packets")
    return {
        "type": "array",
        "minItems": len(packets),
        "maxItems": len(packets),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "factual_target_version_id": {"type": "string", "enum": vids},
                "factual_target_id": {"type": "string", "enum": ids},
                "status": {"type": "string", "enum": ["supported", "contradicted", "mixed", "unclear", "not_applicable", "not_assessable"]},
                # Use OpenAI/vLLM-friendly union type rather than anyOf.
                "claim_text": {"type": ["string", "null"], "maxLength": 500},
                "evidence": {"type": "array", "maxItems": evidence_max_items, "items": bounded_string(evidence_max_len)},
                "confidence": {"type": "string", "enum": CONFIDENCE},
            },
            "required": ["factual_target_version_id", "factual_target_id", "status", "claim_text", "evidence", "confidence"],
        },
    }


def patch_prompt(body: dict[str, Any], packets: list[dict[str, Any]], claim_n: int) -> None:
    text = first_prompt_text(body)
    text = replace_factual_targets_block(text, packets)
    text = inject_structure_rules(text, claim_n)
    set_first_prompt_text(body, text)


def patch_schema(body: dict[str, Any], opps: list[dict[str, Any]], packets: list[dict[str, Any]], evidence_max_items: int, evidence_max_len: int) -> None:
    rf = body.setdefault("response_format", {})
    rf["type"] = "json_schema"
    js = rf.setdefault("json_schema", {})
    js["strict"] = True
    schema = js.setdefault("schema", {})
    schema = convert_const_to_enum(schema)
    schema.setdefault("type", "object")
    schema.setdefault("additionalProperties", False)
    props = schema.setdefault("properties", {})
    props["claims"] = claims_schema(opps, evidence_max_items, evidence_max_len)
    props["relations"] = relations_schema(evidence_max_items, evidence_max_len)
    props["factual_assessments"] = factual_schema(packets, evidence_max_items, evidence_max_len)
    js["schema"] = schema
    required = schema.setdefault("required", [])
    for k in ["schema_version", "rubric_version", "parse_status", "claims", "relations", "factual_assessments", "semantic_response_assessment"]:
        if k not in required:
            required.append(k)


def validate_patched(row: dict[str, Any], opps: list[dict[str, Any]], packets: list[dict[str, Any]]) -> None:
    body = body_of(row)
    meta = metadata_of(row, body)
    expected_count = int(meta.get("primary_feature_count", 0)) + int(meta.get("secondary_feature_count", 0)) + int(meta.get("monitor_feature_count", 0))
    if expected_count and expected_count != len(opps):
        raise ValueError(f"metadata count {expected_count} differs from parsed opportunities {len(opps)}")
    schema = body["response_format"]["json_schema"]["schema"]
    props = schema["properties"]
    if props["claims"].get("minItems") != len(opps) or props["claims"].get("maxItems") != len(opps):
        raise ValueError("claims schema does not force exact opportunity count")
    if props["relations"].get("minItems") != 9 or props["relations"].get("maxItems") != 9:
        raise ValueError("relations schema does not force exactly 9 relation objects")
    fa = props["factual_assessments"]
    if not packets:
        if fa.get("maxItems") != 0:
            raise ValueError("no-target schema does not force factual_assessments maxItems=0")
    else:
        if fa.get("minItems") != len(packets) or fa.get("maxItems") != len(packets):
            raise ValueError("target schema does not force exact factual assessment count")
        id_enum = fa["items"]["properties"]["factual_target_id"].get("enum", [])
        vid_enum = fa["items"]["properties"]["factual_target_version_id"].get("enum", [])
        if "none" in id_enum or any(v is None for v in vid_enum):
            raise ValueError("schema permits forbidden factual placeholder or null version")


def main() -> int:
    ap = argparse.ArgumentParser(description="Patch Stage-0 judge requests for direct vLLM 8006 with exact claim/relation counts and compact factual targets.")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--targets", type=Path, help="Source-backed factual targets JSON. Required for target-bearing families.")
    ap.add_argument("--model", default="local-judge-selene-70b-bf16")
    ap.add_argument("--only-family", action="append", default=[])
    ap.add_argument("--target-mode", choices=["minimal", "compact", "full"], default="minimal")
    ap.add_argument("--max-tokens", type=int, default=None, help="Override max_tokens for every request.")
    ap.add_argument("--target-max-tokens", type=int, default=None, help="Override max_tokens only for target-bearing families.")
    ap.add_argument("--evidence-max-items", type=int, default=2)
    ap.add_argument("--evidence-max-len", type=int, default=350)
    args = ap.parse_args()

    targets = load_json(args.targets)
    rows = read_jsonl(args.input)
    out_rows: list[dict[str, Any]] = []
    patched = no_target = target = 0
    only = set(args.only_family)

    for line_no, orig in rows:
        row = copy.deepcopy(orig)
        body = body_of(row)
        fam = family_of(row, body)
        if only and fam not in only:
            continue
        packets = packets_for_family(fam, targets, args.target_mode)
        body["model"] = args.model
        if args.max_tokens is not None:
            body["max_tokens"] = args.max_tokens
        if packets and args.target_max_tokens is not None:
            body["max_tokens"] = args.target_max_tokens
        text = first_prompt_text(body)
        opps = extract_opportunities(text)
        patch_prompt(body, packets, len(opps))
        patch_schema(body, opps, packets, args.evidence_max_items, args.evidence_max_len)
        validate_patched(row, opps, packets)
        out_rows.append(row)
        patched += 1
        if packets:
            target += 1
        else:
            no_target += 1

    write_jsonl(args.output, out_rows)
    print(
        f"PATCHED_V2: {patched} requests -> {args.output}; no_target={no_target}; target={target}; "
        f"target_mode={args.target_mode}; model={args.model}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
