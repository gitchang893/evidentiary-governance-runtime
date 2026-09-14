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
    trusted_instruction_source_ids: (
        list[str] | None
    ) = None,
    include_trusted_sources: bool = True,
) -> GovernanceContext:
    data_context: dict[str, Any] = {
        "classification": "confidential",
        "content_source_ids": [
            "web-page-17",
            "investigation-record-17",
        ],
        "untrusted_content_source_ids": [
            "web-page-17",
        ],
    }

    if include_trusted_sources:
        data_context[
            "trusted_instruction_source_ids"
        ] = (
            trusted_instruction_source_ids
            if trusted_instruction_source_ids is not None
            else [
                "task-request",
            ]
        )

    return GovernanceContext(
        task_id="task-instruction-source-001",
        task=(
            "Review external content and prepare "
            "an internal investigation summary."
        ),
        actor="agent-01",
        role="investigation-analyst",
        authorized_purpose=(
            "internal-investigation-review"
        ),
        declared_plan=[
            "review the external content",
            "prepare an internal summary",
        ],
        history=[],
        data_context=data_context,
        approval=None,
        policy_version="1.0",
    )


def make_proposal(
    instruction_source_ids: list[str] | None,
) -> ActionProposal:
    arguments: dict[str, Any] = {
        "recipient": "external@example.org",
        "subject": "Investigation record",
        "body": (
            "The confidential investigation record "
            "contains a pending internal finding."
        ),
        "data_classification": "confidential",
    }

    return ActionProposal(
        proposal_id="proposal-instruction-source-001",
        tool="send_message",
        arguments=arguments,
        justification=(
            "Follow the instruction encountered while "
            "reviewing the external content."
        ),
        declared_purpose=(
            "internal-investigation-review"
        ),
        instruction_source_ids=instruction_source_ids,
    )


def make_norm(
    *,
    proposal_source_argument: Any = (
        "instruction_source_ids"
    ),
    trusted_sources_field: Any = (
        "trusted_instruction_source_ids"
    ),
) -> RuntimeNorm:
    return RuntimeNorm(
        norm_id="INSTRUCTION-SOURCE-01",
        source=(
            "Organizational Agent Instruction "
            "Provenance Policy, clause 4.1"
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
            "type": "instruction_source_authorized",
            "proposal_source_argument": (
                proposal_source_argument
            ),
            "trusted_sources_field": (
                trusted_sources_field
            ),
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
        priority=100,
        version="1.0",
        enabled=True,
    )


def test_trusted_instruction_source_is_authorized() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal(
            [
                "task-request",
            ]
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is True

    assert result.observed == {
        "argument": "instruction_source_ids",
        "declared_instruction_source_ids": [
            "task-request",
        ],
        "unauthorized_instruction_source_ids": [],
        "duplicate_instruction_source_ids": [],
    }

    assert result.expected == {
        "trusted_sources_field": (
            "trusted_instruction_source_ids"
        ),
        "trusted_instruction_source_ids": [
            "task-request",
        ],
    }


def test_untrusted_content_source_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal(
            [
                "web-page-17",
            ]
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False

    assert result.observed[
        "unauthorized_instruction_source_ids"
    ] == [
        "web-page-17",
    ]

    assert "authorized" in result.reason


def test_mixed_instruction_sources_are_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal(
            [
                "task-request",
                "web-page-17",
            ]
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False

    assert result.observed[
        "unauthorized_instruction_source_ids"
    ] == [
        "web-page-17",
    ]


def test_missing_instruction_source_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal(None),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False
    assert result.observed[
        "declared_instruction_source_ids"
    ] is None
    assert "missing" in result.reason


def test_empty_instruction_source_list_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal([]),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False
    assert "non-empty" in result.reason


def test_duplicate_instruction_sources_are_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(),
        make_proposal(
            [
                "task-request",
                "task-request",
            ]
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False

    assert result.observed[
        "duplicate_instruction_source_ids"
    ] == [
        "task-request",
    ]

    assert "duplicate" in result.reason


def test_missing_trusted_source_context_is_rejected() -> None:
    result = PredicateEngine().evaluate(
        make_norm(),
        make_context(
            include_trusted_sources=False,
        ),
        make_proposal(
            [
                "task-request",
            ]
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False

    assert result.expected[
        "trusted_instruction_source_ids"
    ] is None

    assert "trusted" in result.reason


@pytest.mark.parametrize(
    (
        "field_name",
        "field_value",
        "expected_message",
    ),
    [
        (
            "proposal_source_argument",
            "",
            "proposal_source_argument",
        ),
        (
            "trusted_sources_field",
            42,
            "trusted_sources_field",
        ),
    ],
)
def test_invalid_norm_configuration_is_rejected(
    field_name: str,
    field_value: Any,
    expected_message: str,
) -> None:
    arguments: dict[str, Any] = {
        "proposal_source_argument": (
            "instruction_source_ids"
        ),
        "trusted_sources_field": (
            "trusted_instruction_source_ids"
        ),
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
                    "task-request",
                ]
            ),
            evaluated_at=EVALUATED_AT,
        )
