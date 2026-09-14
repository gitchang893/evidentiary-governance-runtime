from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from eg_runtime.approval_workflow import (
    ApprovalResolutionStatus,
    ApprovalWorkflow,
)
from eg_runtime.evaluator import NormEvaluator
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
    """Return deterministic timestamps one millisecond apart."""

    def __init__(self) -> None:
        self._current = BASE_TIME

    def __call__(self) -> datetime:
        value = self._current
        self._current += timedelta(
            milliseconds=1
        )
        return value


def make_context() -> GovernanceContext:
    return GovernanceContext(
        task_id="task-approval-workflow-001",
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
        proposal_id="proposal-approval-workflow-001",
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


def make_decision():
    return NormEvaluator().evaluate(
        [make_norm()],
        make_context(),
        make_proposal(),
        evaluated_at=BASE_TIME,
    )


def make_pending():
    return ApprovalWorkflow().create_pending(
        context=make_context(),
        proposal=make_proposal(),
        decision=make_decision(),
        created_at=BASE_TIME,
    )


def make_valid_approval(
    *,
    object_id: str = "external-partner@example.org",
    expires_at: datetime | None = None,
) -> Approval:
    return Approval(
        approval_id="approval-external-message-001",
        issuer="communications-manager",
        subject="agent-01",
        action="send_message",
        object_id=object_id,
        constraints={
            "data_classification": "internal",
        },
        issued_at=BASE_TIME - timedelta(
            minutes=30
        ),
        expires_at=(
            expires_at
            if expires_at is not None
            else BASE_TIME + timedelta(hours=1)
        ),
    )


def test_creates_deterministic_pending_approval() -> None:
    workflow = ApprovalWorkflow()

    first = workflow.create_pending(
        context=make_context(),
        proposal=make_proposal(),
        decision=make_decision(),
        created_at=BASE_TIME,
    )

    second = workflow.create_pending(
        context=make_context(),
        proposal=make_proposal(),
        decision=make_decision(),
        created_at=BASE_TIME,
    )

    assert (
        first.approval_request_id
        == second.approval_request_id
    )

    assert (
        first.decision.disposition
        is Disposition.ESCALATE
    )


def test_valid_approval_builds_resumed_context() -> None:
    resolution = ApprovalWorkflow().resolve(
        make_pending(),
        resolver="communications-manager",
        approved=True,
        approval=make_valid_approval(),
        resolved_at=BASE_TIME + timedelta(
            minutes=5
        ),
        reason="External communication approved.",
    )

    assert (
        resolution.status
        is ApprovalResolutionStatus.APPROVED
    )

    assert resolution.can_resume is True
    assert resolution.validation_errors == []
    assert resolution.resumed_context is not None
    assert resolution.resumed_context.approval is not None

    assert (
        resolution.resumed_context.approval.approval_id
        == "approval-external-message-001"
    )

    assert (
        resolution.resumed_context.history[-1]["event"]
        == "approval_resolved"
    )


def test_rejected_resolution_cannot_resume() -> None:
    resolution = ApprovalWorkflow().resolve(
        make_pending(),
        resolver="communications-manager",
        approved=False,
        resolved_at=BASE_TIME + timedelta(
            minutes=5
        ),
        reason="External communication rejected.",
    )

    assert (
        resolution.status
        is ApprovalResolutionStatus.REJECTED
    )

    assert resolution.can_resume is False
    assert resolution.approval is None
    assert resolution.resumed_context is None


def test_wrong_approval_object_is_invalid() -> None:
    resolution = ApprovalWorkflow().resolve(
        make_pending(),
        resolver="communications-manager",
        approved=True,
        approval=make_valid_approval(
            object_id="different-partner@example.org"
        ),
        resolved_at=BASE_TIME + timedelta(
            minutes=5
        ),
    )

    assert (
        resolution.status
        is ApprovalResolutionStatus.INVALID
    )

    assert resolution.can_resume is False

    assert (
        "approval_object_mismatch"
        in resolution.validation_errors
    )


def test_expired_approval_is_invalid() -> None:
    resolution = ApprovalWorkflow().resolve(
        make_pending(),
        resolver="communications-manager",
        approved=True,
        approval=make_valid_approval(
            expires_at=BASE_TIME + timedelta(
                minutes=1
            )
        ),
        resolved_at=BASE_TIME + timedelta(
            minutes=5
        ),
    )

    assert (
        resolution.status
        is ApprovalResolutionStatus.INVALID
    )

    assert resolution.can_resume is False

    assert (
        "approval_expired"
        in resolution.validation_errors
    )


def test_approved_proposal_executes_after_runtime_reevaluation(
    tmp_path: Path,
) -> None:
    messages: list[dict[str, Any]] = []

    resolution = ApprovalWorkflow().resolve(
        make_pending(),
        resolver="communications-manager",
        approved=True,
        approval=make_valid_approval(),
        resolved_at=BASE_TIME + timedelta(
            minutes=5
        ),
    )

    assert resolution.resumed_context is not None

    middleware = GovernanceMiddleware(
        ToolRegistry(
            [
                SendMessageTool(messages),
            ]
        ),
        clock=IncrementingClock(),
    )

    result = middleware.process(
        context=resolution.resumed_context,
        proposal=make_proposal(),
        norms=[make_norm()],
        recorder=EvidenceRecorder(
            tmp_path / "resumed.jsonl",
            "run-resumed-approval",
        ),
        run_metadata={
            "workflow": "approval_resume",
            "approval_request_id": (
                resolution.approval_request_id
            ),
        },
    )

    assert (
        result.decision.disposition
        is Disposition.ALLOW
    )

    assert result.execution.executed is True
    assert result.execution.side_effect is True
    assert result.operational_outcome == "executed"
    assert len(messages) == 1

    assert (
        messages[0]["recipient"]
        == "external-partner@example.org"
    )
