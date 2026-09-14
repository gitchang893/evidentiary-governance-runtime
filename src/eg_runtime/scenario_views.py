from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import Field

from eg_runtime.models import (
    ActionProposal,
    Approval,
    FrozenModel,
    GovernanceContext,
    RuntimeNorm,
)
from eg_runtime.scenarios import (
    SandboxState,
    ScenarioDefinition,
    ScenarioGroundTruth,
    ScenarioKind,
)


_AGENT_HIDDEN_KEYS = {
    "expected_disposition",
    "expected_preservation",
    "ground_truth",
    "kind",
    "claim",
    "required_evidence",
    "unsafe_if_executed",
    "policy_version",
    "trusted_instruction_source_ids",
    "untrusted_content_source_ids",
    "instruction_sources",
}


class InstructionSource(FrozenModel):
    """Content-bearing instruction source visible to the generator."""

    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    content: str = Field(min_length=1)


class AgentVisibleScenario(FrozenModel):
    """Scenario information that may be supplied to a proposal generator."""

    task: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    role: str = Field(min_length=1)
    authorized_purpose: str = Field(min_length=1)

    available_tools: list[str] = Field(min_length=1)

    history: list[dict[str, Any]] = Field(
        default_factory=list,
    )

    data_context: dict[str, Any] = Field(
        default_factory=dict,
    )

    approval: Approval | None = None

    instruction_sources: list[InstructionSource] = Field(
        default_factory=list,
    )

    instruction_source_ids: list[str] = Field(
        default_factory=list,
    )

    visible_state: SandboxState


class ScenarioOracle(FrozenModel):
    """Evaluator-only scenario identity and expected outcome."""

    scenario_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    kind: ScenarioKind
    claim: str = Field(min_length=1)
    required_evidence: list[str] = Field(min_length=1)
    ground_truth: ScenarioGroundTruth


class ScenarioViews(FrozenModel):
    """Separated views derived from a deterministic scenario."""

    agent_view: AgentVisibleScenario

    governance_context: GovernanceContext

    oracle: ScenarioOracle

    deterministic_proposal: ActionProposal

    norms: list[RuntimeNorm] = Field(min_length=1)

    sandbox: SandboxState


def _sanitize_agent_value(
    value: Any,
) -> Any:
    """Remove evaluator-only fields from agent-visible mappings."""

    if isinstance(value, dict):
        return {
            key: _sanitize_agent_value(item)
            for key, item in value.items()
            if key not in _AGENT_HIDDEN_KEYS
        }

    if isinstance(value, list):
        return [
            _sanitize_agent_value(item)
            for item in value
        ]

    return deepcopy(value)


def _string_values(
    value: Any,
) -> list[str]:
    """Return unique non-empty strings from a list-like value."""

    if not isinstance(value, list):
        return []

    values: list[str] = []

    for item in value:
        if not isinstance(item, str):
            continue

        normalized = item.strip()

        if normalized and normalized not in values:
            values.append(normalized)

    return values


def _explicit_instruction_sources(
    scenario: ScenarioDefinition,
) -> list[InstructionSource]:
    """Normalize content-bearing sources without exposing trust labels."""

    raw_sources = scenario.context.data_context.get(
        "instruction_sources",
    )

    if raw_sources is None:
        return []

    if not isinstance(raw_sources, list):
        raise TypeError(
            "data_context.instruction_sources must be a list"
        )

    normalized: list[InstructionSource] = []
    seen_ids: set[str] = set()

    for index, raw_source in enumerate(raw_sources):
        if not isinstance(raw_source, dict):
            raise TypeError(
                "instruction source at index "
                f"{index} must be a mapping"
            )

        source_id = raw_source.get("source_id")
        source_type = raw_source.get("source_type")
        content = raw_source.get("content")

        if not isinstance(source_id, str):
            raise TypeError(
                "instruction source_id must be a string"
            )

        if not isinstance(source_type, str):
            raise TypeError(
                "instruction source_type must be a string"
            )

        if not isinstance(content, str):
            raise TypeError(
                "instruction source content must be a string"
            )

        source_id = source_id.strip()
        source_type = source_type.strip()
        content = content.strip()

        if not source_id:
            raise ValueError(
                "instruction source_id must not be empty"
            )

        if source_id in seen_ids:
            raise ValueError(
                "duplicate instruction source_id: "
                f"{source_id}"
            )

        seen_ids.add(source_id)

        normalized.append(
            InstructionSource(
                source_id=source_id,
                source_type=source_type,
                content=content,
            )
        )

    return normalized


