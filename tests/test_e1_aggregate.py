from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Callable

import pytest

from eg_runtime.e1_aggregate import (
    CONDITION_ORDER,
    E1AggregationError,
    aggregate_e1_results,
    main,
    write_e1_aggregate,
)


SCENARIO_IDS = (
    "scenario_a",
    "scenario_b",
)

BASE_UNSAFE_EXECUTION_RATES = {
    "c0_logging": 1.0,
    "c1_post_hoc": 1.0,
    "c2_action_level": 0.25,
    "c3_organizational": 0.0,
}


def _write_repetition(
    root: Path,
    repetition_number: int,
    *,
    metric_overrides: (
        dict[str, dict[str, object]] | None
    ) = None,
    run_mutator: (
        Callable[[dict[str, object]], None] | None
    ) = None,
) -> None:
    directory = (
        root
        / f"repetition-{repetition_number:02d}"
    )
    directory.mkdir(parents=True)

    conditions: dict[str, dict[str, object]] = {}

    for condition in CONDITION_ORDER:
        condition_summary: dict[str, object] = {
            "condition": condition,
            "runs": len(SCENARIO_IDS),
            "unsafe_execution_rate": (
                BASE_UNSAFE_EXECUTION_RATES[
                    condition
                ]
            ),
            "mean_evidence_completeness": 0.75,
            "decision_accuracy": None,
        }

        if (
            metric_overrides is not None
            and condition in metric_overrides
        ):
            condition_summary.update(
                metric_overrides[condition]
            )

        conditions[condition] = condition_summary

    total_runs = (
        len(SCENARIO_IDS)
        * len(CONDITION_ORDER)
    )

    summary = {
        "conditions": conditions,
        "scenario_count": len(SCENARIO_IDS),
        "total_runs": total_runs,
    }

    runs: list[dict[str, object]] = []

    for scenario_id in SCENARIO_IDS:
        for condition in CONDITION_ORDER:
            if repetition_number % 2:
                evidence_missing = [
                    "approval",
                    "declared_plan",
                ]
                semantic_issue_codes = [
                    "issue_b",
                    "issue_a",
                ]
            else:
                evidence_missing = [
                    "declared_plan",
                    "approval",
                ]
                semantic_issue_codes = [
                    "issue_a",
                    "issue_b",
                ]

            run: dict[str, object] = {
                "scenario_id": scenario_id,
                "condition": condition,
                "run_id": (
                    f"{scenario_id}--{condition}"
                ),
                "executed": True,
                "operational_outcome": "executed",
                "evidence_missing": evidence_missing,
                "semantic_issue_codes": (
                    semantic_issue_codes
                ),
                "evidence_final_hash": (
                    f"hash-r{repetition_number}-"
                    f"{scenario_id}-{condition}"
                ),
                "evidence_path": (
                    f"repetition-"
                    f"{repetition_number:02d}/"
                    f"evidence/{scenario_id}/"
                    f"{condition}.jsonl"
                ),
                "preservation_bundle_path": (
                    f"repetition-"
                    f"{repetition_number:02d}/"
                    f"preservation/{scenario_id}/"
                    f"{condition}.json"
                ),
            }

            if run_mutator is not None:
                run_mutator(run)

            runs.append(run)

    (directory / "summary.json").write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    (directory / "runs.json").write_text(
        json.dumps(
            runs,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_aggregate_e1_results_detects_consistent_runs(
    tmp_path: Path,
) -> None:
    _write_repetition(tmp_path, 1)
    _write_repetition(tmp_path, 2)

    aggregate = aggregate_e1_results(
        tmp_path,
        expected_repetitions=2,
        expected_scenarios=2,
        expected_runs_per_repetition=8,
    )

    assert aggregate["repetition_count"] == 2
    assert aggregate["scenario_count"] == 2
    assert aggregate["condition_count"] == 4
    assert aggregate["runs_per_repetition"] == 8
    assert aggregate["total_runs"] == 16

    consistency = aggregate[
        "repetition_consistency"
    ]

    assert consistency["all_consistent"] is True
    assert consistency["compared_run_keys"] == 8
    assert consistency["comparison_count"] == 8
    assert consistency["inconsistent_run_count"] == 0
    assert consistency["inconsistent_runs"] == []

    assert set(
        consistency["excluded_volatile_fields"]
    ) == {
        "evidence_final_hash",
        "evidence_path",
        "preservation_bundle_path",
    }

    assert set(
        consistency["order_insensitive_fields"]
    ) == {
        "evidence_missing",
        "semantic_issue_codes",
    }


def test_aggregate_e1_results_summarizes_metrics(
    tmp_path: Path,
) -> None:
    _write_repetition(tmp_path, 1)
    _write_repetition(
        tmp_path,
        2,
        metric_overrides={
            "c2_action_level": {
                "unsafe_execution_rate": 0.75,
            },
        },
    )

    aggregate = aggregate_e1_results(
        tmp_path,
        expected_repetitions=2,
        expected_scenarios=2,
        expected_runs_per_repetition=8,
    )

    metric = aggregate[
        "condition_metrics"
    ]["c2_action_level"]["unsafe_execution_rate"]

    assert metric["values"] == [0.25, 0.75]
    assert metric["non_null_repetitions"] == 2
    assert metric["mean"] == pytest.approx(0.5)
    assert metric[
        "population_standard_deviation"
    ] == pytest.approx(0.25)
    assert metric["minimum"] == pytest.approx(0.25)
    assert metric["maximum"] == pytest.approx(0.75)
    assert metric["identical"] is False

    decision_accuracy = aggregate[
        "condition_metrics"
    ]["c2_action_level"]["decision_accuracy"]

    assert decision_accuracy["values"] == [
        None,
        None,
    ]
    assert (
        decision_accuracy[
            "non_null_repetitions"
        ]
        == 0
    )
    assert decision_accuracy["mean"] is None
    assert decision_accuracy[
        "population_standard_deviation"
    ] is None
    assert decision_accuracy["minimum"] is None
    assert decision_accuracy["maximum"] is None
    assert decision_accuracy["identical"] is True


def test_aggregate_e1_results_reports_run_difference(
    tmp_path: Path,
) -> None:
    _write_repetition(tmp_path, 1)

    def mutate_run(
        run: dict[str, object],
    ) -> None:
        if (
            run["scenario_id"] == "scenario_a"
            and run["condition"]
            == "c3_organizational"
        ):
            run["executed"] = False
            run["operational_outcome"] = (
                "prevented"
            )

    _write_repetition(
        tmp_path,
        2,
        run_mutator=mutate_run,
    )

    aggregate = aggregate_e1_results(
        tmp_path,
        expected_repetitions=2,
        expected_scenarios=2,
        expected_runs_per_repetition=8,
    )

    consistency = aggregate[
        "repetition_consistency"
    ]

    assert consistency["all_consistent"] is False
    assert consistency["inconsistent_run_count"] == 1

    difference = consistency[
        "inconsistent_runs"
    ][0]

    assert difference["scenario_id"] == "scenario_a"
    assert (
        difference["condition"]
        == "c3_organizational"
    )
    assert difference["baseline_repetition"] == 1
    assert difference["compared_repetition"] == 2
    assert difference["differing_fields"] == [
        "executed",
        "operational_outcome",
    ]


def test_write_e1_aggregate_writes_artifacts(
    tmp_path: Path,
) -> None:
    _write_repetition(tmp_path, 1)
    _write_repetition(tmp_path, 2)

    paths = write_e1_aggregate(
        tmp_path,
        expected_repetitions=2,
        expected_scenarios=2,
        expected_runs_per_repetition=8,
    )

    assert set(paths) == {
        "aggregate_json",
        "aggregate_csv",
        "repetition_consistency_json",
    }

    assert all(
        path.is_file()
        for path in paths.values()
    )

    aggregate = json.loads(
        paths["aggregate_json"].read_text(
            encoding="utf-8"
        )
    )

    consistency = json.loads(
        paths[
            "repetition_consistency_json"
        ].read_text(
            encoding="utf-8"
        )
    )

    assert aggregate["total_runs"] == 16
    assert consistency["all_consistent"] is True
    assert (
        consistency
        == aggregate["repetition_consistency"]
    )

    with paths["aggregate_csv"].open(
        encoding="utf-8",
        newline="",
    ) as stream:
        rows = list(
            csv.DictReader(stream)
        )

    assert len(rows) == 16

    unsafe_execution_row = next(
        row
        for row in rows
        if (
            row["condition"]
            == "c3_organizational"
            and row["metric"]
            == "unsafe_execution_rate"
        )
    )

    assert (
        float(
            unsafe_execution_row[
                "repetition_01"
            ]
        )
        == 0.0
    )
    assert (
        float(
            unsafe_execution_row[
                "repetition_02"
            ]
        )
        == 0.0
    )
    assert (
        float(unsafe_execution_row["mean"])
        == 0.0
    )
    assert unsafe_execution_row["identical"] == (
        "True"
    )


def test_aggregate_e1_results_rejects_missing_artifact(
    tmp_path: Path,
) -> None:
    _write_repetition(tmp_path, 1)

    with pytest.raises(
        E1AggregationError,
        match="required E1 artifact does not exist",
    ):
        aggregate_e1_results(
            tmp_path,
            expected_repetitions=2,
            expected_scenarios=2,
            expected_runs_per_repetition=8,
        )


def test_e1_aggregate_cli_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0

    captured = capsys.readouterr()

    assert "Aggregate repeated E1" in captured.out
    assert "--root" in captured.out
    assert "--expected-repetitions" in captured.out


def test_e1_aggregate_cli_requires_root(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2

    captured = capsys.readouterr()

    assert "--root" in captured.err
    assert "required" in captured.err


def test_e1_aggregate_cli_writes_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_repetition(tmp_path, 1)
    _write_repetition(tmp_path, 2)

    result = main(
        [
            "--root",
            str(tmp_path),
            "--expected-repetitions",
            "2",
            "--expected-scenarios",
            "2",
            "--expected-runs-per-repetition",
            "8",
        ]
    )

    assert result == 0

    assert (
        tmp_path / "aggregate.json"
    ).is_file()
    assert (
        tmp_path / "aggregate.csv"
    ).is_file()
    assert (
        tmp_path
        / "repetition-consistency.json"
    ).is_file()

    captured = capsys.readouterr()

    assert "total_runs: 16" in captured.out
    assert "all_consistent: true" in captured.out
    assert (
        "inconsistent_run_count: 0"
        in captured.out
    )

