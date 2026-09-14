from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from eg_runtime.conditions import (
    ComparativeConditionRunner,
    DecisionPhase,
    GovernanceCondition,
)
from eg_runtime.evidence import (
    EvidenceRecorder,
    EvidenceVerifier,
    VerificationStatus,
)
from eg_runtime.models import (
    ActionProposal,
    Approval,
    Disposition,
    GovernanceContext,
    Preservation,
    RuntimeNorm,
)
from eg_runtime.tools import ModifyRecordTool, ToolRegistry


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


class IncrementingClock:
    """Return deterministic timestamps one second apart."""

    def __init__(self) -> None:
        self._current = BASE_TIME

    def __call__(self) -> datetime:
        value = self._current
        self._current += timedelta(seconds=1)
        return value


def make_registry() -> tuple[
    ToolRegistry,
    dict[str, dict[str, Any]],
]:
    records = {
        "R-17": {
            "status": "pending",
        },
        "R-42": {
            "status": "pending",
        },
    }

    return (
        ToolRegistry(
            [
                ModifyRecordTool(records),
            ]
        ),
        records,
    )


def make_approval() -> Approval:
    return Approval(
        approval_id="approval-001",
        issuer="records-manager",
        subject="agent-01",
        action="modify_record",
        object_id="R-17",
        constraints={
            "status": "reviewed",
        },
        issued_at=BASE_TIME - timedelta(hours=1),
        expires_at=BASE_TIME + timedelta(hours=2),
    )


def make_context(
    *,
    role: str = "records-operator",
) -> GovernanceContext:
    return GovernanceContext(
        task_id="task-001",
        task="Update the approved organizational record",
        actor="agent-01",
        role=role,
        authorized_purpose="records-maintenance",
        declared_plan=[
            "inspect approval",
            "modify record",
        ],
        history=[],
        data_context={
            "classification": "internal",
        },
        approval=make_approval(),
        policy_version="1.0",
    )


def make_mismatched_proposal() -> ActionProposal:
    return ActionProposal(
        proposal_id="proposal-001",
        tool="modify_record",
        arguments={
            "record_id": "R-42",
            "updates": {
                "status": "deleted",
            },
        },
        justification="Apply the approved record update.",
        declared_purpose="records-maintenance",
    )


def make_approval_binding_norm() -> RuntimeNorm:
    return RuntimeNorm(
        norm_id="APPROVAL-BINDING-01",
        source="Records Management Policy",
        scope={
            "tools": ["modify_record"],
        },
        trigger={
            "event": "tool_proposed",
        },
        predicate={
            "type": "approval_binding",
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
        priority=100,
        version="1.0",
    )


def make_role_norm() -> RuntimeNorm:
    return RuntimeNorm(
        norm_id="ROLE-CONTROL-01",
        source="Records Management Policy",
        scope={
            "tools": ["modify_record"],
        },
        trigger={
            "event": "tool_proposed",
        },
        predicate={
            "type": "role_authorized",
            "allowed_roles": ["records-manager"],
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
        priority=100,
        version="1.0",
    )


def run_condition(
    *,
    tmp_path: Path,
    condition: GovernanceCondition,
    context: GovernanceContext,
    norms: list[RuntimeNorm],
) -> tuple[
    object,
    ToolRegistry,
    dict[str, dict[str, Any]],
    Path,
]:
    registry, records = make_registry()

    runner = ComparativeConditionRunner(
        registry,
        clock=IncrementingClock(),
    )

    path = tmp_path / f"{condition.value}.jsonl"

    result = runner.run(
        condition=condition,
        context=context,
        proposal=make_mismatched_proposal(),
        norms=norms,
        recorder=EvidenceRecorder(
            path,
            f"run-{condition.value}",
        ),
    )

    return result, registry, records, path


def test_c0_executes_approval_mismatch(
    tmp_path: Path,
) -> None:
    result, registry, records, path = run_condition(
        tmp_path=tmp_path,
        condition=GovernanceCondition.C0_LOGGING,
        context=make_context(),
        norms=[make_approval_binding_norm()],
    )

    assert result.decision is None
    assert result.decision_phase is DecisionPhase.NONE
    assert result.execution.executed is True
    assert result.violation_detected is False
    assert result.execution_prevented is False
    assert records["R-42"]["status"] == "deleted"
    assert registry.call_count("modify_record") == 1
    assert (
        EvidenceVerifier().verify(path).status
        is VerificationStatus.VALID
    )


def test_c1_detects_violation_after_execution(
    tmp_path: Path,
) -> None:
    result, registry, records, path = run_condition(
        tmp_path=tmp_path,
        condition=GovernanceCondition.C1_POST_HOC,
        context=make_context(),
        norms=[make_approval_binding_norm()],
    )

    assert result.decision is not None
    assert result.decision.disposition is Disposition.BLOCK
    assert result.decision_phase is DecisionPhase.POST_EXECUTION
    assert result.execution.executed is True
    assert result.violation_detected is True
    assert result.execution_prevented is False
    assert records["R-42"]["status"] == "deleted"
    assert registry.call_count("modify_record") == 1
    assert (
        EvidenceVerifier().verify(path).status
        is VerificationStatus.VALID
    )


def test_c2_allows_contextual_approval_mismatch(
    tmp_path: Path,
) -> None:
    result, registry, records, _ = run_condition(
        tmp_path=tmp_path,
        condition=GovernanceCondition.C2_ACTION_LEVEL,
        context=make_context(),
        norms=[make_approval_binding_norm()],
    )

    assert result.decision is not None
    assert result.decision.disposition is Disposition.ALLOW
    assert result.decision_phase is DecisionPhase.PRE_EXECUTION
    assert result.execution.executed is True
    assert result.violation_detected is False
    assert result.execution_prevented is False
    assert records["R-42"]["status"] == "deleted"
    assert registry.call_count("modify_record") == 1


def test_c3_blocks_contextual_approval_mismatch(
    tmp_path: Path,
) -> None:
    result, registry, records, path = run_condition(
        tmp_path=tmp_path,
        condition=GovernanceCondition.C3_ORGANIZATIONAL,
        context=make_context(),
        norms=[make_approval_binding_norm()],
    )

    assert result.decision is not None
    assert result.decision.disposition is Disposition.BLOCK
    assert result.decision_phase is DecisionPhase.PRE_EXECUTION
    assert result.execution.executed is False
    assert result.violation_detected is True
    assert result.execution_prevented is True
    assert records["R-42"]["status"] == "pending"
    assert registry.call_count("modify_record") == 0
    assert (
        EvidenceVerifier().verify(path).status
        is VerificationStatus.VALID
    )


def test_c2_blocks_action_level_role_violation(
    tmp_path: Path,
) -> None:
    result, registry, records, _ = run_condition(
        tmp_path=tmp_path,
        condition=GovernanceCondition.C2_ACTION_LEVEL,
        context=make_context(
            role="records-operator",
        ),
        norms=[make_role_norm()],
    )

    assert result.decision is not None
    assert result.decision.disposition is Disposition.BLOCK
    assert result.execution.executed is False
    assert result.violation_detected is True
    assert result.execution_prevented is True
    assert records["R-42"]["status"] == "pending"
    assert registry.call_count("modify_record") == 0
