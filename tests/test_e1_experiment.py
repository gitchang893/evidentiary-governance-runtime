from __future__ import annotations

import json
from pathlib import Path

import pytest

from eg_runtime.e1_experiment import (
    E1ConfigurationError,
    build_e1_run_plan,
    load_e1_configuration,
    load_e1_scenarios,
    run_e1_experiment,
)


EXPECTED_SCENARIO_IDS = [
    "approval_binding_benign",
    "approval_binding_unsafe",
    "data_classification_benign",
    "data_classification_unsafe",
    "plan_alignment_benign",
    "plan_alignment_unsafe",
    "purpose_alignment_benign",
    "purpose_alignment_unsafe",
    "role_authorization_benign",
    "role_authorization_unsafe",
    "excessive_data_access_benign",
    "excessive_data_access_unsafe",
    "cross_step_data_reuse_benign",
    "cross_step_data_reuse_unsafe",
    "prompt_injection_exfiltration_benign",
    "prompt_injection_exfiltration_unsafe",
]


def write_configuration(
    path: Path,
    *,
    scenario_ids: list[str] | None = None,
    repetitions: int = 5,
) -> None:
    payload = {
        "experiment_id": "e1-governance-comparison",
        "scenario_directory": "scenarios/canonical",
        "scenario_ids": (
            scenario_ids
            if scenario_ids is not None
            else EXPECTED_SCENARIO_IDS
        ),
        "conditions": [
            "c0_logging",
            "c1_post_hoc",
            "c2_action_level",
            "c3_organizational",
        ],
        "repetitions": repetitions,
    }

    path.write_text(
        json.dumps(
            payload,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_loads_e1_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "e1.json"
    write_configuration(path)

    configuration = load_e1_configuration(path)

    assert (
        configuration.experiment_id
        == "e1-governance-comparison"
    )

    assert configuration.scenario_directory == Path(
        "scenarios/canonical"
    )

    assert configuration.scenario_ids == (
        EXPECTED_SCENARIO_IDS
    )

    assert [
        condition.value
        for condition in configuration.conditions
    ] == [
        "c0_logging",
        "c1_post_hoc",
        "c2_action_level",
        "c3_organizational",
    ]

    assert configuration.repetitions == 5
    assert configuration.scenario_count == 16
    assert configuration.run_count == 320


def test_rejects_duplicate_scenario_ids(
    tmp_path: Path,
) -> None:
    path = tmp_path / "e1.json"

    write_configuration(
        path,
        scenario_ids=[
            "approval_binding_benign",
            "approval_binding_benign",
        ],
    )

    with pytest.raises(
        E1ConfigurationError,
        match="scenario_ids",
    ):
        load_e1_configuration(path)


@pytest.mark.parametrize(
    "repetitions",
    [
        0,
        -1,
        True,
    ],
)
def test_rejects_invalid_repetitions(
    tmp_path: Path,
    repetitions: object,
) -> None:
    path = tmp_path / "e1.json"

    write_configuration(
        path,
        repetitions=repetitions,
    )

    with pytest.raises(
        E1ConfigurationError,
        match="repetitions",
    ):
        load_e1_configuration(path)


def test_rejects_incomplete_condition_set(
    tmp_path: Path,
) -> None:
    path = tmp_path / "e1.json"

    payload = {
        "experiment_id": "e1-governance-comparison",
        "scenario_directory": "scenarios/canonical",
        "scenario_ids": EXPECTED_SCENARIO_IDS,
        "conditions": [
            "c0_logging",
            "c1_post_hoc",
            "c2_action_level",
        ],
        "repetitions": 5,
    }

    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(
        E1ConfigurationError,
        match="conditions",
    ):
        load_e1_configuration(path)


def test_loads_selected_scenarios_in_configured_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "e1.json"
    write_configuration(path)

    configuration = load_e1_configuration(path)

    repository_root = Path(__file__).resolve().parents[1]

    scenarios = load_e1_scenarios(
        configuration,
        base_directory=repository_root,
    )

    assert [
        scenario.scenario_id
        for scenario in scenarios
    ] == EXPECTED_SCENARIO_IDS

    assert len(scenarios) == 16

    assert {
        scenario.pair_id
        for scenario in scenarios
    } == {
        "approval_binding_01",
        "data_classification_01",
        "plan_alignment_01",
        "purpose_alignment_01",
        "role_authorization_01",
        "excessive_data_access_01",
        "cross_step_data_reuse_01",
        "prompt_injection_exfiltration_01",
    }


def test_rejects_missing_selected_scenario(
    tmp_path: Path,
) -> None:
    path = tmp_path / "e1.json"

    write_configuration(
        path,
        scenario_ids=[
            *EXPECTED_SCENARIO_IDS,
            "missing_scenario",
        ],
    )

    configuration = load_e1_configuration(path)
    repository_root = Path(__file__).resolve().parents[1]

    with pytest.raises(
        E1ConfigurationError,
        match="missing_scenario",
    ):
        load_e1_scenarios(
            configuration,
            base_directory=repository_root,
        )


def test_rejects_incomplete_selected_pair(
    tmp_path: Path,
) -> None:
    path = tmp_path / "e1.json"

    write_configuration(
        path,
        scenario_ids=EXPECTED_SCENARIO_IDS[:-1],
    )

    configuration = load_e1_configuration(path)
    repository_root = Path(__file__).resolve().parents[1]

    with pytest.raises(
        E1ConfigurationError,
        match="complete benign/unsafe pairs",
    ):
        load_e1_scenarios(
            configuration,
            base_directory=repository_root,
        )


def test_builds_deterministic_320_run_plan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "e1.json"
    write_configuration(path)

    configuration = load_e1_configuration(path)
    repository_root = Path(__file__).resolve().parents[1]

    scenarios = load_e1_scenarios(
        configuration,
        base_directory=repository_root,
    )

    run_plan = build_e1_run_plan(
        configuration,
        scenarios,
    )

    assert len(run_plan) == 320

    assert run_plan[0].run_id == (
        "e1-governance-comparison--"
        "r01--approval_binding_benign--c0_logging"
    )

    assert run_plan[-1].run_id == (
        "e1-governance-comparison--"
        "r05--prompt_injection_exfiltration_unsafe--"
        "c3_organizational"
    )

    assert [
        run.condition.value
        for run in run_plan[:4]
    ] == [
        "c0_logging",
        "c1_post_hoc",
        "c2_action_level",
        "c3_organizational",
    ]

    assert len(
        {
            run.run_id
            for run in run_plan
        }
    ) == 320


def test_run_plan_repeats_each_scenario_condition_five_times(
    tmp_path: Path,
) -> None:
    path = tmp_path / "e1.json"
    write_configuration(path)

    configuration = load_e1_configuration(path)
    repository_root = Path(__file__).resolve().parents[1]

    scenarios = load_e1_scenarios(
        configuration,
        base_directory=repository_root,
    )

    run_plan = build_e1_run_plan(
        configuration,
        scenarios,
    )

    counts: dict[tuple[str, str], int] = {}

    for run in run_plan:
        key = (
            run.scenario_id,
            run.condition.value,
        )

        counts[key] = counts.get(key, 0) + 1

    assert len(counts) == 64
    assert set(counts.values()) == {5}


def test_runs_repetitions_in_separate_directories(
    tmp_path: Path,
) -> None:
    configuration_path = tmp_path / "e1.json"

    write_configuration(
        configuration_path,
        scenario_ids=[
            "approval_binding_benign",
            "approval_binding_unsafe",
        ],
        repetitions=2,
    )

    configuration = load_e1_configuration(
        configuration_path
    )

    repository_root = Path(__file__).resolve().parents[1]
    output_directory = tmp_path / "e1-results"

    artifacts = run_e1_experiment(
        configuration,
        base_directory=repository_root,
        output_directory=output_directory,
    )

    assert artifacts.experiment_id == (
        "e1-governance-comparison"
    )
    assert artifacts.scenario_count == 2
    assert artifacts.condition_count == 4
    assert artifacts.repetitions == 2
    assert artifacts.total_runs == 16

    assert len(artifacts.run_plan) == 16
    assert len(artifacts.repetition_results) == 2

    assert [
        result.total_runs
        for result in artifacts.repetition_results
    ] == [
        8,
        8,
    ]

    assert [
        result.output_directory
        for result in artifacts.repetition_results
    ] == [
        output_directory / "repetition-01",
        output_directory / "repetition-02",
    ]

    for repetition in (
        "repetition-01",
        "repetition-02",
    ):
        evidence_path = (
            output_directory
            / repetition
            / "evidence"
            / "approval_binding_benign"
            / "c0_logging.jsonl"
        )

        assert evidence_path.exists()


def test_repository_e1_configuration_defines_320_runs() -> None:
    repository_root = Path(__file__).resolve().parents[1]

    configuration = load_e1_configuration(
        repository_root
        / "configs"
        / "e1-governance-comparison.json"
    )

    scenarios = load_e1_scenarios(
        configuration,
        base_directory=repository_root,
    )

    run_plan = build_e1_run_plan(
        configuration,
        scenarios,
    )

    assert configuration.experiment_id == (
        "e1-governance-comparison"
    )
    assert configuration.scenario_count == 16
    assert configuration.repetitions == 5
    assert configuration.run_count == 320

    assert len(scenarios) == 16
    assert len(run_plan) == 320

    assert {
        scenario.pair_id
        for scenario in scenarios
    } == {
        "approval_binding_01",
        "data_classification_01",
        "plan_alignment_01",
        "purpose_alignment_01",
        "role_authorization_01",
        "excessive_data_access_01",
        "cross_step_data_reuse_01",
        "prompt_injection_exfiltration_01",
    }
