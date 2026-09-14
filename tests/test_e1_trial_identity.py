from __future__ import annotations

import json
from pathlib import Path

from eg_runtime.e1_experiment import (
    load_e1_configuration,
    run_e1_experiment,
)


def _write_configuration(
    path: Path,
) -> None:
    payload = {
        "experiment_id": "e1-trial-identity-test",
        "scenario_directory": "scenarios/canonical",
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
    }

    path.write_text(
        json.dumps(
            payload,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


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
        isinstance(item, dict)
        for item in payload
    )

    return payload


def _scenario_trial_ids(
    runs: list[dict[str, object]],
) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}

    for run in runs:
        scenario_id = run["scenario_id"]
        trial_id = run["trial_id"]

        assert isinstance(
            scenario_id,
            str,
        )

        assert isinstance(
            trial_id,
            str,
        )

        grouped.setdefault(
            scenario_id,
            set(),
        ).add(trial_id)

    return grouped


def test_e1_trial_identity_is_shared_within_repetition(
    tmp_path: Path,
) -> None:
    repository_root = (
        Path(__file__).resolve().parents[1]
    )

    configuration_path = (
        tmp_path / "e1.json"
    )

    output_directory = (
        tmp_path / "e1-results"
    )

    _write_configuration(
        configuration_path
    )

    configuration = load_e1_configuration(
        configuration_path
    )

    artifacts = run_e1_experiment(
        configuration,
        base_directory=repository_root,
        output_directory=output_directory,
    )

    assert artifacts.total_runs == 16

    first_runs = _load_runs(
        output_directory
        / "repetition-01"
    )

    second_runs = _load_runs(
        output_directory
        / "repetition-02"
    )

    assert len(first_runs) == 8
    assert len(second_runs) == 8

    assert {
        run["repetition_index"]
        for run in first_runs
    } == {
        0,
    }

    assert {
        run["repetition_index"]
        for run in second_runs
    } == {
        1,
    }

    first_trials = _scenario_trial_ids(
        first_runs
    )

    second_trials = _scenario_trial_ids(
        second_runs
    )

    assert set(first_trials) == {
        "approval_binding_benign",
        "approval_binding_unsafe",
    }

    assert set(second_trials) == set(
        first_trials
    )

    for scenario_id in first_trials:
        assert len(
            first_trials[scenario_id]
        ) == 1

        assert len(
            second_trials[scenario_id]
        ) == 1

        assert (
            first_trials[scenario_id]
            != second_trials[scenario_id]
        )

    for runs, expected_index in (
        (first_runs, 0),
        (second_runs, 1),
    ):
        for run in runs:
            evidence_path = Path(
                str(run["evidence_path"])
            )

            first_event = json.loads(
                evidence_path.read_text(
                    encoding="utf-8",
                ).splitlines()[0]
            )

            metadata = first_event[
                "payload"
            ][
                "metadata"
            ]

            assert (
                metadata["trial_id"]
                == run["trial_id"]
            )

            assert (
                metadata[
                    "repetition_index"
                ]
                == expected_index
            )
