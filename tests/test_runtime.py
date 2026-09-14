from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
from eg_runtime.runtime import GovernanceMiddleware
from eg_runtime.tools import (
    ModifyRecordTool,
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
    """Return deterministic timestamps one second apart."""

    def __init__(self) -> None:
        self._current = BASE_TIME

    def __call__(self) -> datetime:
        value = self._current
        self._current += timedelta(seconds=1)
        return value


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
    approval: Approval | None = None,
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
        approval=approval,
        policy_version="1.0",
    )


def make_proposal(
    *,
    record_id: str = "R-17",
    status: str = "reviewed",
    purpose: str = "records-maintenance",
) -> ActionProposal:
    return ActionProposal(
        proposal_id="proposal-001",
        tool="modify_record",
        arguments={
            "record_id": record_id,
            "updates": {
                "status": status,
            },
        },
        justification="Apply the approved record update.",
        declared_purpose=purpose,
    )

# def make_proposal(
#     *,
#     record_id: str = "R-17",
#     status: str = "reviewed",
#     purpose: str = "records-maintenance",
# ) -> ActionProposal:
#     return ActionProposal(
#         proposal_id="proposal-001",
#         tool="modify_record",
#         arguments={
#             "record_id": record_id,
#           "updates": {
#                 "status": status,
#             },
#             "purpose": purpose,
#         },
#         justification="Apply the approved record update.",
#     )


def make_norm(
    *,
    norm_id: str,
    predicate: dict[str, Any],
    disposition: Disposition,
    preservation: Preservation,
) -> RuntimeNorm:
    return RuntimeNorm(
        norm_id=norm_id,
        source="Test organizational policy",
        scope={
            "tools": ["modify_record"],
        },
        trigger={
            "event": "tool_proposed",
        },
        predicate=predicate,
        disposition=disposition,
        preservation=preservation,
        priority=100,
        version="1.0",
    )


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

    registry = ToolRegistry(
        [
            ModifyRecordTool(records),
        ]
    )

    return registry, records


def read_event_types(path: Path) -> list[str]:
    return [
        json.loads(line)["event_type"]
        for line in path.read_text(
            encoding="utf-8"
        ).splitlines()
    ]


def test_allowed_proposal_invokes_tool(
    tmp_path: Path,
) -> None:
    registry, records = make_registry()

    middleware = GovernanceMiddleware(
        registry,
        clock=IncrementingClock(),
    )

    norm = make_norm(
        norm_id="APPROVAL-BINDING-01",
        predicate={
            "type": "approval_binding",
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
    )

    evidence_path = tmp_path / "allow.jsonl"

    result = middleware.process(
        context=make_context(
            approval=make_approval(),
        ),
        proposal=make_proposal(),
        norms=[norm],
        recorder=EvidenceRecorder(
            evidence_path,
            "run-allow",
        ),
    )

    assert result.decision.disposition is Disposition.ALLOW
    assert result.execution.executed is True
    assert result.execution.side_effect is True
    assert result.operational_outcome == "executed"
    assert records["R-17"]["status"] == "reviewed"
    assert registry.call_count("modify_record") == 1

    verification = EvidenceVerifier().verify(evidence_path)

    assert verification.status is VerificationStatus.VALID
    assert "tool_invoked" in read_event_types(evidence_path)
    assert "tool_completed" in read_event_types(evidence_path)


def test_approval_mismatch_blocks_without_invocation(
    tmp_path: Path,
) -> None:
    registry, records = make_registry()

    middleware = GovernanceMiddleware(
        registry,
        clock=IncrementingClock(),
    )

    norm = make_norm(
        norm_id="APPROVAL-BINDING-01",
        predicate={
            "type": "approval_binding",
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
    )

    evidence_path = tmp_path / "blocked.jsonl"

    result = middleware.process(
        context=make_context(
            approval=make_approval(),
        ),
        proposal=make_proposal(
            record_id="R-42",
            status="deleted",
        ),
        norms=[norm],
        recorder=EvidenceRecorder(
            evidence_path,
            "run-blocked",
        ),
    )

    assert result.decision.disposition is Disposition.BLOCK
    assert result.execution.executed is False
    assert result.execution.side_effect is False
    assert result.operational_outcome == "blocked"
    assert records["R-42"]["status"] == "pending"
    assert registry.call_count("modify_record") == 0

    event_types = read_event_types(evidence_path)

    assert "tool_blocked" in event_types
    assert "tool_invoked" not in event_types
    assert EvidenceVerifier().verify(
        evidence_path
    ).status is VerificationStatus.VALID


def test_escalation_creates_non_execution_record(
    tmp_path: Path,
) -> None:
    registry, records = make_registry()

    middleware = GovernanceMiddleware(
        registry,
        clock=IncrementingClock(),
    )

    norm = make_norm(
        norm_id="PURPOSE-ESCALATION-01",
        predicate={
            "type": "purpose_match",
        },
        disposition=Disposition.ESCALATE,
        preservation=Preservation.FULL,
    )

    evidence_path = tmp_path / "escalated.jsonl"

    result = middleware.process(
        context=make_context(),
        proposal=make_proposal(
            purpose="external-marketing",
        ),
        norms=[norm],
        recorder=EvidenceRecorder(
            evidence_path,
            "run-escalated",
        ),
    )

    assert result.decision.disposition is Disposition.ESCALATE
    assert result.execution.executed is False
    assert result.operational_outcome == "escalated"
    assert records["R-17"]["status"] == "pending"
    assert registry.call_count("modify_record") == 0

    event_types = read_event_types(evidence_path)

    assert "approval_required" in event_types
    assert "tool_invoked" not in event_types
    assert EvidenceVerifier().verify(
        evidence_path
    ).status is VerificationStatus.VALID


def test_runtime_evidence_contains_decision_and_terminal_event(
    tmp_path: Path,
) -> None:
    registry, _ = make_registry()

    middleware = GovernanceMiddleware(
        registry,
        clock=IncrementingClock(),
    )

    norm = make_norm(
        norm_id="ROLE-CONTROL-01",
        predicate={
            "type": "role_authorized",
            "allowed_roles": ["records-manager"],
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
    )

    evidence_path = tmp_path / "evidence.jsonl"

    result = middleware.process(
        context=make_context(
            role="records-operator",
        ),
        proposal=make_proposal(),
        norms=[norm],
        recorder=EvidenceRecorder(
            evidence_path,
            "run-evidence",
        ),
    )

    event_types = read_event_types(evidence_path)

    assert event_types == [
        "run_started",
        "context_loaded",
        "proposal_created",
        "norms_selected",
        "governance_decision",
        "tool_blocked",
        "run_completed",
    ]

    assert result.evidence_event_count == 7
    assert len(result.evidence_final_hash) == 64
