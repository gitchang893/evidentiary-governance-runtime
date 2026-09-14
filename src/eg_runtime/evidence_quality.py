from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import Field

from eg_runtime.evidence import (
    EvidenceEvent,
    EvidenceVerifier,
    VerificationStatus,
)
from eg_runtime.models import FrozenModel


class EvidenceQualityError(RuntimeError):
    """Raised when evidence completeness cannot be assessed."""


SUPPORTED_EVIDENCE_ELEMENTS = frozenset(
    {
        "authorized_purpose",
        "declared_purpose",
        "declared_plan",
        "role",
        "execution_history",
        "data_provenance",
        "approval",
        "proposal",
        "applicable_norms",
        "predicate_results",
        "approval_binding_result",
        "governance_decision",
        "decision_reasons",
        "decision_phase",
        "preservation_directive",
        "execution_status",
        "proposal_execution_correlation",
        "integrity",
        "policy_version",
    }
)


class EvidenceCompletenessResult(FrozenModel):
    """Claim-relative evidence-completeness assessment."""

    required: list[str]
    present: list[str]
    missing: list[str]
    completeness: float = Field(ge=0.0, le=1.0)
    integrity_valid: bool
    evidence_status: VerificationStatus


def _read_events(path: Path) -> list[EvidenceEvent]:
    """Read and validate JSONL evidence events."""

    if not path.exists():
        raise EvidenceQualityError(
            f"evidence file does not exist: {path}"
        )

    events: list[EvidenceEvent] = []

    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceQualityError(
                f"line {line_number} contains invalid JSON"
            ) from exc

        try:
            event = EvidenceEvent.model_validate(raw)
        except ValueError as exc:
            raise EvidenceQualityError(
                f"line {line_number} contains an invalid evidence event"
            ) from exc

        events.append(event)

    return events


def _payloads(
    events: Iterable[EvidenceEvent],
    *event_types: str,
) -> list[dict[str, Any]]:
    """Return payloads for selected event types."""

    selected = set(event_types)

    return [
        event.payload
        for event in events
        if event.event_type in selected
    ]


def _contexts(
    events: list[EvidenceEvent],
) -> list[dict[str, Any]]:
    """Extract recorded governance contexts."""

    contexts: list[dict[str, Any]] = []

    for payload in _payloads(
        events,
        "context_loaded",
        "context_logged",
    ):
        context = payload.get("context")

        if isinstance(context, dict):
            contexts.append(context)

    return contexts


def _proposals(
    events: list[EvidenceEvent],
) -> list[dict[str, Any]]:
    """Extract recorded structured proposals."""

    proposals: list[dict[str, Any]] = []

    for payload in _payloads(
        events,
        "proposal_created",
        "proposal_logged",
    ):
        proposal = payload.get("proposal")

        if isinstance(proposal, dict):
            proposals.append(proposal)

    return proposals


def _decisions(
    events: list[EvidenceEvent],
) -> list[dict[str, Any]]:
    """Extract pre-execution and post-hoc decisions."""

    decisions: list[dict[str, Any]] = []

    for payload in _payloads(
        events,
        "governance_decision",
        "post_hoc_governance_decision",
    ):
        decision = payload.get("decision")

        if isinstance(decision, dict):
            decisions.append(decision)

    return decisions


def _terminal_execution_events(
    events: list[EvidenceEvent],
) -> list[EvidenceEvent]:
    """Return execution and explicit non-execution events."""

    terminal_types = {
        "tool_completed",
        "tool_failed",
        "tool_blocked",
        "approval_required",
    }

    return [
        event
        for event in events
        if event.event_type in terminal_types
    ]


def _execution_proposal_ids(
    events: list[EvidenceEvent],
) -> set[str]:
    """Extract proposal identifiers from operational outcomes."""

    identifiers: set[str] = set()

    for event in _terminal_execution_events(events):
        execution = event.payload.get("execution")

        if isinstance(execution, dict):
            proposal_id = execution.get("proposal_id")

            if isinstance(proposal_id, str):
                identifiers.add(proposal_id)

        proposal_id = event.payload.get("proposal_id")

        if isinstance(proposal_id, str):
            identifiers.add(proposal_id)

    return identifiers


