from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from eg_runtime.models import (
    ActionProposal,
    Disposition,
    GovernanceContext,
    Preservation,
    RuntimeNorm,
)
from eg_runtime.predicates import (
    PredicateEngine,
    PredicateEvaluationError,
)


EVALUATED_AT = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


def history_entry(
    *,
    execution_id: str,
    object_ids: list[str],
    acquired_under_purpose: str,
    executed: bool = True,
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "proposal_id": f"proposal-{execution_id}",
        "tool": "read_documents",
        "executed": executed,
        "accessed_object_ids": object_ids,
        "acquired_under_purpose": (
            acquired_under_purpose
        ),
    }


def make_context(
    history: list[dict[str, Any]],
) -> GovernanceContext:
    return GovernanceContext(
        task_id="task-cross-step-001",
        task=(
            "Use previously accessed documents "
            "for the authorized organizational purpose."
        ),
        actor="agent-01",
        role="audit-analyst",
        authorized_purpose="internal-audit",
        declared_plan=[
            "read the audit documents",
            "prepare the internal audit message",
        ],
        history=history,
        data_context={
            "classification": "confidential",
        },
        approval=None,
        policy_version="1.0",
    )


def make_proposal(
    *,
    declared_purpose: str | None = "internal-audit",
    source_document_ids: list[str] | None = None,
) -> ActionProposal:
    arguments: dict[str, Any] = {
        "recipient": "audit-team@example.org",
        "subject": "Audit summary",
        "body": "The audit review is complete.",
        "data_classification": "confidential",
    }

    if source_document_ids is not None:
        arguments["source_document_ids"] = (
            source_document_ids
        )

    return ActionProposal(
        proposal_id="proposal-cross-step-001",
        tool="send_message",
        arguments=arguments,
        justification=(
            "Send the authorized internal audit summary."
        ),
        declared_purpose=declared_purpose,
    )


def make_norm(
    *,
    proposal_object_argument: Any = (
        "source_document_ids"
    ),
    history_object_field: Any = "accessed_object_ids",
    history_purpose_field: Any = (
        "acquired_under_purpose"
    ),
) -> RuntimeNorm:
    return RuntimeNorm(
        norm_id="CROSS-STEP-PURPOSE-01",
        source=(
            "Organizational Data Reuse Policy, "
            "clause 7.2"
        ),
        scope={
            "tools": [
                "send_message",
            ],
        },
        trigger={
            "event": "tool_proposed",
        },
        predicate={
            "type": "cross_step_purpose_consistent",
            "proposal_object_argument": (
                proposal_object_argument
            ),
            "history_object_field": (
                history_object_field
            ),
            "history_purpose_field": (
                history_purpose_field
            ),
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
        priority=100,
        version="1.0",
        enabled=True,
    )


def test_same_purpose_reuse_is_consistent() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(
            [
                history_entry(
                    execution_id="read-001",
                    object_ids=["D-01"],
                    acquired_under_purpose=(
                        "internal-audit"
                    ),
                ),
            ]
        ),
        make_proposal(
            source_document_ids=["D-01"],
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is True

    assert result.observed == {
        "current_purpose": "internal-audit",
        "reused_object_ids": ["D-01"],
        "matched_provenance": [
            {
                "execution_id": "read-001",
                "object_ids": ["D-01"],
                "acquired_under_purpose": (
                    "internal-audit"
                ),
            },
        ],
        "missing_provenance_object_ids": [],
        "purpose_mismatch_execution_ids": [],
    }

    assert result.expected == {
        "proposal_object_argument": (
            "source_document_ids"
        ),
        "history_object_field": "accessed_object_ids",
        "history_purpose_field": (
            "acquired_under_purpose"
        ),
        "required_purpose": "internal-audit",
    }


def test_cross_purpose_reuse_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(
            [
                history_entry(
                    execution_id="read-001",
                    object_ids=["D-01"],
                    acquired_under_purpose=(
                        "internal-audit"
                    ),
                ),
            ]
        ),
        make_proposal(
            declared_purpose="external-marketing",
            source_document_ids=["D-01"],
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False
    assert result.observed[
        "purpose_mismatch_execution_ids"
    ] == [
        "read-001",
    ]
    assert "purpose" in result.reason


def test_unrelated_history_objects_are_ignored() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(
            [
                history_entry(
                    execution_id="read-001",
                    object_ids=["D-01"],
                    acquired_under_purpose=(
                        "internal-audit"
                    ),
                ),
                history_entry(
                    execution_id="read-002",
                    object_ids=["D-99"],
                    acquired_under_purpose=(
                        "external-marketing"
                    ),
                ),
            ]
        ),
        make_proposal(
            source_document_ids=["D-01"],
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is True
    assert len(
        result.observed["matched_provenance"]
    ) == 1
    assert result.observed[
        "matched_provenance"
    ][0]["execution_id"] == "read-001"


def test_missing_object_provenance_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(
            [
                history_entry(
                    execution_id="read-001",
                    object_ids=["D-01"],
                    acquired_under_purpose=(
                        "internal-audit"
                    ),
                ),
            ]
        ),
        make_proposal(
            source_document_ids=[
                "D-01",
                "D-02",
            ],
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False
    assert result.observed[
        "missing_provenance_object_ids"
    ] == [
        "D-02",
    ]
    assert "provenance" in result.reason


def test_nonexecuted_history_is_not_provenance() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(
            [
                history_entry(
                    execution_id="read-001",
                    object_ids=["D-01"],
                    acquired_under_purpose=(
                        "internal-audit"
                    ),
                    executed=False,
                ),
            ]
        ),
        make_proposal(
            source_document_ids=["D-01"],
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False
    assert result.observed[
        "missing_provenance_object_ids"
    ] == [
        "D-01",
    ]


def test_missing_proposal_object_argument_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context([]),
        make_proposal(
            source_document_ids=None,
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False
    assert result.observed["reused_object_ids"] is None
    assert "missing" in result.reason


def test_missing_current_purpose_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(
            [
                history_entry(
                    execution_id="read-001",
                    object_ids=["D-01"],
                    acquired_under_purpose=(
                        "internal-audit"
                    ),
                ),
            ]
        ),
        make_proposal(
            declared_purpose=None,
            source_document_ids=["D-01"],
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False
    assert result.observed["current_purpose"] is None
    assert "purpose" in result.reason


@pytest.mark.parametrize(
    (
        "field_name",
        "field_value",
        "expected_message",
    ),
    [
        (
            "proposal_object_argument",
            "",
            "proposal_object_argument",
        ),
        (
            "history_object_field",
            42,
            "history_object_field",
        ),
        (
            "history_purpose_field",
            "",
            "history_purpose_field",
        ),
    ],
)
def test_invalid_norm_configuration_is_rejected(
    field_name: str,
    field_value: Any,
    expected_message: str,
) -> None:
    arguments: dict[str, Any] = {
        "proposal_object_argument": (
            "source_document_ids"
        ),
        "history_object_field": (
            "accessed_object_ids"
        ),
        "history_purpose_field": (
            "acquired_under_purpose"
        ),
    }

    arguments[field_name] = field_value

    with pytest.raises(
        PredicateEvaluationError,
        match=expected_message,
    ):
        PredicateEngine().evaluate(
            make_norm(**arguments),
            make_context([]),
            make_proposal(
                source_document_ids=["D-01"],
            ),
            evaluated_at=EVALUATED_AT,
        )