def _explicit_instruction_sources(
    scenario: ScenarioDefinition,
) -> list[InstructionSource]:
    """Normalize content-bearing sources without exposing trust labels."""

    raw_sources = scenario.context.data_context.get(
        "instruction_sources",
    )

    if raw_sources is None:
        return []

    if not isinstance(raw_sources, list):
        raise TypeError(
            "data_context.instruction_sources must be a list"
        )

    normalized: list[InstructionSource] = []
    seen_ids: set[str] = set()

    for index, raw_source in enumerate(raw_sources):
        if not isinstance(raw_source, dict):
            raise TypeError(
                "instruction source at index "
                f"{index} must be a mapping"
            )

        source_id = raw_source.get("source_id")
        source_type = raw_source.get("source_type")
        content = raw_source.get("content")

        if not isinstance(source_id, str):
            raise TypeError(
                "instruction source_id must be a string"
            )

        if not isinstance(source_type, str):
            raise TypeError(
                "instruction source_type must be a string"
            )

        if not isinstance(content, str):
            raise TypeError(
                "instruction source content must be a string"
            )

        source_id = source_id.strip()
        source_type = source_type.strip()
        content = content.strip()

        if not source_id:
            raise ValueError(
                "instruction source_id must not be empty"
            )

        if not source_type:
            raise ValueError(
                "instruction source_type must not be empty"
            )

        if not content:
            raise ValueError(
                "instruction source content must not be empty"
            )

        if source_id in seen_ids:
            raise ValueError(
                "duplicate instruction source_id: "
                f"{source_id}"
            )

        seen_ids.add(source_id)

        normalized.append(
            InstructionSource(
                source_id=source_id,
                source_type=source_type,
                content=content,
            )
        )

    return normalized


def _instruction_source_ids(
    scenario: ScenarioDefinition,
) -> list[str]:
    """Collect source identifiers without disclosing trust labels."""

    data_context = scenario.context.data_context

    source_ids: list[str] = []

    for field_name in (
        "content_source_ids",
        "trusted_instruction_source_ids",
        "untrusted_content_source_ids",
    ):
        for source_id in _string_values(
            data_context.get(field_name),
        ):
            if source_id not in source_ids:
                source_ids.append(source_id)

    for source_id in (
        scenario.proposal.instruction_source_ids
        or []
    ):
        normalized = source_id.strip()

        if normalized and normalized not in source_ids:
            source_ids.append(normalized)

    return sorted(source_ids)


def derive_agent_view(
    scenario: ScenarioDefinition,
) -> AgentVisibleScenario:
    """Derive an agent-visible view without oracle leakage."""

    sanitized_data_context = _sanitize_agent_value(
        scenario.context.data_context,
    )

    if not isinstance(
        sanitized_data_context,
        dict,
    ):
        raise TypeError(
            "sanitized data context must remain a mapping"
        )

    return AgentVisibleScenario(
        task=scenario.context.task,
        actor=scenario.context.actor,
        role=scenario.context.role,
        authorized_purpose=(
            scenario.context.authorized_purpose
        ),
        available_tools=[
            scenario.proposal.tool,
        ],
        history=_sanitize_agent_value(
            scenario.context.history,
        ),
        data_context=sanitized_data_context,
        approval=(
            scenario.context.approval.model_copy(
                deep=True,
            )
            if scenario.context.approval is not None
            else None
        ),
        instruction_sources=(
            _explicit_instruction_sources(scenario)
        ),
        instruction_source_ids=(
            _instruction_source_ids(scenario)
        ),
        visible_state=scenario.sandbox.model_copy(
            deep=True,
        ),
    )


def derive_oracle(
    scenario: ScenarioDefinition,
) -> ScenarioOracle:
    """Derive an evaluator-only oracle from existing scenario fields."""

    return ScenarioOracle(
        scenario_id=scenario.scenario_id,
        pair_id=scenario.pair_id,
        family=scenario.family,
        kind=scenario.kind,
        claim=scenario.claim,
        required_evidence=list(
            scenario.required_evidence,
        ),
        ground_truth=scenario.ground_truth.model_copy(
            deep=True,
        ),
    )


def derive_scenario_views(
    scenario: ScenarioDefinition,
) -> ScenarioViews:
    """Separate one existing scenario into generation and evaluation views."""

    return ScenarioViews(
        agent_view=derive_agent_view(scenario),
        governance_context=(
            scenario.context.model_copy(
                deep=True,
            )
        ),
        oracle=derive_oracle(scenario),
        deterministic_proposal=(
            scenario.proposal.model_copy(
                deep=True,
            )
        ),
        norms=[
            norm.model_copy(deep=True)
            for norm in scenario.norms
        ],
        sandbox=scenario.sandbox.model_copy(
            deep=True,
        ),
    )
