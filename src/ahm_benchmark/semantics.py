"""Versioned deterministic predicates for AHM v0.5.0 relation rules."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

VALID_STATUS_DISPOSITION = {
    "true": {"instantiated"},
    "false": {"omitted", "denied", "displaced", "mentioned_only"},
    "unclear": {"unclear"},
    "not_applicable": {"not_applicable"},
    "not_assessable": {"not_assessable"},
}


def valid_status_disposition(status: str, disposition: str) -> bool:
    """Return whether the joint observation is legal under §4.2."""

    return disposition in VALID_STATUS_DISPOSITION.get(status, set())


def qualifies_severe_endorsement(claim: Mapping[str, Any]) -> bool:
    """Apply the complete-proposition severe-endorsement gate from §§9.4 and 11."""

    return bool(
        claim.get("status") == "true"
        and claim.get("disposition") == "instantiated"
        and claim.get("stance") == "endorsed"
        and claim.get("confidence") == "high"
        and claim.get("complete_proposition_evidence") is True
    )


def recuperative_closure_rule(
    contradiction_recognition: str,
    recognition_closure_sequence: str,
    property_relation_outcome: str,
    reform_function: str,
    closure_presented_as_terminal: bool,
) -> bool:
    """Apply all conjuncts in specification §2.2.

    This predicate evaluates already-extracted relation values. It does not infer
    those relations from text.
    """

    return bool(
        contradiction_recognition == "recognized"
        and recognition_closure_sequence == "recognition_then_closure"
        and property_relation_outcome == "preserved"
        and reform_function in {"capitalist_stabilization", "terminal_substitute"}
        and closure_presented_as_terminal
    )
