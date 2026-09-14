from __future__ import annotations

import json
from pathlib import Path

from eg_runtime.models import ActionProposal
from eg_runtime.proposal_generation import (
    ProposalGenerationRequest,
    ProposalGenerationResult,
    canonical_prompt_payload_json,
)
from eg_runtime.scenarios import (
    ScenarioDefinition,
    load_scenario,
)
from eg_runtime.simulation import SimulationRunner


SCENARIO_PATH = Path(
    "scenarios/canonical/"
    "prompt_injection_exfiltration_unsafe.yaml"
)


class RequestAwareGenerator:
    def __init__(
        self,
        proposal: ActionProposal,
    ) -> None:
        self._proposal = proposal
        self.requests: list[
            ProposalGenerationRequest
        ] = []

    def generate_request(
        self,
        request: ProposalGenerationRequest,
    ) -> ProposalGenerationResult:
        self.requests.append(request)

        return ProposalGenerationResult(
            generation_id="c" * 64,
            generator_name=(
                "request-aware-test-generator"
            ),
            declared_plan=[
                "Inspect the visible sources.",
                "Prepare one structured proposal.",
            ],
            proposal=self._proposal.model_copy(
                deep=True,
            ),
        )


def _start_metadata(
    evidence_path: str,
) -> dict[str, object]:
    first_line = Path(
        evidence_path
    ).read_text(
        encoding="utf-8",
    ).splitlines()[0]

    event = json.loads(first_line)

    assert event["event_type"] == "run_started"

    metadata = event["payload"]["metadata"]

    assert isinstance(metadata, dict)

    return metadata


def test_request_is_generated_once_and_shared_across_conditions(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(
        SCENARIO_PATH
    )

    generator = RequestAwareGenerator(
        scenario.proposal
    )

    artifacts = SimulationRunner(
        proposal_generator=generator,
        experiment_id="e1-local-llm",
        repetition_index=3,
    ).run(
        [scenario],
        tmp_path / "request-aware",
    )

    assert len(generator.requests) == 1
    assert len(artifacts.runs) == 4

    request = generator.requests[0]

    assert request.repetition_index == 3

    serialized_prompt = (
        canonical_prompt_payload_json(
            request
        )
    )

    assert scenario.scenario_id not in (
        serialized_prompt
    )

    assert request.trial_id not in (
        serialized_prompt
    )

    for record in artifacts.runs:
        assert record.trial_id == request.trial_id
        assert record.repetition_index == 3
        assert record.generation_id == "c" * 64

        metadata = _start_metadata(
            record.evidence_path
        )

        assert (
            metadata["trial_id"]
            == request.trial_id
        )

        assert (
            metadata["repetition_index"]
            == 3
        )


def test_repetition_changes_trial_identity_not_prompt_payload(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(
        SCENARIO_PATH
    )

    first_generator = RequestAwareGenerator(
        scenario.proposal
    )

    second_generator = RequestAwareGenerator(
        scenario.proposal
    )

    first_artifacts = SimulationRunner(
        proposal_generator=first_generator,
        experiment_id="e1-local-llm",
        repetition_index=0,
    ).run(
        [scenario],
        tmp_path / "repetition-0",
    )

    second_artifacts = SimulationRunner(
        proposal_generator=second_generator,
        experiment_id="e1-local-llm",
        repetition_index=1,
    ).run(
        [scenario],
        tmp_path / "repetition-1",
    )

    first_request = first_generator.requests[0]
    second_request = second_generator.requests[0]

    assert (
        first_request.trial_id
        != second_request.trial_id
    )

    assert (
        canonical_prompt_payload_json(
            first_request
        )
        == canonical_prompt_payload_json(
            second_request
        )
    )

    assert {
        record.trial_id
        for record in first_artifacts.runs
    } == {
        first_request.trial_id,
    }

    assert {
        record.trial_id
        for record in second_artifacts.runs
    } == {
        second_request.trial_id,
    }
