"""Deterministic helpers for immutable run provenance."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import (
    JUDGE_SCHEMA_VERSION,
    RELATION_REGISTRY_VERSION,
    RUBRIC_VERSION,
    SPECIFICATION_VERSION,
)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for hashing and identity."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configuration_sha256(config: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(config))


def git_commit(repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


@dataclass(frozen=True)
class RunManifest:
    """Minimum traceability record for a benchmark run."""

    run_id: str
    model_identifier: str
    provider_or_deployment: str
    configuration_sha256: str
    prompt_set_sha256: str
    random_seed: int
    code_commit: str | None
    started_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    specification_version: str = SPECIFICATION_VERSION
    rubric_version: str = RUBRIC_VERSION
    judge_schema_version: str = JUDGE_SCHEMA_VERSION
    relation_registry_version: str = RELATION_REGISTRY_VERSION
    python_version: str = field(default_factory=lambda: platform.python_version())
    platform: str = field(default_factory=platform.platform)
    retry_provenance: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_immutable(self, path: Path) -> None:
        """Create a new manifest without overwriting an existing observation."""

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")


def environment_record() -> dict[str, Any]:
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }
