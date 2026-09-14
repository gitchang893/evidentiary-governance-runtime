from __future__ import annotations

import json
from pathlib import Path

import pytest

from eg_runtime.e1_experiment import (
    load_e1_configuration,
    run_e1_experiment,
)
from eg_runtime.proposal_generation import (
    build_proposal_generation_request,
)
from eg_runtime.request_replay import (
    build_request_replay_factory,
)
from eg_runtime.scenarios import (
    ScenarioDefinition,
    load_scenario,
    load_scenarios,
)


CONFIGURATION_PATH = Path(
    "configs/e1-governance-comparison.json"
)


def _selected_scenarios(
    repository_root: Path,
    scenario_directory: str,
    scenario_ids: list[str],
) -> list[ScenarioDefinition]:
    loaded = load_scenarios(
        repository_root
        / scenario_directory
    )

    by_id = {
        scenario.scenario_id: scenario
        for scenario in loaded
    }

    missing = [
        scenario_id
        for scenario_id in scenario_ids
        if scenario_id not in by_id
    ]

    assert not missing

    return [
        by_id[scenario_id]
        for scenario_id in scenario_ids
    ]


def _load_runs(
    repetition_directory: Path,
) -> list[dict[str, object]]:
    payload = json.loads(
        (
            repetition_directory
            / "runs.json"
        ).read_text(
            encoding="utf-8",
        )
    )

    assert isinstance(payload, list)

    assert all(
        isinstance(run, dict)
        for run in payload
    )

    return payload


def test_request_replay_rejects_unknown_trial(
) -> None:
    scenario = load_scenario(
        Path(
            "scenarios/canonical/"
            "approval_binding_benign.yaml"
        )
    )

    factory = build_request_replay_factory(
        [scenario],
        experiment_id="request-replay-test",
    )

    generator = factory(0)

    unknown_request = (
        build_proposal_generation_request(
            scenario,
            experiment_id=(
                "request-replay-test"
            ),
            repetition_index=1,
        )
    )

    with pytest.raises(
        KeyError,
        match=(
            "no deterministic replay entry"
        ),
    ):
        generator.generate_request(
            unknown_request
        )


def test_request_replay_completes_full_e1_matrix(
    tmp_path: Path,
) -> None:
    repository_root = (
        Path(__file__).resolve().parents[1]
    )

    configuration = (
        load_e1_configuration(
            repository_root
            / CONFIGURATION_PATH
        )
    )

    scenarios = _selected_scenarios(
        repository_root,
        configuration.scenario_directory,
        list(configuration.scenario_ids),
    )

    assert len(scenarios) == 16
    assert configuration.repetitions == 5
    assert len(configuration.conditions) == 4

    generator_factory = (
        build_request_replay_factory(
            scenarios,
            experiment_id=(
                configuration.experiment_id
            ),
        )
    )

    output_directory = (
        tmp_path / "request-replay-e1"
    )

    artifacts = run_e1_experiment(
        configuration,
        base_directory=repository_root,
        output_directory=output_directory,
        proposal_generator_factory=(
            generator_factory
        ),
    )

    assert artifacts.total_runs == 320

    trials_by_scenario: dict[
        str,
        list[str],
    ] = {
        scenario.scenario_id: []
        for scenario in scenarios
    }

    generations_by_scenario: dict[
        str,
        list[str],
    ] = {
        scenario.scenario_id: []
        for scenario in scenarios
    }

    for repetition_number in range(
        1,
        configuration.repetitions + 1,
    ):
        repetition_index = (
            repetition_number - 1
        )

        runs = _load_runs(
            output_directory
            / (
                "repetition-"
                f"{repetition_number:02d}"
            )
        )

        assert len(runs) == 64

        assert {
            run["repetition_index"]
            for run in runs
        } == {
            repetition_index,
        }

        assert {
            run["generator_name"]
            for run in runs
        } == {
            "deterministic-request-replay",
        }

        grouped: dict[
            str,
            list[dict[str, object]],
        ] = {}

        for run in runs:
            grouped.setdefault(
                str(run["scenario_id"]),
                [],
            ).append(run)

        assert set(grouped) == {
            scenario.scenario_id
            for scenario in scenarios
        }

        for scenario_id, scenario_runs in (
            grouped.items()
        ):
            assert len(scenario_runs) == 4

            assert len({
                str(run["condition"])
                for run in scenario_runs
            }) == 4

            trial_ids = {
                str(run["trial_id"])
                for run in scenario_runs
            }

            generation_ids = {
                str(run["generation_id"])
                for run in scenario_runs
            }

            declared_plans = {
                tuple(run["declared_plan"])
                for run in scenario_runs
            }

            assert len(trial_ids) == 1
            assert len(generation_ids) == 1
            assert len(declared_plans) == 1

            trials_by_scenario[
                scenario_id
            ].append(
                next(iter(trial_ids))
            )

            generations_by_scenario[
                scenario_id
            ].append(
                next(iter(generation_ids))
            )

    for scenario_id in (
        trials_by_scenario
    ):
        assert len(
            set(
                trials_by_scenario[
                    scenario_id
                ]
            )
        ) == 5

        assert len(
            set(
                generations_by_scenario[
                    scenario_id
                ]
            )
        ) == 5
