#!/usr/bin/env python3
"""
Run Stage-0 judge requests against an OpenAI-compatible /v1/chat/completions endpoint.

Designed for the Automated Historical-Materialist Benchmark Stage-0 JSONL request shape:
  {"custom_id", "method", "url", "body", "metadata"}

The runner writes one JSON object per completed request, preserving the smoke-output style:
  {"custom_id", "ok", "line_no", "attempts", "metadata", "judge_output"?}

It does not call any non-judge service and does not repair malformed judge outputs.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

DEFAULT_BASE_URL = os.environ.get("AHM_JUDGE_BASE_URL", "http://127.0.0.1:8006/v1")
DEFAULT_ENDPOINT = "/v1/chat/completions"
DEFAULT_MAX_CONCURRENCY = int(os.environ.get("AHM_JUDGE_MAX_CONCURRENCY", "4"))
FORBIDDEN_FACTUAL_TARGET_ID = "none"
REQUIRED_TOP_LEVEL = {
    "schema_version",
    "rubric_version",
    "parse_status",
    "claims",
    "relations",
    "factual_assessments",
    "semantic_response_assessment",
}


@dataclasses.dataclass(frozen=True)
class RunnerConfig:
    base_url: str
    timeout: float
    connect_timeout: float
    retries: int
    retry_sleep: float
    model_override: str | None
    api_key: str | None
    api_key_env: str
    extra_header: tuple[str, ...]
    enforce_factual_targets: bool
    require_schema_ok: bool
    dry_run: bool
    curl_binary: str


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def load_completed_ids(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"WARNING: ignoring unparsable existing output line {line_no} in {path}", file=sys.stderr)
                continue
            custom_id = obj.get("custom_id")
            if isinstance(custom_id, str):
                done.add(custom_id)
    return done


def write_jsonl_row(handle: Any, row: dict[str, Any], lock: threading.Lock) -> None:
    text = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with lock:
        handle.write(text + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def active_payload(record: dict[str, Any]) -> dict[str, Any]:
    body = record.get("body")
    if isinstance(body, dict):
        return body
    return record


def request_metadata(record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(record.get("metadata"), dict):
        return record["metadata"]
    if isinstance(payload.get("metadata"), dict):
        return payload["metadata"]
    return {}


def resolve_url(base_url: str, request_url: str | None) -> str:
    if request_url and request_url.startswith(("http://", "https://")):
        return request_url

    req = request_url or DEFAULT_ENDPOINT
    if not req.startswith("/"):
        req = "/" + req

    base = base_url.rstrip("/")
    split = urlsplit(base)
    if not split.scheme or not split.netloc:
        raise ValueError(f"base URL must be absolute: {base_url!r}")

    base_path = split.path.rstrip("/")
    if base_path.endswith("/v1") and req.startswith("/v1/"):
        path = base_path + req[len("/v1"):]
    else:
        path = base_path + req

    return urlunsplit((split.scheme, split.netloc, path, "", ""))


def prepare_body(record: dict[str, Any], cfg: RunnerConfig) -> dict[str, Any]:
    payload = json.loads(json.dumps(active_payload(record), ensure_ascii=False))
    if cfg.model_override:
        payload["model"] = cfg.model_override
    return payload


def headers(cfg: RunnerConfig) -> list[str]:
    out = ["Content-Type: application/json"]
    key = cfg.api_key or os.environ.get(cfg.api_key_env)
    if key:
        out.append(f"Authorization: Bearer {key}")
    for h in cfg.extra_header:
        out.append(h)
    return out


def run_curl(url: str, body: dict[str, Any], cfg: RunnerConfig, temp_dir: Path) -> tuple[int, float, str, str, str]:
    """Return (status_code, elapsed_ms, stdout_body, stderr, final_url)."""
    body_path = temp_dir / f"request-{time.time_ns()}.json"
    response_path = temp_dir / f"response-{time.time_ns()}.json"
    body_path.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    cmd = [
        cfg.curl_binary,
        "--silent",
        "--show-error",
        "--location",
        "--http1.1",
        "--connect-timeout", str(cfg.connect_timeout),
        "--max-time", str(cfg.timeout),
        "--request", "POST",
        url,
        "--output", str(response_path),
        "--write-out", "%{http_code}\n%{time_total}\n%{url_effective}",
    ]
    for h in headers(cfg):
        cmd.extend(["--header", h])
    cmd.extend(["--data-binary", f"@{body_path}"])

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=cfg.timeout + 15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        raise RuntimeError(f"curl subprocess timeout after {elapsed_ms:.0f} ms: {exc}") from exc
    finally:
        try:
            body_path.unlink(missing_ok=True)
        except Exception:
            pass

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    response_text = response_path.read_text(encoding="utf-8", errors="replace") if response_path.exists() else ""
    try:
        response_path.unlink(missing_ok=True)
    except Exception:
        pass

    status_code = 0
    curl_time = ""
    final_url = url
    parts = proc.stdout.splitlines()
    if parts:
        try:
            status_code = int(parts[0].strip() or "0")
        except ValueError:
            status_code = 0
    if len(parts) >= 2:
        curl_time = parts[1].strip()
    if len(parts) >= 3:
        final_url = parts[2].strip() or url

    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        raise RuntimeError(
            f"curl failed rc={proc.returncode} status={status_code} time={curl_time} url={url!r} stderr={stderr!r}"
        )
    return status_code, elapsed_ms, response_text, stderr, final_url


def strip_code_fence(text: str) -> str:
    s = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return s


def extract_first_json_object(text: str) -> str:
    s = strip_code_fence(text)
    if s.startswith("{") and s.endswith("}"):
        return s
    start = s.find("{")
    if start < 0:
        raise ValueError("no JSON object start found in model content")
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(s[start:], start=start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
    raise ValueError("no complete JSON object found in model content")


def chat_message_content(raw_response: dict[str, Any]) -> str:
    choices = raw_response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("chat response missing choices[0]")
    choice0 = choices[0]
    if not isinstance(choice0, dict):
        raise ValueError("chat response choices[0] is not an object")
    message = choice0.get("message")
    if not isinstance(message, dict):
        raise ValueError("chat response choices[0].message is not an object")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        if parts:
            return "".join(parts)
    raise ValueError("chat response message content is not a string")


def parse_judge_output(raw_response_text: str, require_schema_ok: bool) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Return (parse_status, raw_response_json, judge_output, error)."""
    try:
        raw = json.loads(raw_response_text)
    except json.JSONDecodeError as exc:
        return "raw_response_json_failed", None, None, str(exc)

    try:
        content = chat_message_content(raw)
        judge_json_text = extract_first_json_object(content)
        judge_output = json.loads(judge_json_text)
    except Exception as exc:
        return "judge_json_failed", raw, None, str(exc)

    if require_schema_ok:
        missing = REQUIRED_TOP_LEVEL - set(judge_output)
        if missing:
            return "judge_schema_failed", raw, judge_output, f"missing top-level keys: {sorted(missing)}"
        if judge_output.get("schema_version") != "judge_output_v0.5.0":
            return "judge_schema_failed", raw, judge_output, "schema_version mismatch"
        if judge_output.get("rubric_version") != "hm_v0.5.0":
            return "judge_schema_failed", raw, judge_output, "rubric_version mismatch"
        if not isinstance(judge_output.get("claims"), list):
            return "judge_schema_failed", raw, judge_output, "claims must be array"
        if not isinstance(judge_output.get("relations"), list):
            return "judge_schema_failed", raw, judge_output, "relations must be array"
        if not isinstance(judge_output.get("factual_assessments"), list):
            return "judge_schema_failed", raw, judge_output, "factual_assessments must be array"
        if not isinstance(judge_output.get("semantic_response_assessment"), dict):
            return "judge_schema_failed", raw, judge_output, "semantic_response_assessment must be object"

    return "ok", raw, judge_output, None


