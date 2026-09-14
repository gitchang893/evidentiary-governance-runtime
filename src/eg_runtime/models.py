from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class FrozenModel(BaseModel):
    """Base class for immutable, schema-strict runtime objects."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class Disposition(StrEnum):
    """Operational outcome produced by norm evaluation."""

    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


class Preservation(StrEnum):
    """Evidence-preservation directive produced by norm evaluation."""

    MINIMAL = "minimal"
    FULL = "full"
    INCIDENT = "incident"


class Approval(FrozenModel):
    """Structured authorization bound to a specific action and object."""

    approval_id: str = Field(min_length=1)
    issuer: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    action: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    constraints: dict[str, Any] = Field(default_factory=dict)
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_validity_period(self) -> Approval:
        """Require timezone-aware timestamps and a valid approval interval."""
        timestamps = {
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

        for field_name, value in timestamps.items():
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")

        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")

        return self


class GovernanceContext(FrozenModel):
    """Typed organization-level context for governance step t."""

    task_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    role: str = Field(min_length=1)
    authorized_purpose: str = Field(min_length=1)
    declared_plan: list[str] = Field(min_length=1)
    history: list[dict[str, Any]] = Field(default_factory=list)
    data_context: dict[str, Any] = Field(default_factory=dict)
    approval: Approval | None = None
    policy_version: str = Field(min_length=1)

class ActionProposal(FrozenModel):
    """Schema-valid action proposal produced by the agent."""

    proposal_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any]
    justification: str = Field(min_length=1)
    declared_purpose: str | None = None
    instruction_source_ids: list[str] | None = None

 
#class ActionProposal(FrozenModel):
#    """Schema-valid action proposal produced by the agent."""
#
#    proposal_id: str = Field(min_length=1)
#    tool: str = Field(min_length=1)
#    arguments: dict[str, Any]
#    justification: str = Field(min_length=1)


class RuntimeNorm(FrozenModel):
    """Machine-readable runtime norm."""

    norm_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    scope: dict[str, Any]
    trigger: dict[str, Any]
    predicate: dict[str, Any]
    disposition: Disposition
    preservation: Preservation
    priority: int = Field(default=0)
    version: str = Field(min_length=1)
    enabled: bool = True


class PredicateResult(FrozenModel):
    """Result of evaluating one predicate under one runtime norm."""

    norm_id: str = Field(min_length=1)
    predicate_type: str = Field(min_length=1)
    satisfied: bool
    observed: Any = None
    expected: Any = None
    reason: str = Field(min_length=1)


class DecisionRecord(FrozenModel):
    """Structured output of the norm-evaluation function."""

    decision_id: str = Field(min_length=1)
    disposition: Disposition
    preservation: Preservation
    applicable_norms: list[str] = Field(default_factory=list)
    predicate_results: list[PredicateResult] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=utc_now)


class ToolExecutionRecord(FrozenModel):
    """Observed execution or explicit non-execution of a tool proposal."""

    execution_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    executed: bool
    side_effect: bool
    arguments: dict[str, Any]
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_execution_interval(self) -> ToolExecutionRecord:
        """Require a chronologically valid execution interval."""
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must be equal to or later than started_at")

        if not self.executed and self.side_effect:
            raise ValueError("a non-executed tool call cannot report a side effect")

        return self
    
