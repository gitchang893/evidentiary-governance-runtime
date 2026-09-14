from __future__ import annotations

from pathlib import Path


PATH = Path(
    "src/eg_runtime/proposal_generation.py"
)

MARKER = (
    "# Proposal-generation request and prompt boundary"
)

ADDITION = r'''

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
    """Create minimal schema references for currently visible tools."""

    return [
        {
            "name": tool_name,
        }
        for tool_name in agent_view.available_tools
    ]


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
'''


def main() -> None:
    source = PATH.read_text(
        encoding="utf-8"
    )

    if MARKER in source:
        print(
            "ProposalGenerationRequest boundary "
            "already present"
        )
        return

    if "class ProposalGenerationResult" not in source:
        raise SystemExit(
            "ProposalGenerationResult was not found"
        )

    if "ScenarioDefinition" not in source:
        raise SystemExit(
            "ScenarioDefinition import was not found"
        )

    PATH.write_text(
        source.rstrip() + ADDITION + "\n",
        encoding="utf-8",
    )

    print(
        "ProposalGenerationRequest boundary added"
    )


if __name__ == "__main__":
    main()
