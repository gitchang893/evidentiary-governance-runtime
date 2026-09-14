from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from eg_runtime.approval_workflow import (
    ApprovalResolution,
    ApprovalResolutionStatus,
    PendingApproval,
)
from eg_runtime.evidence import (
    EvidenceEvent,
    EvidenceVerifier,
    VerificationStatus,
)
from eg_runtime.evidence_semantics import (
    EvidenceSemanticsVerifier,
    SemanticVerificationStatus,
)
from eg_runtime.models import (
    Disposition,
    FrozenModel,
)


class ApprovalLineageError(RuntimeError):
    """Raised when approval-lineage verification cannot run."""


class ApprovalLineageStatus(StrEnum):
    """Cross-run approval-lineage verification status."""

    VALID = "valid"
    INVALID = "invalid"


class ApprovalLineageIssue(FrozenModel):
    """One inconsistency in an approval workflow lineage."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ApprovalLineageResult(FrozenModel):
    """Verification result for an approval workflow lineage."""

    status: ApprovalLineageStatus
    approval_request_id: str
    original_run_id: str | None
    resumed_run_id: str | None
    issues: list[ApprovalLineageIssue]

    @property
    def valid(self) -> bool:
        """Return whether the complete lineage is valid."""

        return self.status is ApprovalLineageStatus.VALID


def _read_events(
    path: Path,
) -> list[EvidenceEvent]:
    """Read and validate one JSONL evidence chain."""

    if not path.exists():
        raise ApprovalLineageError(
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
            raise ApprovalLineageError(
                f"line {line_number} contains invalid JSON"
            ) from exc

        try:
            event = EvidenceEvent.model_validate(raw)
        except ValidationError as exc:
            raise ApprovalLineageError(
                f"line {line_number} contains "
                "an invalid evidence event"
            ) from exc

        events.append(event)

    if not events:
        raise ApprovalLineageError(
            "evidence file contains no events"
        )

    return events


def _run_id(
    events: list[EvidenceEvent],
) -> str | None:
    """Extract the unique run identifier."""

    identifiers = {
        event.run_id
        for event in events
    }

    if len(identifiers) != 1:
        return None

    return next(iter(identifiers))


def _nested_mapping(
    payload: dict[str, Any],
    key: str,
) -> dict[str, Any] | None:
    """Return a nested mapping when present."""

    value = payload.get(key)

    if isinstance(value, dict):
        return value

    return None


def _structured_proposal_ids(
    events: list[EvidenceEvent],
) -> set[str]:
    """Extract identifiers from structured proposal events."""

    identifiers: set[str] = set()

    for event in events:
        if event.event_type not in {
            "proposal_created",
            "proposal_logged",
        }:
            continue

        proposal = _nested_mapping(
            event.payload,
            "proposal",
        )

        if proposal is None:
            continue

        proposal_id = proposal.get("proposal_id")

        if isinstance(proposal_id, str) and proposal_id:
            identifiers.add(proposal_id)

    return identifiers


def _decision_records(
    events: list[EvidenceEvent],
) -> list[dict[str, Any]]:
    """Extract structured governance decisions."""

    records: list[dict[str, Any]] = []

    for event in events:
        if event.event_type not in {
            "governance_decision",
            "post_hoc_governance_decision",
        }:
            continue

        decision = _nested_mapping(
            event.payload,
            "decision",
        )

        if decision is not None:
            records.append(decision)

    return records


def _approval_required_records(
    events: list[EvidenceEvent],
) -> list[dict[str, Any]]:
    """Extract approval-required event payloads."""

    return [
        event.payload
        for event in events
        if event.event_type == "approval_required"
    ]


def _approval_required_matches(
    payload: dict[str, Any],
    *,
    proposal_id: str,
    decision_id: str,
) -> bool:
    """Match a structured approval-required event."""

    approval_request = _nested_mapping(
        payload,
        "approval_request",
    )

    if approval_request is None:
        return False

    return (
        approval_request.get("proposal_id")
        == proposal_id
        and approval_request.get("decision_id")
        == decision_id
        and approval_request.get("status")
        == "pending"
    )


def _run_metadata(
    events: list[EvidenceEvent],
) -> dict[str, Any]:
    """Extract metadata from the run-start event."""

    for event in events:
        if event.event_type != "run_started":
            continue

        metadata = event.payload.get("metadata")

        if isinstance(metadata, dict):
            return metadata

    return {}


def _recorded_approval_ids(
    events: list[EvidenceEvent],
) -> set[str]:
    """Extract approval identifiers from recorded contexts."""

    identifiers: set[str] = set()

    for event in events:
        if event.event_type not in {
            "context_loaded",
            "context_logged",
        }:
            continue

        context = _nested_mapping(
            event.payload,
            "context",
        )

        if context is None:
            continue

        approval = context.get("approval")

        if not isinstance(approval, dict):
            continue

        approval_id = approval.get("approval_id")

        if isinstance(approval_id, str) and approval_id:
            identifiers.add(approval_id)

    return identifiers


def _add_issue(
    issues: list[ApprovalLineageIssue],
    code: str,
    message: str,
) -> None:
    """Append one lineage issue."""

    issues.append(
        ApprovalLineageIssue(
            code=code,
            message=message,
        )
    )


class ApprovalLineageVerifier:
    """Verify escalation, resolution, and resumed-run lineage."""

    def verify(
        self,
        *,
        original_evidence_path: str | Path,
        pending: PendingApproval,
        resolution: ApprovalResolution,
        resumed_evidence_path: str | Path | None = None,
    ) -> ApprovalLineageResult:
        """Verify one approval workflow across runtime runs."""

        original_path = Path(
            original_evidence_path
        )

        original_events = _read_events(
            original_path
        )

        issues: list[ApprovalLineageIssue] = []

        original_run_id = _run_id(
            original_events
        )

        resumed_run_id: str | None = None

        original_integrity = EvidenceVerifier().verify(
            original_path
        )

        if (
            original_integrity.status
            is not VerificationStatus.VALID
        ):
            _add_issue(
                issues,
                "original_integrity_invalid",
                "the original escalation chain "
                "has invalid integrity",
            )

        original_semantics = (
            EvidenceSemanticsVerifier().verify(
                original_path
            )
        )

        if (
            original_semantics.status
            is not SemanticVerificationStatus.VALID
        ):
            _add_issue(
                issues,
                "original_semantics_invalid",
                "the original escalation chain "
                "is semantically inconsistent",
            )

        if (
            pending.decision.disposition
            is not Disposition.ESCALATE
        ):
            _add_issue(
                issues,
                "pending_decision_not_escalate",
                "the pending workflow does not contain "
                "an escalate decision",
            )

        original_proposal_ids = (
            _structured_proposal_ids(
                original_events
            )
        )

        if original_proposal_ids != {
            pending.proposal.proposal_id
        }:
            _add_issue(
                issues,
                "original_proposal_mismatch",
                "the original evidence proposal does not "
                "match the pending workflow",
            )

        decisions = _decision_records(
            original_events
        )

        matching_escalation = any(
            decision.get("decision_id")
            == pending.decision.decision_id
            and decision.get("disposition")
            == Disposition.ESCALATE.value
            for decision in decisions
        )

        if not matching_escalation:
            _add_issue(
                issues,
                "original_escalation_missing",
                "the original evidence lacks the "
                "pending escalation decision",
            )

        approval_required = (
            _approval_required_records(
                original_events
            )
        )

        matching_approval_event = any(
            _approval_required_matches(
                payload,
                proposal_id=(
                    pending.proposal.proposal_id
                ),
                decision_id=(
                    pending.decision.decision_id
                ),
            )
            for payload in approval_required
        )

        if not matching_approval_event:
            _add_issue(
                issues,
                "approval_required_event_mismatch",
                "the original evidence lacks a matching "
                "approval-required event",
            )

        if (
            resolution.approval_request_id
            != pending.approval_request_id
        ):
            _add_issue(
                issues,
                "resolution_request_id_mismatch",
                "the resolution refers to a different "
                "approval request",
            )

        if (
            resolution.status
            is ApprovalResolutionStatus.APPROVED
        ):
            if not resolution.can_resume:
                _add_issue(
                    issues,
                    "approved_resolution_not_resumable",
                    "the approved resolution lacks a valid "
                    "approval or resumed context",
                )

            if resumed_evidence_path is None:
                _add_issue(
                    issues,
                    "approved_resolution_without_resumed_run",
                    "an approved resolution requires a "
                    "resumed runtime evidence chain",
                )
            else:
                resumed_run_id = self._verify_resumed_run(
                    path=Path(resumed_evidence_path),
                    pending=pending,
                    resolution=resolution,
                    original_run_id=original_run_id,
                    issues=issues,
                )

        else:
            if resumed_evidence_path is not None:
                _add_issue(
                    issues,
                    "resume_for_non_approved_resolution",
                    "a rejected or invalid resolution "
                    "cannot produce a resumed run",
                )

        status = (
            ApprovalLineageStatus.VALID
            if not issues
            else ApprovalLineageStatus.INVALID
        )

        return ApprovalLineageResult(
            status=status,
            approval_request_id=(
                pending.approval_request_id
            ),
            original_run_id=original_run_id,
            resumed_run_id=resumed_run_id,
            issues=issues,
        )

    def _verify_resumed_run(
        self,
        *,
        path: Path,
        pending: PendingApproval,
        resolution: ApprovalResolution,
        original_run_id: str | None,
        issues: list[ApprovalLineageIssue],
    ) -> str | None:
        """Verify the resumed runtime evidence chain."""

        resumed_events = _read_events(path)
        resumed_run_id = _run_id(resumed_events)

        integrity = EvidenceVerifier().verify(path)

        if integrity.status is not VerificationStatus.VALID:
            _add_issue(
                issues,
                "resumed_integrity_invalid",
                "the resumed evidence chain "
                "has invalid integrity",
            )

        semantics = EvidenceSemanticsVerifier().verify(
            path
        )

        if (
            semantics.status
            is not SemanticVerificationStatus.VALID
        ):
            _add_issue(
                issues,
                "resumed_semantics_invalid",
                "the resumed evidence chain "
                "is semantically inconsistent",
            )

        if (
            original_run_id is not None
            and resumed_run_id == original_run_id
        ):
            _add_issue(
                issues,
                "resumed_run_id_reused",
                "the resumed run reused the original run ID",
            )

        metadata = _run_metadata(
            resumed_events
        )

        if metadata.get("workflow") != "approval_resume":
            _add_issue(
                issues,
                "resumed_workflow_metadata_missing",
                "the resumed run lacks approval-resume metadata",
            )

        if (
            metadata.get("approval_request_id")
            != pending.approval_request_id
        ):
            _add_issue(
                issues,
                "resumed_request_id_mismatch",
                "the resumed run refers to a different "
                "approval request",
            )

        proposal_ids = _structured_proposal_ids(
            resumed_events
        )

        if proposal_ids != {
            pending.proposal.proposal_id
        }:
            _add_issue(
                issues,
                "resumed_proposal_mismatch",
                "the resumed run contains a different proposal",
            )

        expected_approval_id = (
            resolution.approval.approval_id
            if resolution.approval is not None
            else None
        )

        approval_ids = _recorded_approval_ids(
            resumed_events
        )

        if (
            expected_approval_id is None
            or approval_ids != {
                expected_approval_id
            }
        ):
            _add_issue(
                issues,
                "resumed_approval_mismatch",
                "the resumed context contains a different "
                "approval record",
            )

        return resumed_run_id
