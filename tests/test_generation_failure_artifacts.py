from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from eg_runtime.e1_experiment import (
    load_e1_configuration,
    run_e1_experiment,
)
from eg_runtime.generation_failures import (
    ProposalGenerationSchemaError,
)
from eg_runtime.proposal_generation import (
    ProposalGenerationRequest,
    ProposalGenerationResult,
    build_proposal_generation_request,
)
from eg_runtime.scenarios import (
    ScenarioDefinition,
    load_scenario,
)
from eg_runtime.simulation import (
    SimulationRunner,
)


BENIGN_PATH = Path(
    "scenarios/canonical/"
    "approval_binding_benign.yaml"
)

UNSAFE_PATH = Path(
    "scenarios/canonical/"
    "approval_binding_unsafe.yaml"
)


class PartiallyFailingGenerator:
    name = "partially-failing-generator"

    def __init__(
        self,
        *,
        failed_trial_id: str,
        proposals: dict[str, object],
    ) -> None:
        self._failed_trial_id = (
            failed_trial_id
        )

        self._proposals = proposals

    def generate_request(
        self,
        request: ProposalGenerationRequest,
    ) -> ProposalGenerationResult:
        if (
            request.trial_id
            == self._failed_trial_id
        ):
            raise ProposalGenerationSchemaError(
                "Generated proposal failed schema validation",
                raw_response='{"proposal": "invalid"}',
                details={
                    "field": "proposal",
                },
            )

        proposal = self._proposals[
            request.trial_id
        ]

        return ProposalGenerationResult(
            generation_id="d" * 64,
            generator_name=self.name,
            declared_plan=[
                "Use the valid deterministic proposal.",
            ],
            proposal=proposal.model_copy(
                deep=True,
            ),
        )


def _generator(
    scenarios: list[ScenarioDefinition],
    *,
    experiment_id: str,
    repetition_index: int,
    failed_scenario_id: str,
) -> PartiallyFailingGenerator:
    proposals: dict[str, object] = {}

    failed_trial_id: str | None = None

    for scenario in scenarios:
        request = (
            build_proposal_generation_request(
                scenario,
                experiment_id=experiment_id,
                repetition_index=(
                    repetition_index
                ),
            )
        )

        proposals[
            request.trial_id
        ] = scenario.proposal

        if (
            scenario.scenario_id
            == failed_scenario_id
        ):
            failed_trial_id = (
                request.trial_id
            )

    assert failed_trial_id is not None

    return PartiallyFailingGenerator(
        failed_trial_id=failed_trial_id,
        proposals=proposals,
    )


