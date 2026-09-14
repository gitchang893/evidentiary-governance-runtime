from __future__ import annotations

import hashlib
import json
from typing import Protocol

from pydantic import Field

from eg_runtime.models import ActionProposal, FrozenModel
from eg_runtime.scenarios import ScenarioDefinition

from .tools import (
    ModifyRecordArgs,
    SendMessageArgs,
)

class ProposalGenerationResult(FrozenModel):
    """Plan and proposal produced once for a comparative scenario run."""

    generation_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    generator_name: str = Field(min_length=1)
    declared_plan: list[str] = Field(min_length=1)
    proposal: ActionProposal


class ProposalGenerator(Protocol):
    """Generate one plan and proposal before C0--C3 replay."""

    def generate(
        self,
        scenario: ScenarioDefinition,
    ) -> ProposalGenerationResult:
        """Generate one input shared by all governance conditions."""
        ...


class DeterministicProposalGenerator:
    """Return the proposal already declared by the scenario."""

    name = "deterministic-scenario-proposal"

    def generate(
        self,
        scenario: ScenarioDefinition,
    ) -> ProposalGenerationResult:
        declared_plan = list(
            scenario.context.declared_plan
        )

        proposal = scenario.proposal.model_copy(
            deep=True
        )

        canonical_payload = json.dumps(
            {
                "scenario_id": scenario.scenario_id,
                "declared_plan": declared_plan,
                "proposal": proposal.model_dump(
                    mode="json"
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        generation_id = hashlib.sha256(
            canonical_payload.encode("utf-8")
        ).hexdigest()

        return ProposalGenerationResult(
            generation_id=generation_id,
            generator_name=self.name,
            declared_plan=declared_plan,
            proposal=proposal,
        )

# Proposal-generation request and prompt boundary

from copy import deepcopy as _deepcopy
from typing import Any as _Any

from eg_runtime.scenario_views import (
    AgentVisibleScenario,
    derive_agent_view,
)


class ProposalGenerationRequest(FrozenModel):
    """Agent-visible input for one proposal-generation attempt."""

    trial_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    repetition_index: int = Field(
        ge=0,
    )

    agent_view: AgentVisibleScenario

    tool_schemas: list[dict[str, _Any]] = Field(
        default_factory=list,
    )


_FORBIDDEN_PROMPT_KEYS = {
    "scenario_id",
    "pair_id",
    "family",
    "kind",
    "claim",
    "required_evidence",
    "ground_truth",
    "expected_disposition",
    "expected_preservation",
    "unsafe_if_executed",
    "trusted_instruction_source_ids",
    "untrusted_content_source_ids",
    "generation_id",
    "generator_name",
    "trial_id",
    "repetition_index",
}


def _mapping_keys(
    value: _Any,
) -> set[str]:
    """Collect mapping keys recursively."""

    keys: set[str] = set()

    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(
                _mapping_keys(item)
            )

    elif isinstance(value, list):
        for item in value:
            keys.update(
                _mapping_keys(item)
            )

    return keys


def _default_tool_schemas(
    agent_view: AgentVisibleScenario,
) -> list[dict[str, _Any]]:
    """Create executable argument schemas for visible tools."""

    from eg_runtime.tool_contracts import (
        tool_argument_schema,
    )

    schemas: list[dict[str, _Any]] = []

    for tool_name in agent_view.available_tools:
        argument_schema = tool_argument_schema(
            tool_name
        )

        if argument_schema is None:
            raise ValueError(
                "no executable argument schema "
                f"is registered for tool: {tool_name}"
            )

        schemas.append(
            {
                "name": tool_name,
                "arguments": argument_schema,
            }
        )

    return schemas

def build_proposal_generation_request(
    scenario: ScenarioDefinition,
    *,
    experiment_id: str,
    repetition_index: int,
    tool_schemas: list[dict[str, _Any]] | None = None,
) -> ProposalGenerationRequest:
    """Build an opaque, oracle-free request from an existing scenario."""

    normalized_experiment_id = experiment_id.strip()

    if not normalized_experiment_id:
        raise ValueError(
            "experiment_id must not be empty"
        )

    if repetition_index < 0:
        raise ValueError(
            "repetition_index must not be negative"
        )

    agent_view = derive_agent_view(
        scenario
    )

    trial_material = json.dumps(
        {
            "experiment_id": normalized_experiment_id,
            "scenario_id": scenario.scenario_id,
            "repetition_index": repetition_index,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    trial_id = hashlib.sha256(
        trial_material.encode("utf-8")
    ).hexdigest()

    selected_tool_schemas = (
        _default_tool_schemas(agent_view)
        if tool_schemas is None
        else _deepcopy(tool_schemas)
    )

    return ProposalGenerationRequest(
        trial_id=trial_id,
        repetition_index=repetition_index,
        agent_view=agent_view,
        tool_schemas=selected_tool_schemas,
    )


def proposal_prompt_payload(
    request: ProposalGenerationRequest,
) -> dict[str, _Any]:
    """Return only the fields that may be serialized into an LLM prompt."""

    payload: dict[str, _Any] = {
        "agent_view": request.agent_view.model_dump(
            mode="json"
        ),
        "tool_schemas": _deepcopy(
            request.tool_schemas
        ),
    }

    leaked_keys = (
        _mapping_keys(payload)
        & _FORBIDDEN_PROMPT_KEYS
    )

    if leaked_keys:
        raise ValueError(
            "proposal prompt contains evaluator-only keys: "
            + ", ".join(sorted(leaked_keys))
        )

    return payload


def canonical_prompt_payload_json(
    request: ProposalGenerationRequest,
) -> str:
    """Serialize the prompt payload deterministically."""

    return json.dumps(
        proposal_prompt_payload(request),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

# Request-aware generator dispatch


class RequestProposalGenerator(Protocol):
    """Generate a proposal from an oracle-free request."""

    def generate_request(
        self,
        request: ProposalGenerationRequest,
    ) -> ProposalGenerationResult:
        """Generate one proposal for the supplied trial."""
        ...


def generate_proposal(
    generator: ProposalGenerator | RequestProposalGenerator,
    *,
    scenario: ScenarioDefinition,
    request: ProposalGenerationRequest,
) -> ProposalGenerationResult:
    """Dispatch to a request-aware or legacy deterministic generator."""

    request_method = getattr(
        generator,
        "generate_request",
        None,
    )

    if callable(request_method):
        result = request_method(request)

        from eg_runtime.generation_failures import (
            ProposalGenerationSchemaError,
        )
        from eg_runtime.tool_contracts import (
            validate_tool_arguments,
        )

        argument_errors = validate_tool_arguments(
            result.proposal.tool,
            result.proposal.arguments,
        )

        if argument_errors:
            raise ProposalGenerationSchemaError(
                "generated tool arguments failed validation",
                details={
                    "tool": result.proposal.tool,
                    "errors": argument_errors,
                },
            )
    else:
        legacy_method = getattr(
            generator,
            "generate",
            None,
        )

        if not callable(legacy_method):
            raise TypeError(
                "proposal generator must implement "
                "generate_request(request) or generate(scenario)"
            )

        result = legacy_method(scenario)

    if not isinstance(
        result,
        ProposalGenerationResult,
    ):
        raise TypeError(
            "proposal generator returned an invalid result"
        )

    return result

