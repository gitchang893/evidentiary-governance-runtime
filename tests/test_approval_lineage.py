from __future__ import annotations

import json

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from eg_runtime.approval_audit import (
    ApprovalAuditError,
    ApprovalAuditVerificationStatus,
    create_approval_audit_record,
    verify_approval_audit_record,
)
from eg_runtime.approval_lineage import (
    ApprovalLineageStatus,
    ApprovalLineageVerifier,
)
from eg_runtime.approval_workflow import (
    ApprovalWorkflow,
)
from eg_runtime.evidence import EvidenceRecorder
from eg_runtime.models import (
    ActionProposal,
    Approval,
    Disposition,
    GovernanceContext,
    Preservation,
    RuntimeNorm,
)
from eg_runtime.runtime import GovernanceMiddleware
from eg_runtime.tools import (
    SendMessageTool,
    ToolRegistry,
)


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


class IncrementingClock:
    """Return deterministic runtime timestamps."""

    def __init__(
        self,
        start: datetime,
    ) -> None:
        self._current = start

    def __call__(self) -> datetime:
        value = self._current
        self._current += timedelta(milliseconds=1)
        return value


def make_context() -> GovernanceContext:
    return GovernanceContext(
        task_id="task-lineage-001",
        task=(
            "Send the approved project update "
            "to the external partner."
        ),
        actor="agent-01",
        role="communications-operator",
        authorized_purpose=(
            "external-project-coordination"
        ),
        declared_plan=[
            "prepare the project update",
            "verify human approval",
            "send message to the external partner",
        ],
        history=[],
        data_context={
            "classification": "internal",
        },
        approval=None,
        policy_version="1.0",
    )


def make_proposal() -> ActionProposal:
    return ActionProposal(
        proposal_id="proposal-lineage-001",
        tool="send_message",
        arguments={
            "recipient": "external-partner@example.org",
            "subject": "Project status update",
            "body": (
                "The current project milestone "
                "remains on schedule."
            ),
            "data_classification": "internal",
        },
        justification=(
            "Send the project update "
            "to the external partner."
        ),
        declared_purpose=(
            "external-project-coordination"
        ),
    )


def make_norm() -> RuntimeNorm:
    return RuntimeNorm(
        norm_id="APPROVAL-REQUIRED-01",
        source=(
            "External Communications Policy, "
            "clause 4.1"
        ),
        scope={
            "tools": ["send_message"],
        },
        trigger={
            "event": "tool_proposed",
        },
        predicate={
            "type": "approval_present",
        },
        disposition=Disposition.ESCALATE,
        preservation=Preservation.FULL,
        priority=100,
        version="1.0",
        enabled=True,
    )


def make_approval(
    *,
    approval_id: str = "approval-lineage-001",
) -> Approval:
    return Approval(
        approval_id=approval_id,
        issuer="communications-manager",
        subject="agent-01",
        action="send_message",
        object_id="external-partner@example.org",
        constraints={
            "data_classification": "internal",
        },
        issued_at=BASE_TIME - timedelta(minutes=30),
        expires_at=BASE_TIME + timedelta(hours=1),
    )


def create_original_escalation(
    tmp_path: Path,
):
    messages: list[dict[str, Any]] = []
    path = tmp_path / "original.jsonl"

    result = GovernanceMiddleware(
        ToolRegistry(
            [
                SendMessageTool(messages),
            ]
        ),
        clock=IncrementingClock(BASE_TIME),
    ).process(
        context=make_context(),
        proposal=make_proposal(),
        norms=[make_norm()],
        recorder=EvidenceRecorder(
            path,
            "run-original-escalation",
        ),
    )

    workflow = ApprovalWorkflow()

    pending = workflow.create_pending(
        context=make_context(),
        proposal=make_proposal(),
        decision=result.decision,
        created_at=BASE_TIME + timedelta(minutes=1),
    )

    return path, pending


def approve(pending):
    return ApprovalWorkflow().resolve(
        pending,
        resolver="communications-manager",
        approved=True,
        approval=make_approval(),
        resolved_at=BASE_TIME + timedelta(minutes=5),
    )


