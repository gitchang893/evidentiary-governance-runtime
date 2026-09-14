from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from eg_runtime.models import (
    ActionProposal,
    Disposition,
    GovernanceContext,
    Preservation,
    RuntimeNorm,
)
from eg_runtime.predicates import PredicateEngine


EVALUATED_AT = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


def make_context(
    history: list[dict[str, Any]],
) -> GovernanceContext:
    return GovernanceContext(
        task_id="task-history-001",
        task=(
            "Send the recurring internal "
            "project-status message."
        ),
        actor="agent-01",
        role="communications-operator",
        authorized_purpose="internal-project-coordination",
        declared_plan=[
            "prepare project status",
            "send message to project team",
        ],
        history=history,
        data_context={
            "classification": "internal",
        },
        approval=None,
        policy_version="1.0",
    )


def make_proposal() -> ActionProposal:
    return ActionProposal(
        proposal_id="proposal-history-001",
        tool="send_message",
        arguments={
            "recipient": "project-team@example.org",
            "subject": "Project status",
            "body": "The current milestone remains on schedule.",
            "data_classification": "internal",
        },
        justification=(
            "Send the recurring internal "
            "project-status message."
        ),
        declared_purpose="internal-project-coordination",
    )


def make_norm() -> RuntimeNorm:
    return RuntimeNorm(
        norm_id="HISTORY-LIMIT-01",
        source=(
            "Organizational Communication Policy, "
            "clause 6.1"
        ),
        scope={
            "tools": ["send_message"],
        },
        trigger={
            "event": "tool_proposed",
        },
        predicate={
            "type": "history_execution_limit",
            "max_total_executions": 3,
        },
        disposition=Disposition.BLOCK,
        preservation=Preservation.INCIDENT,
        priority=100,
        version="1.0",
        enabled=True,
    )


def history_entry(
    *,
    execution_id: str,
    tool: str = "send_message",
    executed: bool = True,
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "proposal_id": f"proposal-{execution_id}",
        "tool": tool,
        "executed": executed,
    }


def test_execution_at_limit_is_blocked() -> None:
    context = make_context(
        [
            history_entry(execution_id="001"),
            history_entry(execution_id="002"),
            history_entry(execution_id="003"),
        ]
    )

    result = PredicateEngine().evaluate(
        make_norm(),
        context,
        make_proposal(),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is False

    assert result.observed == {
        "tool": "send_message",
        "prior_executions": 3,
        "proposed_total": 4,
    }

    assert result.expected == {
        "max_total_executions": 3,
    }


def test_execution_within_limit_is_allowed() -> None:
    context = make_context(
        [
            history_entry(execution_id="001"),
            history_entry(execution_id="002"),
        ]
    )

    result = PredicateEngine().evaluate(
        make_norm(),
        context,
        make_proposal(),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is True

    assert result.observed == {
        "tool": "send_message",
        "prior_executions": 2,
        "proposed_total": 3,
    }


def test_history_count_uses_matching_completed_tool_actions() -> None:
    context = make_context(
        [
            history_entry(execution_id="001"),
            history_entry(
                execution_id="002",
                executed=False,
            ),
            history_entry(
                execution_id="003",
                tool="modify_record",
            ),
        ]
    )

    result = PredicateEngine().evaluate(
        make_norm(),
        context,
        make_proposal(),
        evaluated_at=EVALUATED_AT,
    )

    assert result.satisfied is True

    assert result.observed == {
        "tool": "send_message",
        "prior_executions": 1,
        "proposed_total": 2,
    }
