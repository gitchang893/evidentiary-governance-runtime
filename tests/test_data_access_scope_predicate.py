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


def make_context(
    *,
    required_document_ids: list[str] | None = None,
) -> GovernanceContext:
    return GovernanceContext(
        task_id="task-access-scope-001",
        task=(
            "Read the two organizational documents "
            "required for the audit summary."
        ),
        actor="agent-01",
        role="audit-analyst",
        authorized_purpose="internal-audit",
        declared_plan=[
            "read the required audit documents",
            "prepare the audit summary",
        ],
        history=[],
        data_context={
            "classification": "confidential",
            "required_document_ids": (
                required_document_ids
                if required_document_ids is not None
                else [
                    "D-01",
                    "D-02",
                ]
            ),
        },
        approval=None,
        policy_version="1.0",
    )


def make_proposal(
    document_ids: list[str] | None,
) -> ActionProposal:
    arguments: dict[str, Any] = {}

    if document_ids is not None:
        arguments["document_ids"] = document_ids

    return ActionProposal(
        proposal_id="proposal-access-scope-001",
        tool="read_documents",
        arguments=arguments,
        justification=(
            "Read the documents needed "
            "for the internal audit summary."
        ),
        declared_purpose="internal-audit",
    )


def make_norm(
    *,
    max_objects: Any = 3,
    object_argument: Any = "document_ids",
    required_scope_field: Any = "required_document_ids",
) -> RuntimeNorm:
    return RuntimeNorm(
        norm_id="DATA-ACCESS-SCOPE-01",
        source=(
            "Organizational Data Minimization Policy, "
            "clause 5.4"
        ),
        scope={
            "tools": [
                "read_documents",
            ],
        },
        trigger={
            "event": "tool_proposed",
        },
        predicate={
            "type": "data_access_scope_allowed",
            "object_argument": object_argument,
            "required_scope_field": required_scope_field,
            "max_objects": max_objects,
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
        priority=100,
        version="1.0",
        enabled=True,
    )


def test_required_documents_are_within_scope() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal(
            [
                "D-01",
                "D-02",
            ]
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is True

    assert result.observed == {
        "argument": "document_ids",
        "requested_object_ids": [
            "D-01",
            "D-02",
        ],
        "requested_count": 2,
        "outside_required_scope": [],
        "duplicate_object_ids": [],
    }

    assert result.expected == {
        "required_scope_field": "required_document_ids",
        "required_object_ids": [
            "D-01",
            "D-02",
        ],
        "max_objects": 3,
    }


def test_request_above_maximum_is_rejected() -> None:
    document_ids = [
        f"D-{index:02d}"
        for index in range(1, 11)
    ]

    result = PredicateEngine().evaluate(
        make_norm(max_objects=3),
        make_context(
            required_document_ids=document_ids,
        ),
        make_proposal(document_ids),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False
    assert result.observed["requested_count"] == 10
    assert result.expected["max_objects"] == 3
    assert "maximum" in result.reason


def test_object_outside_required_scope_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal(
            [
                "D-01",
                "D-99",
            ]
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False

    assert result.observed[
        "outside_required_scope"
    ] == [
        "D-99",
    ]

    assert "task-required scope" in result.reason


def test_missing_object_argument_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal(None),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False
    assert result.observed["requested_object_ids"] is None
    assert "missing" in result.reason


def test_duplicate_object_ids_are_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal(
            [
                "D-01",
                "D-01",
            ]
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False

    assert result.observed[
        "duplicate_object_ids"
    ] == [
        "D-01",
    ]

    assert "duplicate" in result.reason


@pytest.mark.parametrize(
    (
        "field_name",
        "field_value",
        "expected_message",
    ),
    [
        (
            "max_objects",
            0,
            "max_objects",
        ),
        (
            "max_objects",
            True,
            "max_objects",
        ),
        (
            "object_argument",
            "",
            "object_argument",
        ),
        (
            "required_scope_field",
            42,
            "required_scope_field",
        ),
    ],
)
def test_invalid_norm_configuration_is_rejected(
    field_name: str,
    field_value: Any,
    expected_message: str,
) -> None:
    arguments: dict[str, Any] = {
        "max_objects": 3,
        "object_argument": "document_ids",
        "required_scope_field": "required_document_ids",
    }

    arguments[field_name] = field_value

    with pytest.raises(
        PredicateEvaluationError,
        match=expected_message,
    ):
        PredicateEngine().evaluate(
            make_norm(**arguments),
            make_context(),
            make_proposal(
                [
                    "D-01",
                    "D-02",
                ]
            ),
            evaluated_at=EVALUATED_AT,
        )
