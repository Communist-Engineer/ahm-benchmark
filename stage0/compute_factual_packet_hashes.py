#!/usr/bin/env python3
"""
Compute source-file SHA-256 hashes and deterministic packet_sha256 values for
Stage-0 factual-target packets.

Hash order:
1. If --source-dir is provided, compute SHA-256 for archived source files and
   write/update sources[].source_sha256, source_size_bytes, and source_hash_algorithm.
2. Compute packet_sha256 from canonical JSON after removing packet_sha256 itself.

The packet hash therefore changes when packet text, acceptable ranges, source slices,
or source hashes change, while avoiding the self-referential packet_sha256 problem.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

HASH_ALGORITHM = "sha256"
PACKET_HASH_FIELD = "packet_sha256"
SOURCE_HASH_FIELD = "source_sha256"
SOURCE_SIZE_FIELD = "source_size_bytes"
SOURCE_HASH_ALGO_FIELD = "source_hash_algorithm"

SOURCE_PATH_KEYS = (
    "local_path",
    "source_path",
    "file_path",
    "archived_path",
    "archive_path",
)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_canonical_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def sanitize_source_id(source_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", source_id).strip("_")


def candidate_source_paths(source: dict[str, Any], source_dir: Path | None) -> list[Path]:
    candidates: list[Path] = []

    for key in SOURCE_PATH_KEYS:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            p = Path(value)
            if not p.is_absolute() and source_dir is not None:
                p = source_dir / p
            candidates.append(p)

    if source_dir is not None:
        source_id = source.get("source_id")
        if isinstance(source_id, str) and source_id.strip():
            safe_id = sanitize_source_id(source_id)
            # Prefer exact source_id / sanitized source_id names, then any extension.
            candidates.extend([
                source_dir / source_id,
                source_dir / safe_id,
            ])
            candidates.extend(sorted(source_dir.glob(f"{source_id}.*")))
            if safe_id != source_id:
                candidates.extend(sorted(source_dir.glob(f"{safe_id}.*")))

            # As a convenience for nested archive directories, search one level deep.
            candidates.extend(sorted(source_dir.glob(f"*/{source_id}")))
            candidates.extend(sorted(source_dir.glob(f"*/{source_id}.*")))
            if safe_id != source_id:
                candidates.extend(sorted(source_dir.glob(f"*/{safe_id}")))
                candidates.extend(sorted(source_dir.glob(f"*/{safe_id}.*")))

    # Deduplicate while preserving order.
    out: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        key = str(p)
        if key not in seen:
            out.append(p)
            seen.add(key)
    return out


def resolve_source_file(source: dict[str, Any], source_dir: Path | None) -> Path | None:
    for candidate in candidate_source_paths(source, source_dir):
        if candidate.is_file():
            return candidate
    return None


def update_source_hashes(
    packet: dict[str, Any],
    source_dir: Path | None,
    require_source_files: bool,
    verify_only: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    sources = packet.get("sources", [])
    target_id = packet.get("factual_target_id", "<unknown-target>")

    if not isinstance(sources, list):
        errors.append(f"{target_id}: sources must be an array")
        return

    for idx, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"{target_id}: sources[{idx}] must be an object")
            continue

        source_id = source.get("source_id", f"source[{idx}]")
        path = resolve_source_file(source, source_dir)

        if path is None:
            msg = f"{target_id}: source file not found for {source_id!r}"
            if require_source_files:
                errors.append(msg)
            else:
                warnings.append(msg)
            continue

        computed = sha256_file(path)
        size = path.stat().st_size
        existing = source.get(SOURCE_HASH_FIELD)

        if verify_only:
            if existing != computed:
                errors.append(
                    f"{target_id}: {source_id!r} source hash mismatch: "
                    f"existing={existing!r} computed={computed} path={path}"
                )
        else:
            source[SOURCE_HASH_FIELD] = computed
            source[SOURCE_SIZE_FIELD] = size
            source[SOURCE_HASH_ALGO_FIELD] = HASH_ALGORITHM
            # Store a stable relative path if possible, otherwise absolute path.
            if source_dir is not None:
                try:
                    source["archived_path"] = os.fspath(path.relative_to(source_dir))
                except ValueError:
                    source["archived_path"] = os.fspath(path)
            else:
                source["archived_path"] = os.fspath(path)


def packet_for_hash(packet: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(packet)
    normalized.pop(PACKET_HASH_FIELD, None)
    return normalized


def update_packet_hash(
    target_key: str,
    packet: dict[str, Any],
    verify_only: bool,
    errors: list[str],
) -> str:
    computed = sha256_canonical_json(packet_for_hash(packet))
    existing = packet.get(PACKET_HASH_FIELD)

    if verify_only:
        if existing != computed:
            errors.append(
                f"{target_key}: packet_sha256 mismatch: existing={existing!r} computed={computed}"
            )
    else:
        packet[PACKET_HASH_FIELD] = computed
    return computed


def load_packets(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object keyed by factual_target_id")
    for key, packet in data.items():
        if not isinstance(packet, dict):
            raise ValueError(f"{key}: packet must be an object")
        embedded_id = packet.get("factual_target_id")
        if embedded_id != key:
            raise ValueError(f"{key}: factual_target_id mismatch: {embedded_id!r}")
    return data


def write_packets(path: Path, packets: dict[str, dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(packets, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def build_manifest(
    packets: dict[str, dict[str, Any]],
    input_path: Path,
    output_path: Path | None,
) -> dict[str, Any]:
    packet_entries: dict[str, Any] = {}
    for target_id, packet in sorted(packets.items()):
        source_entries = []
        for source in packet.get("sources", []):
            if isinstance(source, dict):
                source_entries.append({
                    "source_id": source.get("source_id"),
                    "archived_path": source.get("archived_path"),
                    "source_sha256": source.get(SOURCE_HASH_FIELD),
                    "source_size_bytes": source.get(SOURCE_SIZE_FIELD),
                    "source_hash_algorithm": source.get(SOURCE_HASH_ALGO_FIELD),
                })
        packet_entries[target_id] = {
            "packet_sha256": packet.get(PACKET_HASH_FIELD),
            "packet_version": packet.get("packet_version"),
            "packet_status": packet.get("packet_status"),
            "sources": source_entries,
        }

    return {
        "manifest_type": "stage0_factual_target_hash_manifest",
        "hash_algorithm": HASH_ALGORITHM,
        "input_file": os.fspath(input_path),
        "output_file": os.fspath(output_path) if output_path else None,
        "packets": packet_entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute source SHA-256 hashes and packet_sha256 values for Stage-0 factual-target packets."
    )
    parser.add_argument("--input", required=True, type=Path, help="Input factual_targets JSON file")
    parser.add_argument("--output", type=Path, help="Output JSON file. Omit with --in-place or --verify-only.")
    parser.add_argument("--in-place", action="store_true", help="Overwrite --input with updated hashes")
    parser.add_argument("--source-dir", type=Path, help="Directory containing archived source files")
    parser.add_argument(
        "--require-source-files",
        action="store_true",
        help="Fail if any packet source cannot be matched to an archived source file",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Check existing source_sha256 and packet_sha256 values; do not write output",
    )
    parser.add_argument("--manifest", type=Path, help="Optional manifest JSON output")
    args = parser.parse_args()

    if args.verify_only and (args.output or args.in_place):
        raise ValueError("--verify-only cannot be combined with --output or --in-place")
    if args.in_place and args.output:
        raise ValueError("use either --in-place or --output, not both")
    if not args.verify_only and not args.in_place and args.output is None:
        raise ValueError("provide --output, --in-place, or --verify-only")
    if args.source_dir is not None and not args.source_dir.is_dir():
        raise ValueError(f"--source-dir is not a directory: {args.source_dir}")

    packets = load_packets(args.input)
    errors: list[str] = []
    warnings: list[str] = []
    computed_packet_hashes: dict[str, str] = {}

    for target_id in sorted(packets):
        packet = packets[target_id]
        update_source_hashes(
            packet=packet,
            source_dir=args.source_dir,
            require_source_files=args.require_source_files,
            verify_only=args.verify_only,
            errors=errors,
            warnings=warnings,
        )
        computed_packet_hashes[target_id] = update_packet_hash(
            target_key=target_id,
            packet=packet,
            verify_only=args.verify_only,
            errors=errors,
        )

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    output_path: Path | None = None
    if not args.verify_only:
        output_path = args.input if args.in_place else args.output
        assert output_path is not None
        write_packets(output_path, packets)
        print(f"WROTE: {output_path}")
    else:
        print("VERIFY PASSED")

    for target_id, digest in computed_packet_hashes.items():
        print(f"{target_id} packet_sha256 {digest}")

    if args.manifest:
        manifest = build_manifest(packets, args.input, output_path)
        args.manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"MANIFEST: {args.manifest}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