def _recorded_elements(
    events: list[EvidenceEvent],
    *,
    integrity_valid: bool,
) -> set[str]:
    """Identify evidence elements present in one recorded run."""

    present: set[str] = set()

    contexts = _contexts(events)
    proposals = _proposals(events)
    decisions = _decisions(events)
    terminal_events = _terminal_execution_events(events)

    if any(
        isinstance(context.get("authorized_purpose"), str)
        and bool(context["authorized_purpose"])
        for context in contexts
    ):
        present.add("authorized_purpose")

    if any(
        isinstance(context.get("declared_plan"), list)
        and bool(context["declared_plan"])
        for context in contexts
    ):
        present.add("declared_plan")

    if any(
        isinstance(context.get("role"), str)
        and bool(context["role"])
        for context in contexts
    ):
        present.add("role")

    if any(
        isinstance(context.get("history"), list)
        for context in contexts
    ):
        present.add("execution_history")

    if any(
        isinstance(context.get("data_context"), dict)
        and bool(context["data_context"])
        for context in contexts
    ):
        present.add("data_provenance")

    if any(
        context.get("approval") is not None
        for context in contexts
    ):
        present.add("approval")

    if proposals:
        present.add("proposal")

    if any(
        isinstance(
            proposal.get("declared_purpose"),
            str,
        )
        and bool(proposal["declared_purpose"])
        for proposal in proposals
    ):
        present.add("declared_purpose")

    selected_norm_payloads = _payloads(
        events,
        "norms_selected",
    )

    norms_from_selection = any(
        isinstance(payload.get("norm_ids"), list)
        and bool(payload["norm_ids"])
        for payload in selected_norm_payloads
    )

    norms_from_decision = any(
        isinstance(decision.get("applicable_norms"), list)
        and bool(decision["applicable_norms"])
        for decision in decisions
    )

    if norms_from_selection or norms_from_decision:
        present.add("applicable_norms")

    predicate_results = [
        result
        for decision in decisions
        for result in decision.get("predicate_results", [])
        if isinstance(result, dict)
    ]

    if predicate_results:
        present.add("predicate_results")

    if any(
        result.get("predicate_type") == "approval_binding"
        for result in predicate_results
    ):
        present.add("approval_binding_result")

    if decisions:
        present.add("governance_decision")

    if any(
        isinstance(decision.get("reasons"), list)
        and bool(decision["reasons"])
        for decision in decisions
    ):
        present.add("decision_reasons")

    start_payloads = _payloads(
        events,
        "run_started",
    )

    if any(
        isinstance(payload.get("metadata"), dict)
        and bool(
            payload["metadata"].get("decision_phase")
        )
        for payload in start_payloads
    ):
        present.add("decision_phase")

    if any(
        isinstance(decision.get("preservation"), str)
        and bool(decision["preservation"])
        for decision in decisions
    ):
        present.add("preservation_directive")

    if terminal_events:
        present.add("execution_status")

    proposal_ids = {
        proposal_id
        for proposal in proposals
        if isinstance(
            proposal_id := proposal.get("proposal_id"),
            str,
        )
    }

    execution_proposal_ids = _execution_proposal_ids(
        events
    )

    if proposal_ids & execution_proposal_ids:
        present.add("proposal_execution_correlation")

    if integrity_valid:
        present.add("integrity")

    if any(
        isinstance(context.get("policy_version"), str)
        and bool(context["policy_version"])
        for context in contexts
    ) or any(
        isinstance(payload.get("policy_version"), str)
        and bool(payload["policy_version"])
        for payload in start_payloads
    ):
        present.add("policy_version")

    return present


def assess_evidence_completeness(
    path: str | Path,
    required_evidence: Iterable[str],
) -> EvidenceCompletenessResult:
    """Assess evidence completeness relative to a scenario claim."""

    evidence_path = Path(path)

    required = list(dict.fromkeys(required_evidence))

    if not required:
        raise EvidenceQualityError(
            "required_evidence must contain at least one element"
        )

    unsupported = sorted(
        set(required) - SUPPORTED_EVIDENCE_ELEMENTS
    )

    if unsupported:
        raise EvidenceQualityError(
            "unsupported evidence elements: "
            + ", ".join(unsupported)
        )

    events = _read_events(evidence_path)
    verification = EvidenceVerifier().verify(
        evidence_path
    )

    integrity_valid = (
        verification.status is VerificationStatus.VALID
    )

    recorded = _recorded_elements(
        events,
        integrity_valid=integrity_valid,
    )

    present = [
        element
        for element in required
        if element in recorded
    ]

    missing = [
        element
        for element in required
        if element not in recorded
    ]

    completeness = len(present) / len(required)

    return EvidenceCompletenessResult(
        required=required,
        present=present,
        missing=missing,
        completeness=completeness,
        integrity_valid=integrity_valid,
        evidence_status=verification.status,
    )
