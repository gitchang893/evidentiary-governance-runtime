from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from eg_runtime.evidence import (
    EvidenceEvent,
    EvidenceVerifier,
    VerificationStatus,
)
from eg_runtime.models import FrozenModel


class EvidenceSemanticError(RuntimeError):
    """Raised when semantic evidence verification cannot run."""


class SemanticVerificationStatus(StrEnum):
    """Result of semantic evidence verification."""

    VALID = "valid"
    INVALID = "invalid"


class SemanticIssue(FrozenModel):
    """One semantic inconsistency in an evidence record."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    sequence: int | None = Field(default=None, ge=0)


class SemanticVerificationResult(FrozenModel):
    """Semantic verification result for one evidence chain."""

    status: SemanticVerificationStatus
    run_id: str | None
    proposal_id: str | None
    integrity_status: VerificationStatus
    issues: list[SemanticIssue]

    @property
    def valid(self) -> bool:
        return self.status is SemanticVerificationStatus.VALID


PROPOSAL_EVENT_TYPES = {
    "proposal_created",
    "proposal_logged",
}

OPERATIONAL_EVENT_TYPES = {
    "tool_invoked",
    "tool_completed",
    "tool_failed",
    "tool_blocked",
    "approval_required",
}

EXECUTION_EVENT_TYPES = {
    "tool_completed",
    "tool_failed",
}

NON_EXECUTION_EVENT_TYPES = {
    "tool_blocked",
    "approval_required",
}


def _read_events(
    path: Path,
) -> list[EvidenceEvent]:
    """Read and validate JSONL evidence events."""

    if not path.exists():
        raise EvidenceSemanticError(
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
            raise EvidenceSemanticError(
                f"line {line_number} contains invalid JSON"
            ) from exc

        try:
            event = EvidenceEvent.model_validate(raw)
        except ValidationError as exc:
            raise EvidenceSemanticError(
                f"line {line_number} contains "
                "an invalid evidence event"
            ) from exc

        events.append(event)

    if not events:
        raise EvidenceSemanticError(
            "evidence file contains no events"
        )

    return events


def _sequence(
    event: EvidenceEvent,
) -> int | None:
    """Return an event sequence when available."""

    value = getattr(event, "sequence", None)

    if isinstance(value, int):
        return value

    return None


def _nested_mapping(
    payload: dict[str, Any],
    key: str,
) -> dict[str, Any] | None:
    """Return one nested mapping from a payload."""

    value = payload.get(key)

    if isinstance(value, dict):
        return value

    return None


def _proposal_id_from_operational_event(
    event: EvidenceEvent,
) -> str | None:
    """Extract a proposal identifier from an operational event."""

    direct = event.payload.get("proposal_id")

    if isinstance(direct, str) and direct:
        return direct

    execution = _nested_mapping(
        event.payload,
        "execution",
    )

    if execution is not None:
        nested = execution.get("proposal_id")

        if isinstance(nested, str) and nested:
            return nested

    return None


def _decision_payload(
    event: EvidenceEvent,
) -> dict[str, Any] | None:
    """Extract a structured decision from an evidence event."""

    return _nested_mapping(
        event.payload,
        "decision",
    )


def _disposition(
    event: EvidenceEvent,
) -> str | None:
    """Extract the decision disposition."""

    decision = _decision_payload(event)

    if decision is None:
        return None

    value = decision.get("disposition")

    if isinstance(value, str):
        return value

    return None


class EvidenceSemanticsVerifier:
    """Verify proposal, decision, and execution correspondence."""

    def verify(
        self,
        path: str | Path,
    ) -> SemanticVerificationResult:
        """Verify one evidence chain semantically."""

        evidence_path = Path(path)
        events = _read_events(evidence_path)

        integrity = EvidenceVerifier().verify(
            evidence_path
        )

        issues: list[SemanticIssue] = []

        if integrity.status is not VerificationStatus.VALID:
            issues.append(
                SemanticIssue(
                    code="integrity_invalid",
                    message=(
                        "semantic verification requires "
                        "a valid integrity chain"
                    ),
                )
            )

        start_events = [
            event
            for event in events
            if event.event_type == "run_started"
        ]

        run_id: str | None = None
        start_proposal_id: str | None = None

        if len(start_events) != 1:
            issues.append(
                SemanticIssue(
                    code="run_start_count",
                    message=(
                        "evidence must contain exactly "
                        "one run_started event"
                    ),
                )
            )
        else:
            start_event = start_events[0]

            raw_run_id = getattr(
                start_event,
                "run_id",
                None,
            )

            if isinstance(raw_run_id, str):
                run_id = raw_run_id

            raw_proposal_id = start_event.payload.get(
                "proposal_id"
            )

            if isinstance(raw_proposal_id, str):
                start_proposal_id = raw_proposal_id

        proposal_events = [
            event
            for event in events
            if event.event_type in PROPOSAL_EVENT_TYPES
        ]

        proposal_ids: list[str] = []

        for event in proposal_events:
            proposal = _nested_mapping(
                event.payload,
                "proposal",
            )

            if proposal is None:
                issues.append(
                    SemanticIssue(
                        code="proposal_payload_missing",
                        message=(
                            "proposal event lacks "
                            "a structured proposal"
                        ),
                        sequence=_sequence(event),
                    )
                )
                continue

            proposal_id = proposal.get(
                "proposal_id"
            )

            if (
                not isinstance(proposal_id, str)
                or not proposal_id
            ):
                issues.append(
                    SemanticIssue(
                        code="proposal_id_missing",
                        message=(
                            "structured proposal lacks "
                            "a proposal identifier"
                        ),
                        sequence=_sequence(event),
                    )
                )
                continue

            proposal_ids.append(proposal_id)

        proposal_id: str | None = None
        unique_proposal_ids = set(proposal_ids)

        if not proposal_ids:
            issues.append(
                SemanticIssue(
                    code="proposal_missing",
                    message=(
                        "evidence contains no structured proposal"
                    ),
                )
            )
        elif len(unique_proposal_ids) > 1:
            issues.append(
                SemanticIssue(
                    code="proposal_id_conflict",
                    message=(
                        "evidence contains conflicting "
                        "proposal identifiers"
                    ),
                )
            )
        else:
            proposal_id = proposal_ids[0]

        if (
            start_proposal_id is not None
            and proposal_id is not None
            and start_proposal_id != proposal_id
        ):
            issues.append(
                SemanticIssue(
                    code="run_proposal_mismatch",
                    message=(
                        "run_started proposal identifier "
                        "does not match the structured proposal"
                    ),
                    sequence=(
                        _sequence(start_events[0])
                        if start_events
                        else None
                    ),
                )
            )

        operational_events = [
            event
            for event in events
            if event.event_type in OPERATIONAL_EVENT_TYPES
        ]

        for event in operational_events:
            operational_proposal_id = (
                _proposal_id_from_operational_event(
                    event
                )
            )

            if operational_proposal_id is None:
                issues.append(
                    SemanticIssue(
                        code="operational_proposal_id_missing",
                        message=(
                            f"{event.event_type} lacks "
                            "a proposal identifier"
                        ),
                        sequence=_sequence(event),
                    )
                )
                continue

            if (
                proposal_id is not None
                and operational_proposal_id
                != proposal_id
            ):
                issues.append(
                    SemanticIssue(
                        code="proposal_execution_mismatch",
                        message=(
                            f"{event.event_type} refers to "
                            "a different proposal"
                        ),
                        sequence=_sequence(event),
                    )
                )

        pre_decisions = [
            event
            for event in events
            if event.event_type
            == "governance_decision"
        ]

        post_decisions = [
            event
            for event in events
            if event.event_type
            == "post_hoc_governance_decision"
        ]

        invoked = [
            event
            for event in events
            if event.event_type == "tool_invoked"
        ]

        completed_or_failed = [
            event
            for event in events
            if event.event_type in EXECUTION_EVENT_TYPES
        ]

        blocked = [
            event
            for event in events
            if event.event_type == "tool_blocked"
        ]

        approval_required = [
            event
            for event in events
            if event.event_type == "approval_required"
        ]

        if len(pre_decisions) > 1:
            issues.append(
                SemanticIssue(
                    code="multiple_pre_execution_decisions",
                    message=(
                        "evidence contains multiple "
                        "pre-execution decisions"
                    ),
                )
            )

        for decision_event in pre_decisions:
            disposition = _disposition(
                decision_event
            )

            decision_sequence = _sequence(
                decision_event
            )

            if disposition not in {
                "allow",
                "block",
                "escalate",
            }:
                issues.append(
                    SemanticIssue(
                        code="invalid_disposition",
                        message=(
                            "pre-execution decision has "
                            "an invalid disposition"
                        ),
                        sequence=decision_sequence,
                    )
                )
                continue

            later_control_events = [
                event
                for event in (
                    invoked
                    + completed_or_failed
                    + blocked
                    + approval_required
                )
                if (
                    decision_sequence is None
                    or _sequence(event) is None
                    or _sequence(event)
                    > decision_sequence
                )
            ]

            earlier_control_events = [
                event
                for event in (
                    invoked
                    + completed_or_failed
                    + blocked
                    + approval_required
                )
                if (
                    decision_sequence is not None
                    and _sequence(event) is not None
                    and _sequence(event)
                    < decision_sequence
                )
            ]

            if earlier_control_events:
                issues.append(
                    SemanticIssue(
                        code="pre_decision_order",
                        message=(
                            "operational control event occurs "
                            "before the pre-execution decision"
                        ),
                        sequence=decision_sequence,
                    )
                )

            later_types = {
                event.event_type
                for event in later_control_events
            }

            if disposition == "allow":
                if "tool_invoked" not in later_types:
                    issues.append(
                        SemanticIssue(
                            code="allow_without_invocation",
                            message=(
                                "allow decision lacks "
                                "a subsequent tool invocation"
                            ),
                            sequence=decision_sequence,
                        )
                    )

                if not (
                    later_types
                    & {
                        "tool_completed",
                        "tool_failed",
                    }
                ):
                    issues.append(
                        SemanticIssue(
                            code="allow_without_terminal_execution",
                            message=(
                                "allow decision lacks "
                                "a terminal execution outcome"
                            ),
                            sequence=decision_sequence,
                        )
                    )

                if later_types & NON_EXECUTION_EVENT_TYPES:
                    issues.append(
                        SemanticIssue(
                            code="allow_with_non_execution",
                            message=(
                                "allow decision is inconsistent "
                                "with an explicit non-execution event"
                            ),
                            sequence=decision_sequence,
                        )
                    )

            if disposition == "block":
                if later_types & {
                    "tool_invoked",
                    "tool_completed",
                    "tool_failed",
                }:
                    issues.append(
                        SemanticIssue(
                            code="blocked_action_executed",
                            message=(
                                "blocked action was followed "
                                "by tool execution"
                            ),
                            sequence=decision_sequence,
                        )
                    )

                if "tool_blocked" not in later_types:
                    issues.append(
                        SemanticIssue(
                            code="block_without_non_execution",
                            message=(
                                "block decision lacks "
                                "an explicit tool_blocked event"
                            ),
                            sequence=decision_sequence,
                        )
                    )

            if disposition == "escalate":
                if later_types & {
                    "tool_invoked",
                    "tool_completed",
                    "tool_failed",
                }:
                    issues.append(
                        SemanticIssue(
                            code="escalated_action_executed",
                            message=(
                                "escalated action was followed "
                                "by tool execution"
                            ),
                            sequence=decision_sequence,
                        )
                    )

                if "approval_required" not in later_types:
                    issues.append(
                        SemanticIssue(
                            code="escalation_without_approval",
                            message=(
                                "escalate decision lacks "
                                "an approval_required event"
                            ),
                            sequence=decision_sequence,
                        )
                    )

        for decision_event in post_decisions:
            decision_sequence = _sequence(
                decision_event
            )

            prior_terminal_execution = any(
                decision_sequence is None
                or _sequence(event) is None
                or _sequence(event)
                < decision_sequence
                for event in completed_or_failed
            )

            if not prior_terminal_execution:
                issues.append(
                    SemanticIssue(
                        code="post_hoc_without_prior_execution",
                        message=(
                            "post-hoc decision lacks "
                            "a prior terminal execution event"
                        ),
                        sequence=decision_sequence,
                    )
                )

        if not pre_decisions and not post_decisions:
            if not invoked:
                issues.append(
                    SemanticIssue(
                        code="logging_without_invocation",
                        message=(
                            "ordinary logging run lacks "
                            "a tool invocation"
                        ),
                    )
                )

            if not completed_or_failed:
                issues.append(
                    SemanticIssue(
                        code="logging_without_terminal_execution",
                        message=(
                            "ordinary logging run lacks "
                            "a terminal execution outcome"
                        ),
                    )
                )

        status = (
            SemanticVerificationStatus.VALID
            if not issues
            else SemanticVerificationStatus.INVALID
        )

        return SemanticVerificationResult(
            status=status,
            run_id=run_id,
            proposal_id=proposal_id,
            integrity_status=integrity.status,
            issues=issues,
        )
