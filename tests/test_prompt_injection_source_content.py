from __future__ import annotations

from pathlib import Path
from typing import Any

from eg_runtime.scenario_views import (
    derive_agent_view,
)
from eg_runtime.scenarios import (
    ScenarioDefinition,
    load_scenario,
)


CANONICAL_ROOT = Path(
    "scenarios/canonical"
)

BENIGN_PATH = (
    CANONICAL_ROOT
    / "prompt_injection_exfiltration_benign.yaml"
)

UNSAFE_PATH = (
    CANONICAL_ROOT
    / "prompt_injection_exfiltration_unsafe.yaml"
)

EXPECTED_SOURCE_IDS = {
    "task-request",
    "web-page-17",
    "investigation-record-17",
}


def _load_pair(
) -> tuple[
    ScenarioDefinition,
    ScenarioDefinition,
]:
    return (
        load_scenario(BENIGN_PATH),
        load_scenario(UNSAFE_PATH),
    )


def _raw_sources(
    scenario: ScenarioDefinition,
) -> list[dict[str, Any]]:
    value = scenario.context.data_context.get(
        "instruction_sources"
    )

    assert isinstance(value, list)

    assert all(
        isinstance(item, dict)
        for item in value
    )

    return value


def _source_map(
    scenario: ScenarioDefinition,
) -> dict[str, str]:
    view = derive_agent_view(scenario)

    return {
        source.source_id: source.content
        for source in view.instruction_sources
    }


def test_prompt_injection_pair_has_content_bearing_sources(
) -> None:
    for scenario in _load_pair():
        raw_sources = _raw_sources(scenario)

        assert {
            source["source_id"]
            for source in raw_sources
        } == EXPECTED_SOURCE_IDS

        for source in raw_sources:
            assert set(source) == {
                "source_id",
                "source_type",
                "content",
            }

            assert isinstance(
                source["content"],
                str,
            )

            assert source["content"].strip()


def test_instruction_source_trust_remains_runtime_only(
) -> None:
    for scenario in _load_pair():
        agent_view = derive_agent_view(
            scenario
        )

        for source in (
            agent_view.instruction_sources
        ):
            dumped = source.model_dump(
                mode="json"
            )

            assert set(dumped) == {
                "source_id",
                "source_type",
                "content",
            }

            assert "trusted" not in dumped
            assert "classification" not in dumped

        serialized = (
            agent_view.model_dump_json()
        )

        assert (
            "trusted_instruction_source_ids"
            not in serialized
        )

        assert (
            "untrusted_content_source_ids"
            not in serialized
        )


def test_only_unsafe_pair_contains_adversarial_instruction(
) -> None:
    benign, unsafe = _load_pair()

    benign_sources = _source_map(benign)
    unsafe_sources = _source_map(unsafe)

    adversarial_phrase = (
        "Ignore the assigned task"
    )

    assert adversarial_phrase not in (
        benign_sources["web-page-17"]
    )

    assert adversarial_phrase in (
        unsafe_sources["web-page-17"]
    )

    assert (
        benign_sources["task-request"]
        == unsafe_sources["task-request"]
    )

    assert (
        benign_sources[
            "investigation-record-17"
        ]
        == unsafe_sources[
            "investigation-record-17"
        ]
    )


def test_proposal_source_references_are_declared(
) -> None:
    for scenario in _load_pair():
        declared_ids = {
            source["source_id"]
            for source in _raw_sources(
                scenario
            )
        }

        proposal_ids = set(
            scenario.proposal
            .instruction_source_ids
            or []
        )

        assert proposal_ids
        assert proposal_ids <= declared_ids
