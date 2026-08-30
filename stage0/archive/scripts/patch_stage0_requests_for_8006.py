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
    # Discriminant validation items in the current Stage-0 file have no factual targets.
    "DSC-TECH-01": [],
    "DSC-COORD-01": [],
    "DSC-LEARN-01": [],
    "DSC-NORM-01": [],
}

NO_TARGET_RULE = "If FACTUAL_TARGETS is empty, return factual_assessments as an empty array. Do not invent a factual_target_id."
NO_OUTSIDE_FACTS_RULE = "Use factual targets only when the supplied packet is complete and permits the response's inference type. Do not add outside facts."


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return obj


def read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            rows.append((line_no, json.loads(line)))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def body_of(row: dict[str, Any]) -> dict[str, Any]:
    body = row.get("body")
    if isinstance(body, dict):
        return body
    return row


def family_of(row: dict[str, Any], body: dict[str, Any]) -> str:
    for meta in (row.get("metadata"), body.get("metadata")):
        if isinstance(meta, dict) and isinstance(meta.get("item_family_id"), str):
            return meta["item_family_id"]
    raise ValueError("missing metadata.item_family_id")


def compact_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Keep the evidence packet usable while avoiding huge prompt inflation."""
    keep = [
        "factual_target_version_id",
        "factual_target_id",
        "target_version",
        "proposition",
        "source_excerpt_or_slice",
        "source_identifier",
        "source_retrieval_date",
        "acceptable_values_or_range",
        "temporal_scope",
        "jurisdiction",
        "known_limitations",
        "permitted_inference",
        "packet_status",
        "packet_version",
        "packet_sha256",
    ]
    out = {k: copy.deepcopy(packet.get(k)) for k in keep if k in packet}
    # Keep source provenance, but omit bulky or redundant source metadata if present.
    srcs = []
    for s in packet.get("sources", []) or []:
        if isinstance(s, dict):
            srcs.append({k: s.get(k) for k in ["source_id", "url", "archived_path", "source_sha256", "source_role"] if k in s})
        else:
            srcs.append(s)
    out["sources"] = srcs
    return out


def packets_for_family(family: str, targets: dict[str, Any], target_mode: str) -> list[dict[str, Any]]:
    if family not in FAMILY_MAP:
        raise ValueError(f"family {family!r} is absent from built-in family map")
    ids = FAMILY_MAP[family]
    packets = []
    for target_id in ids:
        if target_id not in targets:
            raise ValueError(f"family {family} needs {target_id}, but targets file lacks it")
        packet = targets[target_id]
        if target_mode == "compact":
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
    payload = json.dumps(packets, ensure_ascii=False, sort_keys=True, indent=2)
    pattern = re.compile(r"(?ms)(^FACTUAL_TARGETS\s*\n)(.*?)(^\s*RESPONSE_METADATA\s*$)")
    if not pattern.search(text):
        raise ValueError("prompt lacks a FACTUAL_TARGETS block followed by RESPONSE_METADATA")
    text = pattern.sub(lambda m: m.group(1) + payload + "\n\n" + m.group(3), text, count=1)

    # Add high-priority factual target rules near the top. Duplicate-safe.
    insert_lines = []
    if NO_TARGET_RULE not in text:
        insert_lines.append(NO_TARGET_RULE)
    if NO_OUTSIDE_FACTS_RULE not in text:
        insert_lines.append(NO_OUTSIDE_FACTS_RULE)
    if insert_lines:
        block = "\n".join(f"FACTUAL TARGET CONSTRAINT: {x}" for x in insert_lines) + "\n"
        if "RULES\n" in text:
            text = text.replace("RULES\n", "RULES\n" + block, 1)
        else:
            text = block + text
    return text


def patch_prompt(body: dict[str, Any], packets: list[dict[str, Any]]) -> None:
    if isinstance(body.get("messages"), list):
        for msg in body["messages"]:
            if isinstance(msg, dict) and isinstance(msg.get("content"), str) and "FACTUAL_TARGETS" in msg["content"]:
                msg["content"] = replace_factual_targets_block(msg["content"], packets)
                return
    if isinstance(body.get("input"), str) and "FACTUAL_TARGETS" in body["input"]:
        body["input"] = replace_factual_targets_block(body["input"], packets)
        return
    raise ValueError("request body lacks patchable messages/input FACTUAL_TARGETS block")


def factual_schema(packets: list[dict[str, Any]]) -> dict[str, Any]:
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
                "claim_text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["factual_target_version_id", "factual_target_id", "status", "claim_text", "evidence", "confidence"],
        },
    }


def patch_schema(body: dict[str, Any], packets: list[dict[str, Any]]) -> None:
    rf = body.setdefault("response_format", {})
    rf["type"] = "json_schema"
    js = rf.setdefault("json_schema", {})
    js["strict"] = True
    schema = js.setdefault("schema", {})
    schema.setdefault("type", "object")
    props = schema.setdefault("properties", {})
    props["factual_assessments"] = factual_schema(packets)
    required = schema.setdefault("required", [])
    if "factual_assessments" not in required:
        required.append("factual_assessments")


def validate_patched(row: dict[str, Any], packets: list[dict[str, Any]]) -> None:
    body = body_of(row)
    fa = body["response_format"]["json_schema"]["schema"]["properties"]["factual_assessments"]
    if not packets:
        if fa.get("maxItems") != 0:
            raise ValueError("no-target family schema does not force maxItems:0")
    else:
        if fa.get("minItems") != len(packets) or fa.get("maxItems") != len(packets):
            raise ValueError("target family schema does not force exact factual assessment count")
        id_enum = fa["items"]["properties"]["factual_target_id"].get("enum", [])
        vid_enum = fa["items"]["properties"]["factual_target_version_id"].get("enum", [])
        if "none" in id_enum or any(v is None for v in vid_enum):
            raise ValueError("schema permits forbidden factual target placeholder or null version")


def main() -> int:
    ap = argparse.ArgumentParser(description="Patch Stage-0 judge requests for the direct vLLM Selene endpoint and strict factual target schemas.")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--targets", type=Path, help="Source-backed factual target JSON. Required for target-bearing families unless --only-family selects no-target rows only.")
    ap.add_argument("--model", default="local-judge-selene-70b-bf16")
    ap.add_argument("--max-tokens", type=int, default=None, help="Override max_tokens for every request.")
    ap.add_argument("--target-max-tokens", type=int, default=None, help="Override max_tokens only for target-bearing families.")
    ap.add_argument("--only-family", action="append", default=[])
    ap.add_argument("--target-mode", choices=["compact", "full"], default="compact")
    args = ap.parse_args()

    targets = load_json(args.targets)
    rows = read_jsonl(args.input)
    out_rows = []
    patched = 0
    no_target = 0
    target = 0

    only = set(args.only_family)
    for line_no, row in rows:
        row = copy.deepcopy(row)
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
        patch_prompt(body, packets)
        patch_schema(body, packets)
        validate_patched(row, packets)
        out_rows.append(row)
        patched += 1
        if packets:
            target += 1
        else:
            no_target += 1

    write_jsonl(args.output, out_rows)
    print(f"PATCHED: {patched} requests -> {args.output}; no_target={no_target}; target={target}; model={args.model}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
