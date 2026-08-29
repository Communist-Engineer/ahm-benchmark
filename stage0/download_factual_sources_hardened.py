#!/usr/bin/env python3
"""
Hardened Stage-0 factual-source downloader, v2.

This is a fail-fast replacement for download_factual_sources.py. It reads a
factual_targets.stage0.source_backed.json-style file, downloads each unique
sources[].source_id, archives bytes under --source-dir, writes source_sha256 and
archived_path back into every packet source entry, and recomputes packet_sha256
from canonical JSON with packet_sha256 excluded.

Design goals:
- no judge/API calls;
- no indefinite network waits;
- hard subprocess timeout around curl;
- destination-local temp files to avoid cross-device rename failures;
- curl forced to HTTP/1.1 to avoid HTTP/2 stream resets from some endpoints;
- skip already archived files unless --force is supplied;
- continue after per-source failures when --continue-on-error is supplied;
- preserve a manifest of successes and failures.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_USER_AGENT = (
    "Automated-Historical-Materialist-Benchmark/0.5.0 "
    "source-archiver (+local reproducibility script)"
)

DEFAULT_SOURCE_URLS: dict[str, str] = {
    "BLS_CPS_ANNUAL_AVERAGES_TABLE_1_2024": "https://www.bls.gov/cps/cpsa2024.pdf",
    "FRED_DPCERE1Q156NBEA": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DPCERE1Q156NBEA",
    "FRED_W270RE1A156NBEA": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=W270RE1A156NBEA",
    "FRED_A4002E1A156NBEA": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=A4002E1A156NBEA",
    "STANFORD_AI_INDEX_2025": "https://hai-production.s3.amazonaws.com/files/hai_ai_index_report_2025.pdf",
    "SYNERGY_RESEARCH_2024_CLOUD": "https://www.srgresearch.com/articles/cloud-market-jumped-to-330-billion-in-2024-genai-is-now-driving-half-of-the-growth",
    "BIS_DEC_2024_SEMICONDUCTOR_CONTROLS": "https://www.bis.gov/press-release/commerce-strengthens-export-controls-restrict-chinas-capability-produce-advanced-semiconductors-military",
    "TRENDFORCE_Q3_2024_FOUNDRY": "https://www.trendforce.com/presscenter/news/20241205-12398.html",
    "MICROSOFT_AZURE_REGIONS": "https://learn.microsoft.com/en-us/azure/reliability/regions-list",
    "GOOGLE_DATA_CENTER_LOCATIONS": "https://datacenters.google/locations",
    "OECD_2024_ALGORITHMIC_MANAGEMENT_SURVEY": "https://www.oecd.org/content/dam/oecd/en/publications/reports/2025/02/algorithmic-management-in-the-workplace_3c84ed6d/287c13c4-en.pdf",
    "ILO_ALGORITHMIC_MANAGEMENT": "https://www.ilo.org/algorithmic-management-workplace",
    "EU_JRC_ALGORITHMIC_MANAGEMENT": "https://publications.jrc.ec.europa.eu/repository/handle/JRC143072",
    "US_CENSUS_ACS_2024_5YR_DP04": "https://api.census.gov/data/2024/acs/acs5/profile?get=NAME,DP04_0001E,DP04_0002E,DP04_0003E&for=us:1",
    "HUD_AHAR_2024_PART_1": "https://www.huduser.gov/portal/sites/default/files/pdf/2024-AHAR-Part-1.pdf",
    "WORLD_BANK_FDI_NET_INFLOWS_GDP_GHA": "https://api.worldbank.org/v2/country/GHA/indicator/BX.KLT.DINV.WD.GD.ZS?format=json&per_page=20000",
    "WORLD_BANK_CURRENT_ACCOUNT_GDP_GHA": "https://api.worldbank.org/v2/country/GHA/indicator/BN.CAB.XOKA.GD.ZS?format=json&per_page=20000",
}

KNOWN_WARNINGS: dict[str, str] = {
    "FRED_W270RE1A156NBEA": (
        "Packet consistency check: W270RE1A156NBEA is the wage-and-salary accruals "
        "subseries. The 51.9 value used in the earlier source-backed packet corresponds "
        "to A4002E1A156NBEA, compensation of employees paid as a share of GDI. If the packet "
        "intends the 51.9 value, change the source_id to FRED_A4002E1A156NBEA or supply a "
        "--url-map override and update source excerpts accordingly."
    )
}

CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/json": ".json",
    "application/csv": ".csv",
    "text/csv": ".csv",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/plain": ".txt",
}

URL_HINT_EXTENSIONS: dict[str, str] = {
    "fredgraph.csv": ".csv",
    "api.worldbank.org": ".json",
    "api.census.gov": ".json",
}

SOURCE_URL_KEYS = ("url", "source_url", "download_url", "uri", "archive_url")
SOURCE_PATH_KEYS = ("archived_path", "local_path", "path")


@dataclass(frozen=True)
class ResolvedSource:
    source_id: str
    url: str
    target_path: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_packet_hash(packet: dict[str, Any]) -> str:
    material = copy.deepcopy(packet)
    material.pop("packet_sha256", None)
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def sanitize_source_id(source_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", source_id).strip("._-")
    if not safe:
        raise ValueError(f"cannot sanitize source_id {source_id!r}")
    return safe


def load_url_map(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError("--url-map must be a JSON object")
    return data


def url_from_source(source: dict[str, Any], url_map: dict[str, Any]) -> str | None:
    for key in SOURCE_URL_KEYS:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    source_id = source.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError(f"source object is missing source_id: {source}")

    mapped = url_map.get(source_id)
    if isinstance(mapped, str) and mapped.strip():
        return mapped.strip()
    if isinstance(mapped, dict):
        for key in SOURCE_URL_KEYS:
            value = mapped.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return DEFAULT_SOURCE_URLS.get(source_id)


def explicit_archive_path(source: dict[str, Any], url_map: dict[str, Any]) -> str | None:
    for key in SOURCE_PATH_KEYS:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    source_id = source.get("source_id")
    mapped = url_map.get(source_id) if isinstance(source_id, str) else None
    if isinstance(mapped, dict):
        for key in SOURCE_PATH_KEYS:
            value = mapped.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def extension_from_url(url: str) -> str | None:
    lower_url = url.lower()
    for marker, ext in URL_HINT_EXTENSIONS.items():
        if marker in lower_url:
            return ext
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in {".pdf", ".csv", ".json", ".html", ".htm", ".txt", ".xml"}:
        return ".html" if suffix == ".htm" else suffix
    return None


def extension_from_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in CONTENT_TYPE_EXTENSIONS:
        return CONTENT_TYPE_EXTENSIONS[media_type]
    guessed = mimetypes.guess_extension(media_type)
    if guessed:
        return ".html" if guessed == ".htm" else guessed
    return None


def target_path_for(source: dict[str, Any], url: str, source_dir: Path, url_map: dict[str, Any]) -> Path:
    source_id = source.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError(f"source object is missing source_id: {source}")

    explicit = explicit_archive_path(source, url_map)
    if explicit:
        p = Path(explicit)
        return p if p.is_absolute() else source_dir / p

    ext = extension_from_url(url) or ".bin"
    return source_dir / f"{sanitize_source_id(source_id)}{ext}"


def all_sources(packet_data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for target_id, packet in packet_data.items():
        if not isinstance(packet, dict):
            raise ValueError(f"packet {target_id!r} must be an object")
        sources = packet.get("sources")
        if not isinstance(sources, list):
            raise ValueError(f"packet {target_id!r} must contain sources array")
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError(f"packet {target_id!r} has non-object source: {source!r}")
            out.append(source)
    return out


def resolve_sources(packet_data: dict[str, Any], source_dir: Path, url_map: dict[str, Any], only: set[str] | None) -> dict[str, ResolvedSource]:
    resolved: dict[str, ResolvedSource] = {}
    for source in all_sources(packet_data):
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError(f"source object is missing source_id: {source}")
        if only is not None and source_id not in only:
            continue
        url = url_from_source(source, url_map)
        if not url:
            raise ValueError(f"no URL found for source_id={source_id!r}; add source.url or --url-map")
        path = target_path_for(source, url, source_dir, url_map)
        prior = resolved.get(source_id)
        if prior:
            if prior.url != url or prior.target_path != path:
                raise ValueError(f"source_id {source_id!r} resolves inconsistently")
            continue
        resolved[source_id] = ResolvedSource(source_id=source_id, url=url, target_path=path)
    return resolved


def parse_only(values: list[str]) -> set[str] | None:
    if not values:
        return None
    out: set[str] = set()
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                out.add(part)
    return out or None


def rel_or_abs(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def read_headers(header_file: Path) -> dict[str, str]:
    if not header_file.exists():
        return {}
    # curl -L writes one header block per redirect. Keep the last nonempty block.
    raw = header_file.read_text("iso-8859-1", errors="replace")
    blocks = [b for b in re.split(r"\r?\n\r?\n", raw) if b.strip()]
    if not blocks:
        return {}
    headers: dict[str, str] = {}
    for line in blocks[-1].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers


def maybe_adjust_extension(path: Path, content_type: str | None, explicit_path_was_used: bool) -> Path:
    if explicit_path_was_used:
        return path
    if path.suffix.lower() not in {"", ".bin"}:
        return path
    ext = extension_from_content_type(content_type)
    return path.with_suffix(ext) if ext else path


def download_with_curl(
    url: str,
    destination: Path,
    *,
    connect_timeout: float,
    max_time: float,
    retries: int,
    user_agent: str,
) -> tuple[Path, str | None, str | None]:
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is not installed; install curl or use --backend python")

    # Important: keep temporary body and header files in the destination directory.
    # WSL/Linux setups often mount /tmp and the project directory on different
    # filesystems, so os.replace() from /tmp to the archive directory can raise
    # EXDEV: Invalid cross-device link.
    destination.parent.mkdir(parents=True, exist_ok=True)
    body_tmp = destination
    header_tmp = destination.with_suffix(destination.suffix + ".headers.tmp")
    for tmp in (body_tmp, header_tmp):
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

    cmd = [
        curl,
        "--http1.1",
        "--location",
        "--fail",
        "--show-error",
        "--silent",
        "--compressed",
        "--connect-timeout", str(connect_timeout),
        "--max-time", str(max_time),
        "--retry", str(retries),
        "--retry-delay", "1",
        "--retry-all-errors",
        "--retry-max-time", str(max(max_time, max_time * max(1, retries))),
        "--user-agent", user_agent,
        "--header", "Accept: text/csv,application/json,application/pdf,text/html,*/*;q=0.8",
        "--dump-header", str(header_tmp),
        "--output", str(body_tmp),
        url,
    ]
    hard_timeout = max_time * (retries + 1) + connect_timeout + 10
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=hard_timeout)
    except subprocess.TimeoutExpired as exc:
        for tmp in (body_tmp, header_tmp):
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        raise RuntimeError(f"curl subprocess timed out after {hard_timeout:.1f}s for {url!r}") from exc

    if proc.returncode != 0:
        for tmp in (body_tmp, header_tmp):
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        raise RuntimeError(
            f"curl failed rc={proc.returncode} url={url!r} stderr={proc.stderr.strip()!r}"
        )
    if not body_tmp.exists() or body_tmp.stat().st_size == 0:
        for tmp in (body_tmp, header_tmp):
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        raise RuntimeError(f"download returned zero bytes for {url!r}")

    headers = read_headers(header_tmp)
    content_type = headers.get("content-type")
    final_url = headers.get("location")
    try:
        header_tmp.unlink()
    except FileNotFoundError:
        pass
    return destination, content_type, final_url


def download_with_python(url: str, destination: Path, *, timeout: float, user_agent: str) -> tuple[Path, str | None, str | None]:
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    req = urllib.request.Request(url, headers=headers, method="GET")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if not data:
            raise RuntimeError(f"download returned zero bytes for {url!r}")
        destination.write_bytes(data)
        return destination, resp.headers.get("Content-Type"), resp.geturl()


def archive_one(
    source: ResolvedSource,
    *,
    backend: str,
    connect_timeout: float,
    timeout: float,
    retries: int,
    user_agent: str,
    force: bool,
    dry_run: bool,
    explicit_path_was_used: bool,
) -> dict[str, Any]:
    planned_path = source.target_path
    if dry_run:
        return {
            "source_id": source.source_id,
            "url": source.url,
            "archived_path": str(planned_path),
            "status": "dry_run",
        }

    if planned_path.exists() and not force:
        return {
            "source_id": source.source_id,
            "url": source.url,
            "archived_path": str(planned_path),
            "status": "existing",
            "bytes": planned_path.stat().st_size,
            "source_sha256": sha256_file(planned_path),
            "retrieved_at_utc": None,
            "content_type": None,
            "final_url": None,
        }

    tmp_target = planned_path.with_suffix(planned_path.suffix + ".tmp")
    if tmp_target.exists():
        tmp_target.unlink()

    if backend == "curl":
        downloaded_tmp, content_type, final_url = download_with_curl(
            source.url,
            tmp_target,
            connect_timeout=connect_timeout,
            max_time=timeout,
            retries=retries,
            user_agent=user_agent,
        )
    else:
        downloaded_tmp, content_type, final_url = download_with_python(
            source.url,
            tmp_target,
            timeout=timeout,
            user_agent=user_agent,
        )

    final_path = maybe_adjust_extension(planned_path, content_type, explicit_path_was_used)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(downloaded_tmp, final_path)
    size = final_path.stat().st_size
    return {
        "source_id": source.source_id,
        "url": source.url,
        "archived_path": str(final_path),
        "status": "downloaded",
        "bytes": size,
        "source_sha256": sha256_file(final_path),
        "retrieved_at_utc": utc_now(),
        "content_type": content_type,
        "final_url": final_url,
    }


def explicit_path_used_by_id(packet_data: dict[str, Any], url_map: dict[str, Any]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for source in all_sources(packet_data):
        source_id = source.get("source_id")
        if isinstance(source_id, str):
            out[source_id] = explicit_archive_path(source, url_map) is not None
    return out


def annotate_packets(
    packet_data: dict[str, Any],
    results: dict[str, dict[str, Any]],
    source_dir: Path,
    *,
    recompute_packet_hashes: bool,
    preserve_source_retrieved_at: bool,
) -> dict[str, Any]:
    out = copy.deepcopy(packet_data)
    for packet in out.values():
        if not isinstance(packet, dict):
            continue
        for source in packet.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_id = source.get("source_id")
            if not isinstance(source_id, str) or source_id not in results:
                continue
            result = results[source_id]
            if result.get("status") == "error":
                source["download_error"] = result.get("error")
                continue
            if result.get("status") == "dry_run":
                source["planned_url"] = result.get("url")
                source["planned_archived_path"] = rel_or_abs(Path(result["archived_path"]), source_dir)
                continue
            source["url"] = result["url"]
            source["archived_path"] = rel_or_abs(Path(result["archived_path"]), source_dir)
            source["source_sha256"] = result["source_sha256"]
            source["bytes"] = result["bytes"]
            if result.get("content_type"):
                source["content_type"] = result["content_type"]
            if result.get("final_url") and result.get("final_url") != result.get("url"):
                source["final_url"] = result["final_url"]
            if not preserve_source_retrieved_at and result.get("retrieved_at_utc"):
                source["retrieved_at"] = result["retrieved_at_utc"]
        if recompute_packet_hashes:
            packet["packet_sha256"] = canonical_packet_hash(packet)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardened downloader for Stage-0 factual packet sources.")
    parser.add_argument("--input", required=True, type=Path, help="Input factual_targets.stage0.source_backed.json")
    parser.add_argument("--output", type=Path, help="Updated packet JSON with source archive metadata")
    parser.add_argument("--source-dir", required=True, type=Path, help="Directory where source files are archived")
    parser.add_argument("--manifest", type=Path, help="Optional JSON manifest")
    parser.add_argument("--url-map", type=Path, help="Optional source_id URL/path override JSON")
    parser.add_argument("--only-source-id", action="append", default=[], help="Limit to source_id; comma-separated accepted")
    parser.add_argument("--force", action="store_true", help="Redownload existing files")
    parser.add_argument("--dry-run", action="store_true", help="Resolve only; no network access")
    parser.add_argument("--backend", choices=["curl", "python"], default="curl", help="Download backend; curl is hard-timeout protected")
    parser.add_argument("--connect-timeout", type=float, default=8.0, help="curl connect timeout seconds")
    parser.add_argument("--timeout", type=float, default=30.0, help="curl max-time or Python socket timeout seconds per source")
    parser.add_argument("--retries", type=int, default=1, help="curl retry count")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent")
    parser.add_argument("--continue-on-error", action="store_true", help="Record failed sources and continue")
    parser.add_argument("--no-packet-hash", action="store_true", help="Do not recompute packet_sha256")
    parser.add_argument("--preserve-source-retrieved-at", action="store_true", help="Keep existing sources[].retrieved_at")
    parser.add_argument("--fail-on-known-warning", action="store_true", help="Exit nonzero on known packet/source warnings")
    args = parser.parse_args()

    if args.retries < 0:
        raise ValueError("--retries must be >= 0")
    if args.timeout <= 0 or args.connect_timeout <= 0:
        raise ValueError("--timeout and --connect-timeout must be > 0")

    packet_data = load_json(args.input)
    if not isinstance(packet_data, dict):
        raise ValueError("input packet file must be a JSON object keyed by factual_target_id")

    url_map = load_url_map(args.url_map)
    only = parse_only(args.only_source_id)
    resolved = resolve_sources(packet_data, args.source_dir, url_map, only)
    explicit_used = explicit_path_used_by_id(packet_data, url_map)

    warning_count = 0
    for source_id in sorted(resolved):
        warning = KNOWN_WARNINGS.get(source_id)
        if warning:
            warning_count += 1
            print(f"WARNING: {source_id}: {warning}", file=sys.stderr)
    if args.fail_on_known_warning and warning_count:
        raise ValueError(f"aborting because {warning_count} known warning(s) were found")

    results: dict[str, dict[str, Any]] = {}
    failure_count = 0
    start = time.monotonic()
    for source_id, source in sorted(resolved.items()):
        print(f"ARCHIVE {source_id}: {source.url} -> {source.target_path}", file=sys.stderr, flush=True)
        try:
            result = archive_one(
                source,
                backend=args.backend,
                connect_timeout=args.connect_timeout,
                timeout=args.timeout,
                retries=args.retries,
                user_agent=args.user_agent,
                force=args.force,
                dry_run=args.dry_run,
                explicit_path_was_used=explicit_used.get(source_id, False),
            )
        except Exception as exc:
            failure_count += 1
            result = {
                "source_id": source_id,
                "url": source.url,
                "archived_path": str(source.target_path),
                "status": "error",
                "error": str(exc),
                "retrieved_at_utc": utc_now(),
            }
            print(f"  ERROR {source_id}: {exc}", file=sys.stderr, flush=True)
            results[source_id] = result
            if not args.continue_on_error:
                break
        else:
            results[source_id] = result
            print(
                f"  {result.get('status')} bytes={result.get('bytes', 'n/a')} sha256={result.get('source_sha256', 'n/a')}",
                file=sys.stderr,
                flush=True,
            )

    updated = annotate_packets(
        packet_data,
        results,
        args.source_dir,
        recompute_packet_hashes=not args.no_packet_hash,
        preserve_source_retrieved_at=args.preserve_source_retrieved_at,
    )

    manifest = {
        "created_at_utc": utc_now(),
        "input": str(args.input),
        "source_dir": str(args.source_dir),
        "backend": args.backend,
        "dry_run": args.dry_run,
        "force": args.force,
        "connect_timeout": args.connect_timeout,
        "timeout": args.timeout,
        "retries": args.retries,
        "source_count": len(resolved),
        "completed_count": len(results) - failure_count,
        "failure_count": failure_count,
        "known_warning_count": warning_count,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "sources": results,
    }
    if args.manifest:
        write_json(args.manifest, manifest)
    if args.output:
        write_json(args.output, updated)
    elif not args.dry_run:
        print("No --output supplied; packet JSON was not written.", file=sys.stderr)

    print(
        f"DONE: resolved={len(resolved)} completed={len(results)-failure_count} failed={failure_count} "
        f"warnings={warning_count} dry_run={args.dry_run}",
        file=sys.stderr,
    )
    return 1 if failure_count else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired as exc:
        print(f"ERROR: hard subprocess timeout expired: {exc}", file=sys.stderr)
        raise SystemExit(124)
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
