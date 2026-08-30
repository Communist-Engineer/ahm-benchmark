#!/usr/bin/env python3
"""Verify the authoritative specification has not changed silently."""

from __future__ import annotations

import argparse
from pathlib import Path

from ahm_benchmark.provenance import sha256_file

EXPECTED = "10b1e4c89b7c1b92ce785269fdf185f4e0b29990bfa82ceb45b6f5042358021b"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("docs/specification/AHM-Benchmark-Spec-v0.5.0.md"),
    )
    path = parser.parse_args().path
    actual = sha256_file(path)
    if actual != EXPECTED:
        raise SystemExit(f"specification hash mismatch: expected {EXPECTED}, got {actual}")
    print(f"SPEC_HASH_OK {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
