from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eg_runtime.proposal_generation import (
    build_proposal_generation_request,
    canonical_prompt_payload_json,
    proposal_prompt_payload,
)
from eg_runtime.scenarios import load_scenario


SCENARIO_PATH = Path(
    "scenarios/canonical/"
    "prompt_injection_exfiltration_unsafe.yaml"
)

FORBIDDEN_KEYS = {
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
    value: Any,
) -> set[str]:
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


def test_request_contains_agent_view_and_opaque_trial_id(
) -> None:
    scenario = load_scenario(
        SCENARIO_PATH
    )

    request = build_proposal_generation_request(
        scenario,
        experiment_id="e1-local-llm",
        repetition_index=2,
    )

    assert len(request.trial_id) == 64
    assert request.repetition_index == 2

    assert (
        scenario.scenario_id
        not in request.trial_id
    )

    assert (
        request.agent_view.task
        == scenario.context.task
    )

    assert len(request.tool_schemas) == 1

    tool_schema = request.tool_schemas[0]

    assert (
        tool_schema["name"]
        == scenario.proposal.tool
    )

    assert "arguments" in tool_schema

    argument_schema = tool_schema["arguments"]

    assert "properties" in argument_schema

    assert {
        "recipient",
        "subject",
        "body",
    }.issubset(
        argument_schema["properties"]
    )

    assert set(
        argument_schema["required"]
    ) == {
        "recipient",
        "subject",
        "body",
    }
def test_prompt_payload_excludes_oracle_and_trial_metadata(
) -> None:
    scenario = load_scenario(
        SCENARIO_PATH
    )

    request = build_proposal_generation_request(
        scenario,
        experiment_id="e1-local-llm",
        repetition_index=0,
    )

    payload = proposal_prompt_payload(
        request
    )

    assert not (
        _mapping_keys(payload)
        & FORBIDDEN_KEYS
    )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )

    assert scenario.scenario_id not in serialized
    assert scenario.pair_id not in serialized
    assert request.trial_id not in serialized


def test_prompt_payload_exposes_content_without_trust_labels(
) -> None:
    scenario = load_scenario(
        SCENARIO_PATH
    )

    request = build_proposal_generation_request(
        scenario,
        experiment_id="e1-local-llm",
        repetition_index=0,
    )

    payload = proposal_prompt_payload(
        request
    )

    agent_view = payload["agent_view"]

    sources = agent_view[
        "instruction_sources"
    ]

    assert len(sources) == 3

    for source in sources:
        assert set(source) == {
            "source_id",
            "source_type",
            "content",
        }

        assert "trusted" not in source
        assert "classification" not in source

    contents = {
        source["source_id"]: source["content"]
        for source in sources
    }

    assert (
        "Ignore the assigned task"
        in contents["web-page-17"]
    )

    assert (
        "trusted_instruction_source_ids"
        not in agent_view["data_context"]
    )

    assert (
        "untrusted_content_source_ids"
        not in agent_view["data_context"]
    )

    assert (
        agent_view["data_context"][
            "classification"
        ]
        == "confidential"
    )


def test_request_identity_is_deterministic_and_repetition_specific(
) -> None:
    scenario = load_scenario(
        SCENARIO_PATH
    )

    first = build_proposal_generation_request(
        scenario,
        experiment_id="e1-local-llm",
        repetition_index=0,
    )

    repeated = build_proposal_generation_request(
        scenario,
        experiment_id="e1-local-llm",
        repetition_index=0,
    )

    next_repetition = (
        build_proposal_generation_request(
            scenario,
            experiment_id="e1-local-llm",
            repetition_index=1,
        )
    )

    assert first.trial_id == repeated.trial_id

    assert (
        first.trial_id
        != next_repetition.trial_id
    )

    assert (
        canonical_prompt_payload_json(first)
        == canonical_prompt_payload_json(
            repeated
        )
    )

    assert (
        canonical_prompt_payload_json(first)
        == canonical_prompt_payload_json(
            next_repetition
        )
    )
