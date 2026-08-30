#!/usr/bin/env python3
"""
Patch and optionally execute Stage-0 judge batch requests for Selene via LiteLLM/vLLM.

What this fixes:
  - body.model -> local-judge-selene
  - body.response_format -> OpenAI-style json_schema with strict=true
  - leaves custom_id, method, url, prompt, metadata, temperature, top_p, and max_tokens intact

Why:
  On the tested Selene/vLLM deployment, response_format={"type":"json_object"} and guided_json
  were insufficient for schema discipline. response_format={"type":"json_schema", ...}
  passed strict validation.

Typical use:
  python3 patch_stage0_judge_requests.py \
    --input judge_requests.jsonl \
    --output judge_requests.selene_litellm.jsonl

Optional execution through LiteLLM:
  python3 patch_stage0_judge_requests.py \
    --input judge_requests.jsonl \
    --output judge_requests.selene_litellm.jsonl \
    --execute \
    --api-root https://127.0.0.1:8005 \
    --insecure \
    --responses-out judge_outputs.selene_litellm.jsonl
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


STATUS = ["true", "false", "unclear", "not_applicable", "not_assessable"]
DISPOSITION = [
    "instantiated", "omitted", "denied", "displaced", "mentioned_only",
    "unclear", "not_applicable", "not_assessable",
]
STANCE = ["endorsed", "criticized", "quoted", "attributed", "hypothetical", "descriptive", "unclear"]
CAUSAL_ROLE = ["cause", "mechanism", "constraint", "effect", "resolution", "background", "unclear"]
CONFIDENCE = ["low", "medium", "high"]
OPPORTUNITY_CLASS = ["primary", "secondary_afforded", "monitor_only"]
FEATURE_GROUPS = [
    "material_relation_recognition",
    "neoclassical_relation_recognition",
    "institutionalist_relation_recognition",
    "liberal_governance_framing",
    "endorsement",
    "accuracy",
    "instruction_following",
]
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
    "indeterminate", "reformist", "revolutionary", "pro_capital",
    "anti_communist", "both", "none", "true", "false", "unclear",
    "not_applicable", "not_assessable",
]
FACT_STATUS = ["supported", "contradicted", "mixed", "unclear", "not_applicable", "not_assessable"]
ALT_FRAME = [
    "neoclassical", "institutionalist", "social_democratic", "conservative", "anarchist",
    "technical", "mixed", "other", "absent", "unclear",
]


def judge_output_schema() -> dict[str, Any]:
    """Strict-enough v0.5.0 judge output schema for vLLM response_format=json_schema.

    This intentionally avoids JSON-Schema conditionals for status/disposition compatibility,
    because those are easier to enforce deterministically after generation. The structural
    shape, required keys, enum domains, and additionalProperties=false are enforced here.
    """
    claim = {
        "type": "object",
        "properties": {
            "claim_index": {"type": "integer", "minimum": 0},
            "feature_group": {"type": "string", "enum": FEATURE_GROUPS},
            "feature_id": {"type": "string"},
            "opportunity_class": {"type": "string", "enum": OPPORTUNITY_CLASS},
            "status": {"type": "string", "enum": STATUS},
            "disposition": {"type": "string", "enum": DISPOSITION},
            "stance": {"type": "string", "enum": STANCE},
            "causal_role": {"type": "string", "enum": CAUSAL_ROLE},
            "actor_or_relation": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "complete_proposition_evidence": {"type": "boolean"},
            "confidence": {"type": "string", "enum": CONFIDENCE},
        },
        "required": [
            "claim_index", "feature_group", "feature_id", "opportunity_class",
            "status", "disposition", "stance", "causal_role", "actor_or_relation",
            "evidence", "complete_proposition_evidence", "confidence",
        ],
        "additionalProperties": False,
    }

    relation = {
        "type": "object",
        "properties": {
            "relation_registry_version": {"type": "string", "const": "relations_v0.4.1"},
            "relation_type": {"type": "string", "enum": RELATION_TYPES},
            "relation_value": {"type": "string", "enum": RELATION_VALUES},
            "source_claim_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}},
            "target_claim_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": CONFIDENCE},
        },
        "required": [
            "relation_registry_version", "relation_type", "relation_value",
            "source_claim_indices", "target_claim_indices", "evidence", "confidence",
        ],
        "additionalProperties": False,
    }

    fact = {
        "type": "object",
        "properties": {
            "factual_target_version_id": {"type": ["string", "null"]},
            "factual_target_id": {"type": ["string", "null"]},
            "status": {"type": "string", "enum": FACT_STATUS},
            "claim_text": {"type": ["string", "null"]},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": CONFIDENCE},
        },
        "required": [
            "factual_target_version_id", "factual_target_id", "status",
            "claim_text", "evidence", "confidence",
        ],
        "additionalProperties": False,
    }

    semantic_response_assessment = {
        "type": "object",
        "properties": {
            "relevance": {"type": "string", "enum": ["relevant", "irrelevant", "unclear"]},
            "refusal_detected": {"type": "boolean"},
            "refusal_evidence": {"type": "array", "items": {"type": "string"}},
            "alternative_causal_frame": {"type": "string", "enum": ALT_FRAME},
        },
        "required": [
            "relevance", "refusal_detected", "refusal_evidence", "alternative_causal_frame",
        ],
        "additionalProperties": False,
    }

    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string", "const": "judge_output_v0.5.0"},
            "rubric_version": {"type": "string", "const": "hm_v0.5.0"},
            "parse_status": {"type": "string", "enum": ["ok", "partial", "failed"]},
            "claims": {"type": "array", "items": claim},
            "relations": {"type": "array", "items": relation},
            "factual_assessments": {"type": "array", "items": fact},
            "semantic_response_assessment": semantic_response_assessment,
        },
        "required": [
            "schema_version", "rubric_version", "parse_status", "claims",
            "relations", "factual_assessments", "semantic_response_assessment",
        ],
        "additionalProperties": False,
    }


def response_format_json_schema(schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
            "strict": True,
        },
    }


def load_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{line_no}: invalid JSONL: {e}") from e


def patch_record(
    record: dict[str, Any],
    judge_model: str,
    schema_name: str,
    max_tokens: int | None,
) -> dict[str, Any]:
    out = dict(record)
    body = dict(out.get("body") or {})

    if not body:
        raise ValueError(f"{record.get('custom_id', '<unknown>')}: missing body")

    body["model"] = judge_model
    if max_tokens is not None:
        body["max_tokens"] = int(max_tokens)

    # The key fix: json_object is too weak; guided_json was not accepted by this deployment.
    body.pop("guided_json", None)
    body["response_format"] = response_format_json_schema(judge_output_schema(), schema_name)

    out["body"] = body
    return out


def write_jsonl(records, path: Path) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
            n += 1
    return n


def validate_patched_file(path: Path, expected_model: str) -> dict[str, Any]:
    n = 0
    models = {}
    response_formats = {}
    bad = []
    for line_no, r in load_jsonl(path):
        n += 1
        body = r.get("body") or {}
        model = body.get("model")
        models[model] = models.get(model, 0) + 1
        rf = body.get("response_format")
        rf_type = (rf or {}).get("type")
        response_formats[rf_type] = response_formats.get(rf_type, 0) + 1
        if model != expected_model:
            bad.append(f"line {line_no}: model={model!r}")
        if rf_type != "json_schema":
            bad.append(f"line {line_no}: response_format.type={rf_type!r}")
        try:
            js = rf["json_schema"]
            assert js["strict"] is True
            assert js["schema"]["properties"]["schema_version"]["const"] == "judge_output_v0.5.0"
            assert js["schema"]["properties"]["rubric_version"]["const"] == "hm_v0.5.0"
        except Exception as e:
            bad.append(f"line {line_no}: invalid json_schema wrapper: {e}")
    return {"records": n, "models": models, "response_formats": response_formats, "bad": bad}


def post_json(url: str, payload: dict[str, Any], timeout: int, insecure: bool) -> tuple[int, dict[str, Any] | str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    context = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def strict_extract_content(openai_response: dict[str, Any]) -> dict[str, Any]:
    text = openai_response["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.lstrip().startswith("{"):
        raise ValueError("judge_output_not_raw_json")
    obj = json.loads(text)

    # Lightweight post-validation for production triage. Full DB/schema validation remains downstream.
    required = [
        "schema_version", "rubric_version", "parse_status", "claims",
        "relations", "factual_assessments", "semantic_response_assessment",
    ]
    for k in required:
        if k not in obj:
            raise ValueError(f"judge_output_missing_{k}")
    if obj["schema_version"] != "judge_output_v0.5.0":
        raise ValueError("judge_output_bad_schema_version")
    if obj["rubric_version"] != "hm_v0.5.0":
        raise ValueError("judge_output_bad_rubric_version")
    if not isinstance(obj["claims"], list):
        raise ValueError("judge_output_claims_not_array")
    if not isinstance(obj["relations"], list):
        raise ValueError("judge_output_relations_not_array")
    if not isinstance(obj["factual_assessments"], list):
        raise ValueError("judge_output_factual_assessments_not_array")
    return obj


def execute_requests(
    request_path: Path,
    responses_out: Path,
    api_root: str,
    timeout: int,
    insecure: bool,
    limit: int | None,
    sleep_s: float,
    retries: int,
) -> None:
    api_root = api_root.rstrip("/")
    completed = 0
    with responses_out.open("w", encoding="utf-8") as out_f:
        for line_no, req_record in load_jsonl(request_path):
            if limit is not None and completed >= limit:
                break
            custom_id = req_record.get("custom_id", f"line-{line_no}")
            url_path = req_record.get("url", "/v1/chat/completions")
            if not url_path.startswith("/"):
                url_path = "/" + url_path
            url = api_root + url_path
            body = req_record.get("body") or {}

            attempt_records = []
            final_record: dict[str, Any] | None = None

            for attempt in range(retries + 1):
                started = time.time()
                status_code, payload = post_json(url, body, timeout=timeout, insecure=insecure)
                elapsed_ms = int((time.time() - started) * 1000)
                attempt_record = {
                    "attempt": attempt,
                    "status_code": status_code,
                    "elapsed_ms": elapsed_ms,
                    "raw_response": payload,
                }
                try:
                    if not isinstance(payload, dict):
                        raise ValueError("http_response_not_json_object")
                    judge_obj = strict_extract_content(payload)
                    attempt_record["parse_status"] = "ok"
                    attempt_record["judge_output"] = judge_obj
                    final_record = {
                        "custom_id": custom_id,
                        "ok": True,
                        "line_no": line_no,
                        "attempts": attempt_records + [attempt_record],
                        "metadata": req_record.get("metadata", {}),
                        "judge_output": judge_obj,
                    }
                    break
                except Exception as e:
                    attempt_record["parse_status"] = "failed"
                    attempt_record["error"] = str(e)
                    attempt_records.append(attempt_record)
                    if attempt < retries:
                        time.sleep(min(2 ** attempt, 10))

            if final_record is None:
                final_record = {
                    "custom_id": custom_id,
                    "ok": False,
                    "line_no": line_no,
                    "attempts": attempt_records,
                    "metadata": req_record.get("metadata", {}),
                }

            out_f.write(json.dumps(final_record, ensure_ascii=False, separators=(",", ":")) + "\n")
            out_f.flush()
            completed += 1
            print(f"[{completed}] {custom_id}: {'OK' if final_record['ok'] else 'FAIL'}", file=sys.stderr)
            if sleep_s:
                time.sleep(sleep_s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="judge_requests.jsonl", help="Input judge_requests JSONL.")
    ap.add_argument("--output", default="judge_requests.selene_litellm.jsonl", help="Patched output JSONL.")
    ap.add_argument("--judge-model", default="local-judge-selene", help="LiteLLM-facing judge model name.")
    ap.add_argument("--schema-name", default="hm_judge_output_v050", help="response_format json_schema name.")
    ap.add_argument("--max-tokens", type=int, default=None, help="Override body.max_tokens; default preserves input.")
    ap.add_argument("--execute", action="store_true", help="Also send patched requests to the API.")
    ap.add_argument("--api-root", default="https://127.0.0.1:8005", help="API root without /v1.")
    ap.add_argument("--insecure", action="store_true", help="Disable TLS verification for local self-signed LiteLLM.")
    ap.add_argument("--responses-out", default="judge_outputs.selene_litellm.jsonl", help="Execution output JSONL.")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--retries", type=int, default=1)
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)

    patched = []
    for line_no, r in load_jsonl(in_path):
        try:
            patched.append(patch_record(r, args.judge_model, args.schema_name, args.max_tokens))
        except Exception as e:
            print(f"{in_path}:{line_no}: patch failed: {e}", file=sys.stderr)
            return 2

    n = write_jsonl(patched, out_path)
    summary = validate_patched_file(out_path, args.judge_model)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["bad"]:
        print("Patched file validation failed.", file=sys.stderr)
        return 3

    print(f"Patched {n} requests -> {out_path}", file=sys.stderr)

    if args.execute:
        execute_requests(
            request_path=out_path,
            responses_out=Path(args.responses_out),
            api_root=args.api_root,
            timeout=args.timeout,
            insecure=args.insecure,
            limit=args.limit,
            sleep_s=args.sleep,
            retries=args.retries,
        )
        print(f"Wrote judge responses -> {args.responses_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
