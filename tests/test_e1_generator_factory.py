from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eg_runtime.e1_experiment import (
    load_e1_configuration,
    run_e1_experiment,
)
from eg_runtime.models import ActionProposal
from eg_runtime.proposal_generation import (
    ProposalGenerationRequest,
    ProposalGenerationResult,
    build_proposal_generation_request,
)
from eg_runtime.scenarios import (
    ScenarioDefinition,
    load_scenario,
)


EXPERIMENT_ID = "e1-generator-factory-test"

SCENARIO_IDS = (
    "approval_binding_benign",
    "approval_binding_unsafe",
)


class TrialMappedGenerator:
    def __init__(
        self,
        *,
        repetition_index: int,
        proposals: dict[str, ActionProposal],
    ) -> None:
        self.repetition_index = repetition_index
        self._proposals = proposals
        self.requests: list[
            ProposalGenerationRequest
        ] = []

    def generate_request(
        self,
        request: ProposalGenerationRequest,
    ) -> ProposalGenerationResult:
        self.requests.append(request)

        proposal = self._proposals[
            request.trial_id
        ].model_copy(
            deep=True,
        )

        generation_id = hashlib.sha256(
            (
                str(self.repetition_index)
                + ":"
                + request.trial_id
                + ":"
                + proposal.proposal_id
            ).encode("utf-8")
        ).hexdigest()

        return ProposalGenerationResult(
            generation_id=generation_id,
            generator_name=(
                "factory-generator-"
                f"{self.repetition_index}"
            ),
            declared_plan=[
                "Use the repetition-scoped generator.",
                "Return the mapped proposal.",
            ],
            proposal=proposal,
        )


def _write_configuration(
    path: Path,
) -> None:
    path.write_text(
        json.dumps(
            {
                "experiment_id": EXPERIMENT_ID,
                "scenario_directory": (
                    "scenarios/canonical"
                ),
                "scenario_ids": list(
                    SCENARIO_IDS
                ),
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


def _load_selected_scenarios(
    repository_root: Path,
) -> list[ScenarioDefinition]:
    return [
        load_scenario(
            repository_root
            / "scenarios"
            / "canonical"
            / f"{scenario_id}.yaml"
        )
        for scenario_id in SCENARIO_IDS
    ]


def _load_runs(
    directory: Path,
) -> list[dict[str, object]]:
    payload = json.loads(
        (
            directory / "runs.json"
        ).read_text(
            encoding="utf-8",
        )
    )

    assert isinstance(payload, list)

    return payload


def test_e1_creates_one_generator_per_repetition(
    tmp_path: Path,
) -> None:
    repository_root = (
        Path(__file__).resolve().parents[1]
    )

    scenarios = _load_selected_scenarios(
        repository_root
    )

    configuration_path = (
        tmp_path / "configuration.json"
    )

    output_directory = (
        tmp_path / "results"
    )

    _write_configuration(
        configuration_path
    )

    factory_calls: list[int] = []
    generators: list[
        TrialMappedGenerator
    ] = []

    def factory(
        repetition_index: int,
    ) -> TrialMappedGenerator:
        factory_calls.append(
            repetition_index
        )

        proposals: dict[
            str,
            ActionProposal,
        ] = {}

        for scenario in scenarios:
            request = (
                build_proposal_generation_request(
                    scenario,
                    experiment_id=EXPERIMENT_ID,
                    repetition_index=(
                        repetition_index
                    ),
                )
            )

            proposals[
                request.trial_id
            ] = scenario.proposal

        generator = TrialMappedGenerator(
            repetition_index=(
                repetition_index
            ),
            proposals=proposals,
        )

        generators.append(generator)

        return generator

    configuration = load_e1_configuration(
        configuration_path
    )

    artifacts = run_e1_experiment(
        configuration,
        base_directory=repository_root,
        output_directory=output_directory,
        proposal_generator_factory=factory,
    )

    assert artifacts.total_runs == 16
    assert factory_calls == [0, 1]
    assert len(generators) == 2

    for generator in generators:
        assert len(generator.requests) == 2

        assert {
            request.repetition_index
            for request in generator.requests
        } == {
            generator.repetition_index,
        }

    for repetition_number, expected_index in (
        (1, 0),
        (2, 1),
    ):
        runs = _load_runs(
            output_directory
            / f"repetition-{repetition_number:02d}"
        )

        assert len(runs) == 8

        assert {
            run["repetition_index"]
            for run in runs
        } == {
            expected_index,
        }

        assert {
            run["generator_name"]
            for run in runs
        } == {
            f"factory-generator-{expected_index}",
        }

        trial_ids_by_scenario: dict[
            str,
            set[str],
        ] = {}

        for run in runs:
            scenario_id = str(
                run["scenario_id"]
            )

            trial_ids_by_scenario.setdefault(
                scenario_id,
                set(),
            ).add(
                str(run["trial_id"])
            )

        assert set(
            trial_ids_by_scenario
        ) == set(SCENARIO_IDS)

        assert all(
            len(trial_ids) == 1
            for trial_ids
            in trial_ids_by_scenario.values()
        )