def test_generation_failure_is_fail_fast_by_default(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(
        UNSAFE_PATH
    )

    generator = _generator(
        [scenario],
        experiment_id="failure-fast-test",
        repetition_index=0,
        failed_scenario_id=(
            scenario.scenario_id
        ),
    )

    with pytest.raises(
        ProposalGenerationSchemaError,
    ):
        SimulationRunner(
            proposal_generator=generator,
            experiment_id="failure-fast-test",
            repetition_index=0,
        ).run(
            [scenario],
            tmp_path / "failure-fast",
        )


def test_generation_failure_is_saved_without_governed_runs(
    tmp_path: Path,
) -> None:
    benign = load_scenario(
        BENIGN_PATH
    )

    unsafe = load_scenario(
        UNSAFE_PATH
    )

    scenarios = [
        benign,
        unsafe,
    ]

    generator = _generator(
        scenarios,
        experiment_id="failure-artifact-test",
        repetition_index=0,
        failed_scenario_id=(
            unsafe.scenario_id
        ),
    )

    artifacts = SimulationRunner(
        proposal_generator=generator,
        experiment_id="failure-artifact-test",
        repetition_index=0,
        continue_on_generation_failure=True,
    ).run(
        scenarios,
        tmp_path / "failure-artifacts",
    )

    assert len(artifacts.runs) == 4

    assert {
        run.scenario_id
        for run in artifacts.runs
    } == {
        benign.scenario_id,
    }

    assert len(
        artifacts.generation_failures
    ) == 1

    failure = (
        artifacts.generation_failures[0]
    )

    assert (
        failure.scenario_id
        == unsafe.scenario_id
    )

    assert (
        failure.failure.kind.value
        == "schema_validation_error"
    )

    summary = artifacts.summary

    assert summary.total_runs == 4
    assert summary.scenario_count == 1
    assert summary.attempted_generations == 2
    assert summary.successful_generations == 1
    assert summary.failed_generations == 1

    assert (
        summary.generation_completion_rate
        == 0.5
    )

    json_failures = json.loads(
        Path(
            artifacts.generation_failures_json
        ).read_text(
            encoding="utf-8",
        )
    )

    assert len(json_failures) == 1

    assert (
        json_failures[0]["scenario_id"]
        == unsafe.scenario_id
    )

    with Path(
        artifacts.generation_failures_csv
    ).open(
        encoding="utf-8",
        newline="",
    ) as stream:
        csv_failures = list(
            csv.DictReader(stream)
        )

    assert len(csv_failures) == 1

    assert (
        csv_failures[0]["scenario_id"]
        == unsafe.scenario_id
    )

    assert (
        csv_failures[0]["kind"]
        == "schema_validation_error"
    )


def test_e1_counts_generation_failures_separately(
    tmp_path: Path,
) -> None:
    repository_root = (
        Path(__file__).resolve().parents[1]
    )

    configuration_path = (
        tmp_path / "configuration.json"
    )

    configuration_path.write_text(
        json.dumps(
            {
                "experiment_id": (
                    "e1-generation-failure-test"
                ),
                "scenario_directory": (
                    "scenarios/canonical"
                ),
                "scenario_ids": [
                    "approval_binding_benign",
                    "approval_binding_unsafe",
                ],
                "conditions": [
                    "c0_logging",
                    "c1_post_hoc",
                    "c2_action_level",
                    "c3_organizational",
                ],
                "repetitions": 2,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    configuration = (
        load_e1_configuration(
            configuration_path
        )
    )

    scenarios = [
        load_scenario(
            repository_root
            / "scenarios"
            / "canonical"
            / "approval_binding_benign.yaml"
        ),
        load_scenario(
            repository_root
            / "scenarios"
            / "canonical"
            / "approval_binding_unsafe.yaml"
        ),
    ]

    def factory(
        repetition_index: int,
    ) -> PartiallyFailingGenerator:
        return _generator(
            scenarios,
            experiment_id=(
                configuration.experiment_id
            ),
            repetition_index=(
                repetition_index
            ),
            failed_scenario_id=(
                "approval_binding_unsafe"
            ),
        )

    output_directory = (
        tmp_path / "e1-results"
    )

    artifacts = run_e1_experiment(
        configuration,
        base_directory=repository_root,
        output_directory=output_directory,
        proposal_generator_factory=factory,
        continue_on_generation_failure=True,
    )

    assert artifacts.total_runs == 8

    assert (
        artifacts.attempted_generations
        == 4
    )

    assert (
        artifacts.successful_generations
        == 2
    )

    assert (
        artifacts.failed_generations
        == 2
    )

    assert (
        artifacts.generation_completion_rate
        == 0.5
    )

    assert len(
        artifacts.repetition_results
    ) == 2

    for repetition in (
        artifacts.repetition_results
    ):
        assert repetition.total_runs == 4
        assert repetition.scenario_count == 1

        assert (
            repetition.attempted_generations
            == 2
        )

        assert (
            repetition.successful_generations
            == 1
        )

        assert (
            repetition.failed_generations
            == 1
        )

        assert (
            repetition
            .generation_completion_rate
            == 0.5
        )

        failures = json.loads(
            (
                repetition.output_directory
                / "generation-failures.json"
            ).read_text(
                encoding="utf-8",
            )
        )

        assert len(failures) == 1

        assert (
            failures[0]["scenario_id"]
            == "approval_binding_unsafe"
        )
