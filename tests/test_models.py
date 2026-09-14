from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from eg_runtime.models import (
    ActionProposal,
    Approval,
    Disposition,
    GovernanceContext,
    Preservation,
    RuntimeNorm,
    ToolExecutionRecord,
)


def make_approval() -> Approval:
    issued_at = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)

    return Approval(
        approval_id="approval-001",
        issuer="records-manager",
        subject="agent-01",
        action="modify_record",
        object_id="R-17",
        constraints={"status": "reviewed"},
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=2),
    )


def test_governance_models_can_be_constructed() -> None:
    approval = make_approval()

    context = GovernanceContext(
        task_id="task-001",
        task="Update the approved organizational record",
        actor="agent-01",
        role="records-operator",
        authorized_purpose="records-maintenance",
        declared_plan=[
            "inspect approval",
            "modify the approved record",
        ],
        approval=approval,
        policy_version="2026-07-22",
    )

    proposal = ActionProposal(
        proposal_id="proposal-001",
        tool="modify_record",
        arguments={
            "record_id": "R-17",
            "status": "reviewed",
        },
        justification="Apply the approved status change to R-17.",
    )

    norm = RuntimeNorm(
        norm_id="APPROVAL-BINDING-01",
        source="Records Management Policy, clause 4.2",
        scope={
            "roles": ["records-operator"],
            "tools": ["modify_record"],
        },
        trigger={"event": "tool_proposed"},
        predicate={"type": "approval_binding"},
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
        priority=100,
        version="1.0",
    )

    assert context.approval is not None
    assert context.approval.object_id == "R-17"
    assert proposal.arguments["record_id"] == "R-17"
    assert norm.disposition is Disposition.BLOCK


def test_expired_or_reversed_approval_period_is_rejected() -> None:
    issued_at = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)

    with pytest.raises(ValidationError):
        Approval(
            approval_id="approval-invalid",
            issuer="records-manager",
            subject="agent-01",
            action="modify_record",
            object_id="R-17",
            issued_at=issued_at,
            expires_at=issued_at - timedelta(minutes=1),
        )


def test_non_execution_cannot_report_side_effect() -> None:
    with pytest.raises(ValidationError):
        ToolExecutionRecord(
            execution_id="execution-001",
            proposal_id="proposal-001",
            tool="modify_record",
            executed=False,
            side_effect=True,
            arguments={"record_id": "R-17"},
        )

