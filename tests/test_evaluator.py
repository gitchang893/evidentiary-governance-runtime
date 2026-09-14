from datetime import UTC, datetime
from typing import Any

from eg_runtime.evaluator import NormEvaluator
from eg_runtime.models import (
    ActionProposal,
    Disposition,
    GovernanceContext,
    Preservation,
    RuntimeNorm,
)


EVALUATION_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


def make_context(
    *,
    role: str = "records-operator",
) -> GovernanceContext:
    return GovernanceContext(
        task_id="task-001",
        task="Update an organizational record",
        actor="agent-01",
        role=role,
        authorized_purpose="records-maintenance",
        declared_plan=[
            "inspect record",
            "modify record",
        ],
        policy_version="1.0",
    )


def make_proposal(
    *,
    tool: str = "modify_record",
    purpose: str = "records-maintenance",
) -> ActionProposal:
    return ActionProposal(
        proposal_id="proposal-001",
        tool=tool,
        arguments={
            "record_id": "R-17",
            "updates": {
                "status": "reviewed",
            },
            "purpose": purpose,
        },
        justification="Apply the authorized record update.",
    )


def make_norm(
    norm_id: str,
    predicate: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
    trigger: dict[str, Any] | None = None,
    disposition: Disposition = Disposition.BLOCK,
    preservation: Preservation = Preservation.INCIDENT,
    priority: int = 0,
    enabled: bool = True,
    version: str = "1.0",
) -> RuntimeNorm:
    return RuntimeNorm(
        norm_id=norm_id,
        source="Test organizational policy",
        scope=scope or {},
        trigger=trigger or {"event": "tool_proposed"},
        predicate=predicate,
        disposition=disposition,
        preservation=preservation,
        priority=priority,
        version=version,
        enabled=enabled,
    )


def test_selects_only_applicable_norms() -> None:
    evaluator = NormEvaluator()

    norms = [
        make_norm(
            "APPLICABLE",
            {"type": "tool_authorized"},
            scope={"tools": ["modify_record"]},
        ),
        make_norm(
            "WRONG-TOOL",
            {"type": "tool_authorized"},
            scope={"tools": ["send_message"]},
        ),
        make_norm(
            "WRONG-TRIGGER",
            {"type": "tool_authorized"},
            trigger={"event": "tool_executed"},
        ),
        make_norm(
            "DISABLED",
            {"type": "tool_authorized"},
            enabled=False,
        ),
    ]

    selected = evaluator.select_applicable_norms(
        norms,
        make_context(),
        make_proposal(),
    )

    assert [
        norm.norm_id
        for norm in selected
    ] == ["APPLICABLE"]


def test_specificity_precedes_priority_in_norm_order() -> None:
    evaluator = NormEvaluator()

    generic = make_norm(
        "GENERIC-HIGH-PRIORITY",
        {"type": "tool_authorized"},
        scope={
            "tools": ["modify_record"],
        },
        priority=100,
    )

    specific = make_norm(
        "SPECIFIC-LOW-PRIORITY",
        {"type": "tool_authorized"},
        scope={
            "tools": ["modify_record"],
            "roles": ["records-operator"],
        },
        priority=1,
    )

    selected = evaluator.select_applicable_norms(
        [generic, specific],
        make_context(),
        make_proposal(),
    )

    assert [
        norm.norm_id
        for norm in selected
    ] == [
        "SPECIFIC-LOW-PRIORITY",
        "GENERIC-HIGH-PRIORITY",
    ]


def test_satisfied_norms_produce_default_allow() -> None:
    evaluator = NormEvaluator()

    norms = [
        make_norm(
            "TOOL-AUTH",
            {
                "type": "tool_authorized",
                "allowed_tools": ["modify_record"],
            },
        ),
        make_norm(
            "ROLE-AUTH",
            {
                "type": "role_authorized",
                "allowed_roles": ["records-operator"],
            },
        ),
    ]

    decision = evaluator.evaluate(
        norms,
        make_context(),
        make_proposal(),
        evaluated_at=EVALUATION_TIME,
    )

    assert decision.disposition is Disposition.ALLOW
    assert decision.preservation is Preservation.MINIMAL
    assert all(
        result.satisfied
        for result in decision.predicate_results
    )


def test_failed_norm_applies_block_and_incident() -> None:
    evaluator = NormEvaluator()

    norm = make_norm(
        "ROLE-BLOCK",
        {
            "type": "role_authorized",
            "allowed_roles": ["records-manager"],
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
    )

    decision = evaluator.evaluate(
        [norm],
        make_context(role="records-operator"),
        make_proposal(),
        evaluated_at=EVALUATION_TIME,
    )

    assert decision.disposition is Disposition.BLOCK
    assert decision.preservation is Preservation.INCIDENT
    assert decision.predicate_results[0].satisfied is False
    assert "ROLE-BLOCK" in decision.reasons[0]


def test_block_precedes_escalate() -> None:
    evaluator = NormEvaluator()

    escalate_norm = make_norm(
        "PURPOSE-ESCALATE",
        {"type": "purpose_match"},
        disposition=Disposition.ESCALATE,
        preservation=Preservation.FULL,
    )

    block_norm = make_norm(
        "ROLE-BLOCK",
        {
            "type": "role_authorized",
            "allowed_roles": ["records-manager"],
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.MINIMAL,
    )

    decision = evaluator.evaluate(
        [
            escalate_norm,
            block_norm,
        ],
        make_context(role="records-operator"),
        make_proposal(purpose="external-marketing"),
        evaluated_at=EVALUATION_TIME,
    )

    assert decision.disposition is Disposition.BLOCK


def test_preservation_is_resolved_independently() -> None:
    evaluator = NormEvaluator()

    block_norm = make_norm(
        "ROLE-BLOCK",
        {
            "type": "role_authorized",
            "allowed_roles": ["records-manager"],
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.MINIMAL,
    )

    incident_norm = make_norm(
        "PURPOSE-INCIDENT",
        {"type": "purpose_match"},
        disposition=Disposition.ESCALATE,
        preservation=Preservation.INCIDENT,
    )

    decision = evaluator.evaluate(
        [
            block_norm,
            incident_norm,
        ],
        make_context(role="records-operator"),
        make_proposal(purpose="external-marketing"),
        evaluated_at=EVALUATION_TIME,
    )

    assert decision.disposition is Disposition.BLOCK
    assert decision.preservation is Preservation.INCIDENT


def test_fixed_inputs_produce_same_decision_identifier() -> None:
    evaluator = NormEvaluator()

    norms = [
        make_norm(
            "TOOL-AUTH",
            {
                "type": "tool_authorized",
                "allowed_tools": ["modify_record"],
            },
        )
    ]

    first = evaluator.evaluate(
        norms,
        make_context(),
        make_proposal(),
        evaluated_at=EVALUATION_TIME,
    )

    second = evaluator.evaluate(
        norms,
        make_context(),
        make_proposal(),
        evaluated_at=EVALUATION_TIME,
    )

    assert first.decision_id == second.decision_id


def test_no_applicable_norm_uses_default_decision() -> None:
    evaluator = NormEvaluator()

    norm = make_norm(
        "MESSAGE-ONLY",
        {"type": "tool_authorized"},
        scope={
            "tools": ["send_message"],
        },
    )

    decision = evaluator.evaluate(
        [norm],
        make_context(),
        make_proposal(tool="modify_record"),
        evaluated_at=EVALUATION_TIME,
    )

    assert decision.disposition is Disposition.ALLOW
    assert decision.preservation is Preservation.MINIMAL
    assert decision.applicable_norms == []
    assert "default disposition" in decision.reasons[0]
