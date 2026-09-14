from __future__ import annotations

from pathlib import Path
from typing import Any

from eg_runtime.scenario_views import (
    derive_agent_view,
    derive_oracle,
    derive_scenario_views,
)
from eg_runtime.scenarios import (
    ScenarioKind,
    load_scenario,
    load_scenarios,
)


CANONICAL_DIRECTORY = Path(
    "scenarios/canonical",
)

PROMPT_INJECTION_PATH = (
    CANONICAL_DIRECTORY
    / "prompt_injection_exfiltration_unsafe.yaml"
)

FORBIDDEN_AGENT_KEYS = {
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
    "policy_version",
    "trusted_instruction_source_ids",
    "untrusted_content_source_ids",
}


def _mapping_keys(
    value: Any,
) -> set[str]:
    keys: set[str] = set()

    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(key)
            keys.update(
                _mapping_keys(item),
            )

    elif isinstance(value, list):
        for item in value:
            keys.update(
                _mapping_keys(item),
            )

    return keys


def test_all_agent_views_exclude_oracle_fields() -> None:
    scenarios = load_scenarios(
        CANONICAL_DIRECTORY,
    )

    assert len(scenarios) == 20

    for scenario in scenarios:
        agent_view = derive_agent_view(
            scenario,
        )

        dumped = agent_view.model_dump(
            mode="json",
        )

        assert not (
            _mapping_keys(dumped)
            & FORBIDDEN_AGENT_KEYS
        )

        assert agent_view.available_tools == [
            scenario.proposal.tool,
        ]

        assert (
            agent_view.task
            == scenario.context.task
        )

        assert (
            agent_view.authorized_purpose
            == scenario.context.authorized_purpose
        )


def test_prompt_injection_view_hides_trust_labels() -> None:
    scenario = load_scenario(
        PROMPT_INJECTION_PATH,
    )

    agent_view = derive_agent_view(
        scenario,
    )

    assert set(
        agent_view.instruction_source_ids
    ) == {
        "task-request",
        "web-page-17",
        "investigation-record-17",
    }

    assert (
        "trusted_instruction_source_ids"
        not in agent_view.data_context
    )

    assert (
        "untrusted_content_source_ids"
        not in agent_view.data_context
    )

    assert agent_view.data_context[
        "content_source_ids"
    ] == [
        "web-page-17",
        "investigation-record-17",
    ]


def test_oracle_preserves_existing_ground_truth() -> None:
    scenarios = load_scenarios(
        CANONICAL_DIRECTORY,
    )

    for scenario in scenarios:
        oracle = derive_oracle(
            scenario,
        )

        assert (
            oracle.scenario_id
            == scenario.scenario_id
        )

        assert oracle.kind is scenario.kind

        assert (
            oracle.ground_truth
            == scenario.ground_truth
        )

        assert (
            oracle.required_evidence
            == scenario.required_evidence
        )

        if scenario.kind is ScenarioKind.UNSAFE:
            assert (
                oracle.ground_truth
                .unsafe_if_executed
                is True
            )
        else:
            assert (
                oracle.ground_truth
                .unsafe_if_executed
                is False
            )


def test_compatibility_view_preserves_scenario_schema() -> None:
    scenario = load_scenario(
        PROMPT_INJECTION_PATH,
    )

    original = scenario.model_dump(
        mode="json",
    )

    views = derive_scenario_views(
        scenario,
    )

    assert scenario.model_dump(
        mode="json",
    ) == original

    assert set(original) == {
        "scenario_id",
        "pair_id",
        "title",
        "family",
        "kind",
        "claim",
        "required_evidence",
        "ground_truth",
        "context",
        "proposal",
        "norms",
        "sandbox",
    }

    assert (
        views.deterministic_proposal
        == scenario.proposal
    )

    assert (
        views.deterministic_proposal
        is not scenario.proposal
    )

    assert (
        views.governance_context
        == scenario.context
    )

    assert (
        views.oracle.scenario_id
        not in views.agent_view.model_dump_json()
    )
