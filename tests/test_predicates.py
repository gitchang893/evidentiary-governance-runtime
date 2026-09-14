from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from eg_runtime.models import (
    ActionProposal,
    Approval,
    Disposition,
    GovernanceContext,
    Preservation,
    RuntimeNorm,
)
from eg_runtime.predicates import (
    PredicateEngine,
    PredicateEvaluationError,
)


EVALUATION_TIME = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)


def make_approval(
    *,
    object_id: str = "R-17",
    status: str = "reviewed",
    expires_at: datetime | None = None,
) -> Approval:
    issued_at = EVALUATION_TIME - timedelta(hours=1)

    return Approval(
        approval_id="approval-001",
        issuer="records-manager",
        subject="agent-01",
        action="modify_record",
        object_id=object_id,
        constraints={"status": status},
        issued_at=issued_at,
        expires_at=(
            expires_at
            if expires_at is not None
            else EVALUATION_TIME + timedelta(hours=1)
        ),
    )


def make_context(
    *,
    role: str = "records-operator",
    approval: Approval | None = None,
    declared_plan: list[str] | None = None,
) -> GovernanceContext:
    return GovernanceContext(
        task_id="task-001",
        task="Update an approved organizational record",
        actor="agent-01",
        role=role,
        authorized_purpose="records-maintenance",
        declared_plan=(
            declared_plan
            if declared_plan is not None
            else [
                "inspect approval",
                "modify record",
            ]
        ),
        approval=approval,
        policy_version="1.0",
    )


def make_proposal(
    *,
    record_id: str = "R-17",
    status: str = "reviewed",
    tool: str = "modify_record",
    extra_arguments: dict[str, Any] | None = None,
) -> ActionProposal:
    arguments: dict[str, Any] = {
        "record_id": record_id,
        "updates": {
            "status": status,
        },
    }

    if extra_arguments:
        arguments.update(extra_arguments)

    return ActionProposal(
        proposal_id="proposal-001",
        tool=tool,
        arguments=arguments,
        justification="Apply the approved record update.",
    )


def make_norm(
    predicate: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
) -> RuntimeNorm:
    return RuntimeNorm(
        norm_id="TEST-NORM-01",
        source="Test organizational policy",
        scope=scope or {},
        trigger={"event": "tool_proposed"},
        predicate=predicate,
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
        priority=100,
        version="1.0",
    )


def test_tool_authorization_succeeds() -> None:
    engine = PredicateEngine()
    norm = make_norm(
        {"type": "tool_authorized"},
        scope={"tools": ["modify_record"]},
    )

    result = engine.evaluate(
        norm,
        make_context(),
        make_proposal(),
        EVALUATION_TIME,
    )

    assert result.satisfied is True
    assert result.observed == "modify_record"


def test_role_authorization_detects_unauthorized_role() -> None:
    engine = PredicateEngine()
    norm = make_norm(
        {"type": "role_authorized"},
        scope={"roles": ["records-manager"]},
    )

    result = engine.evaluate(
        norm,
        make_context(role="records-operator"),
        make_proposal(),
        EVALUATION_TIME,
    )

    assert result.satisfied is False
    assert "outside the authorized set" in result.reason


def test_approval_binding_succeeds_for_exact_proposal() -> None:
    engine = PredicateEngine()
    norm = make_norm({"type": "approval_binding"})

    result = engine.evaluate(
        norm,
        make_context(approval=make_approval()),
        make_proposal(),
        EVALUATION_TIME,
    )

    assert result.satisfied is True
    assert result.reason == "approval is bound to the complete proposal"


def test_approval_binding_detects_object_mismatch() -> None:
    engine = PredicateEngine()
    norm = make_norm({"type": "approval_binding"})

    result = engine.evaluate(
        norm,
        make_context(approval=make_approval(object_id="R-17")),
        make_proposal(record_id="R-42"),
        EVALUATION_TIME,
    )

    assert result.satisfied is False
    assert "object" in result.reason


def test_approval_binding_detects_constraint_mismatch() -> None:
    engine = PredicateEngine()
    norm = make_norm({"type": "approval_binding"})

    result = engine.evaluate(
        norm,
        make_context(approval=make_approval(status="reviewed")),
        make_proposal(status="deleted"),
        EVALUATION_TIME,
    )

    assert result.satisfied is False
    assert "constraint:status" in result.reason


def test_approval_presence_detects_expired_approval() -> None:
    engine = PredicateEngine()
    norm = make_norm({"type": "approval_present"})
    expired_at = EVALUATION_TIME - timedelta(minutes=1)
    approval = make_approval(expires_at=expired_at)

    result = engine.evaluate(
        norm,
        make_context(approval=approval),
        make_proposal(),
        EVALUATION_TIME,
    )

    assert result.satisfied is False
    assert "validity period" in result.reason


def test_purpose_match_detects_purpose_drift() -> None:
    engine = PredicateEngine()
    norm = make_norm({"type": "purpose_match"})
    proposal = make_proposal(
        extra_arguments={
            "purpose": "external-marketing",
        }
    )

    result = engine.evaluate(
        norm,
        make_context(),
        proposal,
        EVALUATION_TIME,
    )

    assert result.satisfied is False
    assert result.observed == "external-marketing"
    assert result.expected == "records-maintenance"


def test_plan_tool_match_accepts_declared_tool() -> None:
    engine = PredicateEngine()
    norm = make_norm({"type": "plan_tool_match"})

    result = engine.evaluate(
        norm,
        make_context(
            declared_plan=[
                "inspect approval",
                "modify record",
            ]
        ),
        make_proposal(tool="modify_record"),
        EVALUATION_TIME,
    )

    assert result.satisfied is True


def test_unknown_predicate_is_rejected() -> None:
    engine = PredicateEngine()
    norm = make_norm({"type": "unknown_predicate"})

    with pytest.raises(PredicateEvaluationError):
        engine.evaluate(
            norm,
            make_context(),
            make_proposal(),
            EVALUATION_TIME,
        )