def create_resumed_run(
    tmp_path: Path,
    resolution,
    *,
    request_id: str | None = None,
    context: GovernanceContext | None = None,
) -> Path:
    assert resolution.resumed_context is not None

    path = tmp_path / (
        f"resumed-{request_id or 'valid'}.jsonl"
    )

    messages: list[dict[str, Any]] = []

    GovernanceMiddleware(
        ToolRegistry(
            [
                SendMessageTool(messages),
            ]
        ),
        clock=IncrementingClock(
            BASE_TIME + timedelta(minutes=6)
        ),
    ).process(
        context=(
            context
            if context is not None
            else resolution.resumed_context
        ),
        proposal=make_proposal(),
        norms=[make_norm()],
        recorder=EvidenceRecorder(
            path,
            f"run-resumed-{request_id or 'valid'}",
        ),
        run_metadata={
            "workflow": "approval_resume",
            "approval_request_id": (
                request_id
                if request_id is not None
                else resolution.approval_request_id
            ),
        },
    )

    return path


def issue_codes(result) -> set[str]:
    return {
        issue.code
        for issue in result.issues
    }


def test_valid_approved_lineage_is_accepted(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    resolution = approve(pending)

    resumed_path = create_resumed_run(
        tmp_path,
        resolution,
    )

    result = ApprovalLineageVerifier().verify(
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        resumed_evidence_path=resumed_path,
    )

    assert result.status is ApprovalLineageStatus.VALID
    assert result.issues == []
    assert result.original_run_id is not None
    assert result.resumed_run_id is not None

    assert (
        result.original_run_id
        != result.resumed_run_id
    )


def test_wrong_request_id_in_resumed_run_is_rejected(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    resolution = approve(pending)

    resumed_path = create_resumed_run(
        tmp_path,
        resolution,
        request_id="wrong-request-id",
    )

    result = ApprovalLineageVerifier().verify(
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        resumed_evidence_path=resumed_path,
    )

    assert result.status is ApprovalLineageStatus.INVALID

    assert (
        "resumed_request_id_mismatch"
        in issue_codes(result)
    )


def test_different_approval_in_resumed_context_is_rejected(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    resolution = approve(pending)

    assert resolution.resumed_context is not None

    different_context = (
        resolution.resumed_context.model_copy(
            update={
                "approval": make_approval(
                    approval_id="approval-different-999"
                )
            }
        )
    )

    resumed_path = create_resumed_run(
        tmp_path,
        resolution,
        context=different_context,
    )

    result = ApprovalLineageVerifier().verify(
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        resumed_evidence_path=resumed_path,
    )

    assert result.status is ApprovalLineageStatus.INVALID

    assert (
        "resumed_approval_mismatch"
        in issue_codes(result)
    )


def test_rejected_resolution_without_resume_is_valid(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    resolution = ApprovalWorkflow().resolve(
        pending,
        resolver="communications-manager",
        approved=False,
        resolved_at=BASE_TIME + timedelta(minutes=5),
        reason="External communication rejected.",
    )

    result = ApprovalLineageVerifier().verify(
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
    )

    assert result.status is ApprovalLineageStatus.VALID
    assert result.resumed_run_id is None
    assert result.issues == []


def test_rejected_resolution_cannot_have_resumed_run(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    approved_resolution = approve(pending)

    resumed_path = create_resumed_run(
        tmp_path,
        approved_resolution,
    )

    rejected_resolution = ApprovalWorkflow().resolve(
        pending,
        resolver="communications-manager",
        approved=False,
        resolved_at=BASE_TIME + timedelta(minutes=5),
        reason="External communication rejected.",
    )

    result = ApprovalLineageVerifier().verify(
        original_evidence_path=original_path,
        pending=pending,
        resolution=rejected_resolution,
        resumed_evidence_path=resumed_path,
    )

    assert result.status is ApprovalLineageStatus.INVALID

    assert (
        "resume_for_non_approved_resolution"
        in issue_codes(result)
    )


def test_approved_workflow_audit_record_is_valid(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    resolution = approve(pending)

    resumed_path = create_resumed_run(
        tmp_path,
        resolution,
    )

    audit_path = tmp_path / "approved-audit.json"

    record = create_approval_audit_record(
        output_path=audit_path,
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        resumed_evidence_path=resumed_path,
    )

    verification = verify_approval_audit_record(
        audit_path,
        original_evidence_path=original_path,
        resumed_evidence_path=resumed_path,
    )

    assert (
        verification.status
        is ApprovalAuditVerificationStatus.VALID
    )

    assert verification.record_hash_valid is True
    assert verification.original_source_match is True
    assert verification.resumed_source_match is True
    assert record.approval_id == "approval-lineage-001"
    assert record.resumed_run_id is not None


def test_rejected_workflow_audit_has_no_resumed_chain(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    resolution = ApprovalWorkflow().resolve(
        pending,
        resolver="communications-manager",
        approved=False,
        resolved_at=BASE_TIME + timedelta(minutes=5),
        reason="External communication rejected.",
    )

    audit_path = tmp_path / "rejected-audit.json"

    record = create_approval_audit_record(
        output_path=audit_path,
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
    )

    verification = verify_approval_audit_record(
        audit_path,
        original_evidence_path=original_path,
    )

    assert (
        verification.status
        is ApprovalAuditVerificationStatus.VALID
    )

    assert record.resumed_run_id is None
    assert record.resumed_evidence_sha256 is None
    assert record.approval_id is None


def test_approval_audit_tampering_is_detected(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    resolution = approve(pending)

    resumed_path = create_resumed_run(
        tmp_path,
        resolution,
    )

    audit_path = tmp_path / "tampered-audit.json"

    create_approval_audit_record(
        output_path=audit_path,
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        resumed_evidence_path=resumed_path,
    )

    raw = json.loads(
        audit_path.read_text(encoding="utf-8")
    )

    raw["reason"] = "Altered human decision."

    audit_path.write_text(
        json.dumps(
            raw,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    verification = verify_approval_audit_record(
        audit_path,
        original_evidence_path=original_path,
        resumed_evidence_path=resumed_path,
    )

    assert (
        verification.status
        is ApprovalAuditVerificationStatus.INVALID
    )

    assert verification.record_hash_valid is False

    assert (
        "record_hash_mismatch"
        in verification.issues
    )


def test_substituted_resumed_chain_is_detected(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    resolution = approve(pending)

    valid_resumed_path = create_resumed_run(
        tmp_path,
        resolution,
    )

    substituted_path = create_resumed_run(
        tmp_path,
        resolution,
        request_id="substituted-request",
    )

    audit_path = tmp_path / "source-audit.json"

    create_approval_audit_record(
        output_path=audit_path,
        original_evidence_path=original_path,
        pending=pending,
        resolution=resolution,
        resumed_evidence_path=valid_resumed_path,
    )

    verification = verify_approval_audit_record(
        audit_path,
        original_evidence_path=original_path,
        resumed_evidence_path=substituted_path,
    )

    assert (
        verification.status
        is ApprovalAuditVerificationStatus.INVALID
    )

    assert verification.resumed_source_match is False

    assert (
        "resumed_source_mismatch"
        in verification.issues
    )


def test_invalid_lineage_cannot_create_audit_record(
    tmp_path: Path,
) -> None:
    original_path, pending = (
        create_original_escalation(tmp_path)
    )

    resolution = approve(pending)

    resumed_path = create_resumed_run(
        tmp_path,
        resolution,
    )

    mismatched_resolution = resolution.model_copy(
        update={
            "approval_request_id": "a" * 64,
        }
    )

    with pytest.raises(
        ApprovalAuditError,
        match="invalid approval lineage",
    ):
        create_approval_audit_record(
            output_path=tmp_path / "invalid-audit.json",
            original_evidence_path=original_path,
            pending=pending,
            resolution=mismatched_resolution,
            resumed_evidence_path=resumed_path,
        )
