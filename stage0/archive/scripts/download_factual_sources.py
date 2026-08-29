#!/usr/bin/env python3
"""
Download and archive source files for Stage-0 factual packets.

This script reads factual_targets.stage0.source_backed.json-style packets,
resolves each sources[].source_id to a URL, downloads each unique source once,
computes source byte hashes, writes source archive metadata back into the packet
JSON, and optionally recomputes packet_sha256 from canonical JSON with
packet_sha256 excluded.

It does not call any judge endpoint.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
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

# Built-in URL registry for the source_id values used in the generated Stage-0
# packet file. Override or extend with --url-map when the live repo has a more
# authoritative URL, API endpoint, DOI landing page, or local mirror.
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
    "text/csv": ".csv",
    "application/csv": ".csv",
    "application/json": ".json",
    "text/json": ".json",
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


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")


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


def source_url_from_entry(source: dict[str, Any], url_map: dict[str, Any]) -> str | None:
    for key in SOURCE_URL_KEYS:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    source_id = source.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError(f"source object is missing source_id: {source}")

    mapped = url_map.get(source_id)
    if isinstance(mapped, str):
        return mapped
    if isinstance(mapped, dict):
        for key in SOURCE_URL_KEYS:
            value = mapped.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return DEFAULT_SOURCE_URLS.get(source_id)


def source_archived_path_from_entry(source: dict[str, Any], url_map: dict[str, Any]) -> str | None:
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
    parsed = urllib.parse.urlparse(url)
    lower_url = url.lower()
    for marker, ext in URL_HINT_EXTENSIONS.items():
        if marker in lower_url:
            return ext
    suffix = Path(parsed.path).suffix.lower()
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


def derive_target_path(source: dict[str, Any], url: str, source_dir: Path, url_map: dict[str, Any]) -> Path:
    source_id = source.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError(f"source object is missing source_id: {source}")

    explicit_path = source_archived_path_from_entry(source, url_map)
    if explicit_path:
        p = Path(explicit_path)
        if p.is_absolute():
            return p
        return source_dir / p

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


def resolve_unique_sources(
    packet_data: dict[str, Any],
    source_dir: Path,
    url_map: dict[str, Any],
    only_source_ids: set[str] | None,
) -> dict[str, ResolvedSource]:
    resolved: dict[str, ResolvedSource] = {}
    for source in all_sources(packet_data):
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError(f"source object is missing source_id: {source}")
        if only_source_ids is not None and source_id not in only_source_ids:
            continue

        url = source_url_from_entry(source, url_map)
        if not url:
            raise ValueError(
                f"No URL found for source_id={source_id!r}. Add source.url/source_url/download_url "
                "to the packet or provide --url-map."
            )
        target_path = derive_target_path(source, url, source_dir, url_map)

        prior = resolved.get(source_id)
        if prior is not None:
            if prior.url != url:
                raise ValueError(f"source_id {source_id!r} resolves to conflicting URLs: {prior.url!r} vs {url!r}")
            if prior.target_path != target_path:
                raise ValueError(
                    f"source_id {source_id!r} resolves to conflicting archive paths: "
                    f"{prior.target_path!s} vs {target_path!s}"
                )
            continue

        resolved[source_id] = ResolvedSource(source_id=source_id, url=url, target_path=target_path)
    return resolved


def download_url(url: str, timeout: float, retries: int, user_agent: str, sleep_seconds: float) -> tuple[bytes, str | None, str | None]:
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type")
                final_url = resp.geturl()
                if not data:
                    raise ValueError("download returned zero bytes")
                return data, content_type, final_url
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(sleep_seconds * (2 ** attempt))
    raise RuntimeError(f"failed to download {url!r}: {last_error}")


def maybe_adjust_extension(path: Path, content_type: str | None, explicit_path_was_used: bool) -> Path:
    if explicit_path_was_used:
        return path
    current = path.suffix.lower()
    if current not in {".bin", ""}:
        return path
    ext = extension_from_content_type(content_type)
    if ext:
        return path.with_suffix(ext)
    return path


def archive_one(
    source: ResolvedSource,
    *,
    timeout: float,
    retries: int,
    user_agent: str,
    sleep_seconds: float,
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

    data, content_type, final_url = download_url(source.url, timeout, retries, user_agent, sleep_seconds)
    final_path = maybe_adjust_extension(planned_path, content_type, explicit_path_was_used)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    os.replace(tmp_path, final_path)
    return {
        "source_id": source.source_id,
        "url": source.url,
        "archived_path": str(final_path),
        "status": "downloaded",
        "bytes": len(data),
        "source_sha256": sha256_bytes(data),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "content_type": content_type,
        "final_url": final_url,
    }


def rel_or_abs(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def annotate_packets(
    packet_data: dict[str, Any],
    archive_results: dict[str, dict[str, Any]],
    source_dir: Path,
    *,
    recompute_packet_hashes: bool,
    preserve_source_retrieved_at: bool,
) -> dict[str, Any]:
    out = copy.deepcopy(packet_data)
    for packet in out.values():
        for source in packet.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_id = source.get("source_id")
            if not isinstance(source_id, str) or source_id not in archive_results:
                continue
            result = archive_results[source_id]
            if result.get("status") == "dry_run":
                source["planned_url"] = result["url"]
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


def collect_explicit_path_used(packet_data: dict[str, Any], url_map: dict[str, Any]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for source in all_sources(packet_data):
        source_id = source.get("source_id")
        if not isinstance(source_id, str):
            continue
        out[source_id] = source_archived_path_from_entry(source, url_map) is not None
    return out


def parse_only_source_ids(values: list[str]) -> set[str] | None:
    if not values:
        return None
    result: set[str] = set()
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                result.add(part)
    return result or None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download and archive sources referenced by Stage-0 factual packets."
    )
    parser.add_argument("--input", required=True, type=Path, help="Input factual_targets.stage0.source_backed.json")
    parser.add_argument("--output", type=Path, help="Updated packet JSON with source hashes and archive paths")
    parser.add_argument("--source-dir", required=True, type=Path, help="Directory where source files are archived")
    parser.add_argument("--manifest", type=Path, help="Optional JSON manifest of download results")
    parser.add_argument("--url-map", type=Path, help="Optional JSON map of source_id to URL or object with url/archived_path")
    parser.add_argument("--only-source-id", action="append", default=[], help="Limit to one or more source_id values; comma-separated values accepted")
    parser.add_argument("--force", action="store_true", help="Redownload and replace existing archived files")
    parser.add_argument("--dry-run", action="store_true", help="Resolve URLs and planned paths without downloading")
    parser.add_argument("--timeout", type=float, default=90.0, help="Per-request timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retry count after the first failed download")
    parser.add_argument("--sleep", type=float, default=1.0, help="Initial retry sleep in seconds; exponential backoff is used")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent")
    parser.add_argument("--no-packet-hash", action="store_true", help="Do not recompute packet_sha256 after source metadata changes")
    parser.add_argument("--preserve-source-retrieved-at", action="store_true", help="Keep existing sources[].retrieved_at values")
    parser.add_argument("--fail-on-known-warning", action="store_true", help="Exit nonzero on known source/packet consistency warnings")
    args = parser.parse_args()

    if args.retries < 0:
        raise ValueError("--retries must be >= 0")

    packet_data = load_json(args.input)
    if not isinstance(packet_data, dict):
        raise ValueError("input packet file must be a JSON object keyed by factual_target_id")

    url_map = load_url_map(args.url_map)
    only_source_ids = parse_only_source_ids(args.only_source_id)
    resolved = resolve_unique_sources(packet_data, args.source_dir, url_map, only_source_ids)
    explicit_path_used = collect_explicit_path_used(packet_data, url_map)

    warning_count = 0
    for source_id in sorted(resolved):
        warning = KNOWN_WARNINGS.get(source_id)
        if warning:
            warning_count += 1
            print(f"WARNING: {source_id}: {warning}", file=sys.stderr)

    if args.fail_on_known_warning and warning_count:
        raise ValueError(f"aborting because {warning_count} known source/packet warning(s) were found")

    results: dict[str, dict[str, Any]] = {}
    for source_id, source in sorted(resolved.items()):
        print(f"ARCHIVE {source_id}: {source.url} -> {source.target_path}", file=sys.stderr)
        result = archive_one(
            source,
            timeout=args.timeout,
            retries=args.retries,
            user_agent=args.user_agent,
            sleep_seconds=args.sleep,
            force=args.force,
            dry_run=args.dry_run,
            explicit_path_was_used=explicit_path_used.get(source_id, False),
        )
        results[source_id] = result
        print(
            f"  {result.get('status')} bytes={result.get('bytes', 'n/a')} sha256={result.get('source_sha256', 'n/a')}",
            file=sys.stderr,
        )

    updated = annotate_packets(
        packet_data,
        results,
        args.source_dir,
        recompute_packet_hashes=not args.no_packet_hash,
        preserve_source_retrieved_at=args.preserve_source_retrieved_at,
    )

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "source_dir": str(args.source_dir),
        "dry_run": args.dry_run,
        "force": args.force,
        "source_count": len(results),
        "known_warning_count": warning_count,
        "sources": results,
    }

    if args.manifest:
        write_json(args.manifest, manifest)

    if args.output:
        write_json(args.output, updated)
    elif not args.dry_run:
        print("No --output supplied; packet JSON was not written.", file=sys.stderr)

    print(
        f"DONE: resolved {len(resolved)} source(s); downloaded/hashed {len(results)} source(s); "
        f"warnings={warning_count}; dry_run={args.dry_run}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