def prompt_texts(body: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    if isinstance(body.get("input"), str):
        texts.append(body["input"])
    messages = body.get("messages")
    if isinstance(messages, list):
        for m in messages:
            if isinstance(m, dict) and isinstance(m.get("content"), str):
                texts.append(m["content"])
    return texts


def extract_supplied_factual_targets(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    pattern = re.compile(r"(?ms)^FACTUAL_TARGETS\s*\n(.*?)^\s*RESPONSE_METADATA\s*$")
    for text in prompt_texts(body):
        if "FACTUAL_TARGETS" not in text:
            continue
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group(1).strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("FACTUAL_TARGETS block is not an array")
        return parsed
    return None


def validate_factual_semantics(judge_output: dict[str, Any], supplied_packets: list[dict[str, Any]] | None) -> None:
    if supplied_packets is None:
        return
    facts = judge_output.get("factual_assessments")
    if not isinstance(facts, list):
        raise ValueError("judge_output.factual_assessments must be array")
    allowed_ids = {p.get("factual_target_id") for p in supplied_packets if isinstance(p, dict)}
    allowed_versions = {p.get("factual_target_version_id") for p in supplied_packets if isinstance(p, dict)}
    packet_status_by_id = {p.get("factual_target_id"): p.get("packet_status") for p in supplied_packets if isinstance(p, dict)}

    if not supplied_packets:
        if facts != []:
            raise ValueError("FACTUAL_TARGETS is empty but judge_output.factual_assessments is not []")
        return

    if len(facts) != len(supplied_packets):
        raise ValueError(f"expected {len(supplied_packets)} factual_assessments, got {len(facts)}")

    seen: set[tuple[str, str]] = set()
    for fact in facts:
        if not isinstance(fact, dict):
            raise ValueError("factual assessment is not an object")
        target_id = fact.get("factual_target_id")
        version_id = fact.get("factual_target_version_id")
        if target_id == FORBIDDEN_FACTUAL_TARGET_ID:
            raise ValueError("judge emitted forbidden placeholder factual_target_id")
        if version_id is None:
            raise ValueError("judge emitted null factual_target_version_id")
        if target_id not in allowed_ids:
            raise ValueError(f"judge emitted unsupplied factual_target_id {target_id!r}")
        if version_id not in allowed_versions:
            raise ValueError(f"judge emitted unsupplied factual_target_version_id {version_id!r}")
        pair = (str(target_id), str(version_id))
        if pair in seen:
            raise ValueError(f"duplicate factual assessment for {pair}")
        seen.add(pair)
        if packet_status_by_id.get(target_id) != "complete" and fact.get("status") != "not_assessable":
            raise ValueError("non-complete factual packet produced assessable factual status")


def execute_one(line_no: int, record: dict[str, Any], cfg: RunnerConfig, temp_dir: Path) -> dict[str, Any]:
    custom_id = record.get("custom_id") or f"line-{line_no}"
    body = prepare_body(record, cfg)
    metadata = request_metadata(record, body)
    url = resolve_url(cfg.base_url, record.get("url") if isinstance(record.get("url"), str) else None)
    supplied_packets = extract_supplied_factual_targets(body)

    result: dict[str, Any] = {
        "custom_id": custom_id,
        "ok": False,
        "line_no": line_no,
        "metadata": metadata,
        "request_url": url,
        "request_body_sha256": sha256_text(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        "started_at_utc": now_utc(),
        "attempts": [],
    }

    if cfg.dry_run:
        result["ok"] = True
        result["dry_run"] = True
        result["completed_at_utc"] = now_utc()
        return result

    for attempt in range(cfg.retries + 1):
        attempt_row: dict[str, Any] = {"attempt": attempt, "started_at_utc": now_utc()}
        try:
            status_code, elapsed_ms, response_text, stderr, final_url = run_curl(url, body, cfg, temp_dir)
            attempt_row.update({
                "status_code": status_code,
                "elapsed_ms": int(round(elapsed_ms)),
                "stderr": stderr,
                "final_url": final_url,
                "raw_response_text_sha256": sha256_text(response_text),
            })
            parse_status, raw_json, judge_output, parse_error = parse_judge_output(response_text, cfg.require_schema_ok)
            attempt_row["parse_status"] = parse_status
            if raw_json is not None:
                attempt_row["raw_response"] = raw_json
            else:
                attempt_row["raw_response_text_excerpt"] = response_text[:2000]
            if judge_output is not None:
                attempt_row["judge_output"] = judge_output
            if parse_error:
                attempt_row["parse_error"] = parse_error

            if status_code < 200 or status_code >= 300:
                attempt_row["error"] = f"HTTP status {status_code}"
            elif parse_status != "ok":
                attempt_row["error"] = f"parse/schema status {parse_status}"
            else:
                if cfg.enforce_factual_targets:
                    validate_factual_semantics(judge_output or {}, supplied_packets)
                result["ok"] = True
                result["judge_output"] = judge_output
                result["completed_at_utc"] = now_utc()
                result["attempts"].append(attempt_row)
                return result
        except Exception as exc:
            attempt_row.update({
                "status_code": 0,
                "elapsed_ms": None,
                "parse_status": "request_failed",
                "error": str(exc),
            })

        result["attempts"].append(attempt_row)
        if attempt < cfg.retries:
            time.sleep(cfg.retry_sleep * (attempt + 1))

    result["completed_at_utc"] = now_utc()
    result["error"] = result["attempts"][-1].get("error", "unknown error") if result["attempts"] else "no attempts"
    return result


def select_rows(
    rows: list[tuple[int, dict[str, Any]]],
    only_custom_id: set[str] | None,
    only_family: set[str] | None,
    limit: int | None,
    start_line: int | None,
    completed: set[str],
    resume: bool,
) -> list[tuple[int, dict[str, Any]]]:
    selected: list[tuple[int, dict[str, Any]]] = []
    for line_no, record in rows:
        if start_line is not None and line_no < start_line:
            continue
        custom_id = record.get("custom_id") or f"line-{line_no}"
        if resume and custom_id in completed:
            continue
        if only_custom_id and custom_id not in only_custom_id:
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        fam = metadata.get("item_family_id")
        if only_family and fam not in only_family:
            continue
        selected.append((line_no, record))
        if limit is not None and len(selected) >= limit:
            break
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Stage-0 judge JSONL requests against a local OpenAI-compatible judge endpoint.")
    parser.add_argument("--input", required=True, type=Path, help="Input judge request JSONL")
    parser.add_argument("--output", required=True, type=Path, help="Output judge result JSONL")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Judge base URL, default {DEFAULT_BASE_URL}")
    parser.add_argument("--model", default=None, help="Optional model override; omitted preserves body.model")
    parser.add_argument("--timeout", type=float, default=1800.0, help="Hard curl max-time per attempt in seconds")
    parser.add_argument("--connect-timeout", type=float, default=10.0, help="Curl connect timeout in seconds")
    parser.add_argument("--retries", type=int, default=0, help="Retry count after first attempt")
    parser.add_argument("--retry-sleep", type=float, default=5.0, help="Base sleep between retries")
    parser.add_argument("--workers", type=int, default=1, help="Parallel requests; use 1 for initial judge testing")
    parser.add_argument("--max-concurrency", type=int, default=DEFAULT_MAX_CONCURRENCY,
                        help="Deployment concurrency ceiling; default comes from AHM_JUDGE_MAX_CONCURRENCY (4)")
    parser.add_argument("--limit", type=int, default=None, help="Run at most N selected requests")
    parser.add_argument("--start-line", type=int, default=None, help="Start at this JSONL line number")
    parser.add_argument("--only-family", action="append", default=None, help="Restrict to item_family_id; may repeat")
    parser.add_argument("--only-custom-id", action="append", default=None, help="Restrict to custom_id; may repeat")
    parser.add_argument("--resume", action="store_true", help="Skip custom_ids already present in output")
    parser.add_argument("--force", action="store_true", help="Allow appending duplicate custom_ids to an existing output")
    parser.add_argument("--api-key", default=None, help="Optional bearer token")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Env var for bearer token if set")
    parser.add_argument("--header", action="append", default=[], help="Extra HTTP header, e.g. 'X-Foo: bar'")
    parser.add_argument("--no-enforce-factual-targets", action="store_true", help="Disable semantic checks for factual_assessments vs supplied FACTUAL_TARGETS")
    parser.add_argument("--no-require-schema-ok", action="store_true", help="Do not require top-level judge schema keys for ok=true")
    parser.add_argument("--dry-run", action="store_true", help="Select and hash requests without calling the endpoint")
    parser.add_argument("--curl", default="curl", help="curl binary path")
    args = parser.parse_args()

    if args.max_concurrency < 1:
        raise ValueError("--max-concurrency must be >= 1")
    if not 1 <= args.workers <= args.max_concurrency:
        raise ValueError("--workers must be between 1 and --max-concurrency")
    if args.retries < 0:
        raise ValueError("--retries must be >= 0")
    if not shutil.which(args.curl):
        raise ValueError(f"curl binary not found: {args.curl}")
    if args.output.exists() and not (args.resume or args.force):
        raise ValueError(f"output exists: {args.output}; pass --resume or --force")

    rows = read_jsonl(args.input)
    completed = load_completed_ids(args.output) if args.output.exists() else set()
    selected = select_rows(
        rows,
        only_custom_id=set(args.only_custom_id) if args.only_custom_id else None,
        only_family=set(args.only_family) if args.only_family else None,
        limit=args.limit,
        start_line=args.start_line,
        completed=completed,
        resume=args.resume,
    )

    cfg = RunnerConfig(
        base_url=args.base_url,
        timeout=args.timeout,
        connect_timeout=args.connect_timeout,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
        model_override=args.model,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        extra_header=tuple(args.header),
        enforce_factual_targets=not args.no_enforce_factual_targets,
        require_schema_ok=not args.no_require_schema_ok,
        dry_run=args.dry_run,
        curl_binary=args.curl,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = args.output.parent / ".judge_runner_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    print(
        f"RUNNER: input={args.input} selected={len(selected)} total={len(rows)} output={args.output} "
        f"base_url={args.base_url} workers={args.workers} timeout={args.timeout}s resume={args.resume} dry_run={args.dry_run}",
        flush=True,
    )

    ok_count = 0
    fail_count = 0
    started = time.perf_counter()
    mode = "a" if args.output.exists() else "w"
    with args.output.open(mode, encoding="utf-8") as out:
        if args.workers == 1:
            for idx, (line_no, record) in enumerate(selected, start=1):
                cid = record.get("custom_id") or f"line-{line_no}"
                fam = (record.get("metadata") or {}).get("item_family_id") if isinstance(record.get("metadata"), dict) else None
                print(f"[{idx}/{len(selected)}] START line={line_no} custom_id={cid} family={fam}", flush=True)
                row = execute_one(line_no, record, cfg, temp_dir)
                write_jsonl_row(out, row, lock)
                if row.get("ok"):
                    ok_count += 1
                    print(f"[{idx}/{len(selected)}] OK line={line_no} custom_id={cid}", flush=True)
                else:
                    fail_count += 1
                    print(f"[{idx}/{len(selected)}] FAIL line={line_no} custom_id={cid} error={row.get('error')}", flush=True)
        else:
            with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
                future_to_info = {
                    pool.submit(execute_one, line_no, record, cfg, temp_dir): (i, line_no, record)
                    for i, (line_no, record) in enumerate(selected, start=1)
                }
                for fut in cf.as_completed(future_to_info):
                    i, line_no, record = future_to_info[fut]
                    cid = record.get("custom_id") or f"line-{line_no}"
                    try:
                        row = fut.result()
                    except Exception as exc:
                        row = {
                            "custom_id": cid,
                            "ok": False,
                            "line_no": line_no,
                            "metadata": record.get("metadata", {}),
                            "attempts": [],
                            "error": str(exc),
                            "completed_at_utc": now_utc(),
                        }
                    write_jsonl_row(out, row, lock)
                    if row.get("ok"):
                        ok_count += 1
                        print(f"[{i}/{len(selected)}] OK line={line_no} custom_id={cid}", flush=True)
                    else:
                        fail_count += 1
                        print(f"[{i}/{len(selected)}] FAIL line={line_no} custom_id={cid} error={row.get('error')}", flush=True)

    elapsed = time.perf_counter() - started
    print(f"DONE: selected={len(selected)} ok={ok_count} failed={fail_count} elapsed_seconds={elapsed:.1f}", flush=True)
    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("INTERRUPTED", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
