from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any


CONDITION_ORDER = (
    "c0_logging",
    "c1_post_hoc",
    "c2_action_level",
    "c3_organizational",
)

VOLATILE_RUN_FIELDS = frozenset(
    {
        "evidence_final_hash",
        "evidence_path",
        "preservation_bundle_path",
    }
)

ORDER_INSENSITIVE_RUN_FIELDS = frozenset(
    {
        "evidence_missing",
        "semantic_issue_codes",
    }
)


class E1AggregationError(RuntimeError):
    """Raised when E1 result artifacts cannot be aggregated safely."""


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise E1AggregationError(
            f"required E1 artifact does not exist: {path}"
        )

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise E1AggregationError(
            f"invalid JSON artifact: {path}: {exc}"
        ) from exc


def _require_integer(
    value: Any,
    *,
    expected: int,
    field: str,
    source: Path,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value != expected
    ):
        raise E1AggregationError(
            f"{source}: expected {field}={expected}, "
            f"found {value!r}"
        )


def _run_key(run: dict[str, Any], source: Path) -> tuple[str, str]:
    scenario_id = run.get("scenario_id")
    condition = run.get("condition")

    if not isinstance(scenario_id, str) or not scenario_id:
        raise E1AggregationError(
            f"{source}: run has invalid scenario_id: "
            f"{scenario_id!r}"
        )

    if condition not in CONDITION_ORDER:
        raise E1AggregationError(
            f"{source}: run has invalid condition: "
            f"{condition!r}"
        )

    return scenario_id, condition


def _normalize_run(run: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    for field, value in run.items():
        if field in VOLATILE_RUN_FIELDS:
            continue

        if (
            field in ORDER_INSENSITIVE_RUN_FIELDS
            and isinstance(value, list)
        ):
            normalized[field] = sorted(value)
        else:
            normalized[field] = value

    return normalized


def _load_repetition(
    directory: Path,
    *,
    expected_scenarios: int,
    expected_runs: int,
) -> dict[str, Any]:
    summary_path = directory / "summary.json"
    runs_path = directory / "runs.json"

    summary = _read_json(summary_path)
    runs = _read_json(runs_path)

    if not isinstance(summary, dict):
        raise E1AggregationError(
            f"{summary_path}: expected a JSON object"
        )

    if not isinstance(runs, list):
        raise E1AggregationError(
            f"{runs_path}: expected a JSON list"
        )

    _require_integer(
        summary.get("scenario_count"),
        expected=expected_scenarios,
        field="scenario_count",
        source=summary_path,
    )
    _require_integer(
        summary.get("total_runs"),
        expected=expected_runs,
        field="total_runs",
        source=summary_path,
    )

    if len(runs) != expected_runs:
        raise E1AggregationError(
            f"{runs_path}: expected {expected_runs} runs, "
            f"found {len(runs)}"
        )

    conditions = summary.get("conditions")

    if not isinstance(conditions, dict):
        raise E1AggregationError(
            f"{summary_path}: conditions must be an object"
        )

    if set(conditions) != set(CONDITION_ORDER):
        raise E1AggregationError(
            f"{summary_path}: expected conditions "
            f"{list(CONDITION_ORDER)!r}, found "
            f"{sorted(conditions)!r}"
        )

    expected_condition_runs = (
        expected_runs // len(CONDITION_ORDER)
    )

    for condition in CONDITION_ORDER:
        condition_summary = conditions[condition]

        if not isinstance(condition_summary, dict):
            raise E1AggregationError(
                f"{summary_path}: summary for {condition} "
                "must be an object"
            )

        if condition_summary.get("condition") != condition:
            raise E1AggregationError(
                f"{summary_path}: condition summary mismatch "
                f"for {condition}"
            )

        _require_integer(
            condition_summary.get("runs"),
            expected=expected_condition_runs,
            field=f"conditions.{condition}.runs",
            source=summary_path,
        )

    indexed_runs: dict[tuple[str, str], dict[str, Any]] = {}
    condition_counts = {
        condition: 0
        for condition in CONDITION_ORDER
    }

    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise E1AggregationError(
                f"{runs_path}: run {index} must be an object"
            )

        key = _run_key(run, runs_path)

        if key in indexed_runs:
            raise E1AggregationError(
                f"{runs_path}: duplicate scenario-condition "
                f"run: {key!r}"
            )

        indexed_runs[key] = run
        condition_counts[key[1]] += 1

    for condition, count in condition_counts.items():
        if count != expected_condition_runs:
            raise E1AggregationError(
                f"{runs_path}: expected "
                f"{expected_condition_runs} runs for "
                f"{condition}, found {count}"
            )

    return {
        "directory": directory,
        "summary": summary,
        "runs": runs,
        "indexed_runs": indexed_runs,
    }


def _is_numeric_or_none(value: Any) -> bool:
    return value is None or (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _summarize_metric(values: list[Any]) -> dict[str, Any]:
    if not values:
        raise E1AggregationError(
            "cannot summarize an empty metric series"
        )

    if not all(_is_numeric_or_none(value) for value in values):
        raise E1AggregationError(
            f"metric contains non-numeric values: {values!r}"
        )

    numeric_values = [
        float(value)
        for value in values
        if value is not None
    ]

    if not numeric_values:
        mean = None
        population_standard_deviation = None
        minimum = None
        maximum = None
    else:
        mean = fmean(numeric_values)
        population_standard_deviation = (
            pstdev(numeric_values)
            if len(numeric_values) > 1
            else 0.0
        )
        minimum = min(numeric_values)
        maximum = max(numeric_values)

    return {
        "values": values,
        "non_null_repetitions": len(numeric_values),
        "mean": mean,
        "population_standard_deviation": (
            population_standard_deviation
        ),
        "minimum": minimum,
        "maximum": maximum,
        "identical": all(
            value == values[0]
            for value in values[1:]
        ),
    }


def _aggregate_condition_metrics(
    repetitions: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    aggregate: dict[
        str,
        dict[str, dict[str, Any]],
    ] = {}

    for condition in CONDITION_ORDER:
        summaries = [
            repetition["summary"]["conditions"][condition]
            for repetition in repetitions
        ]

        baseline_fields = set(summaries[0])
        baseline_fields.discard("condition")

        for index, summary in enumerate(
            summaries[1:],
            start=2,
        ):
            fields = set(summary)
            fields.discard("condition")

            if fields != baseline_fields:
                raise E1AggregationError(
                    f"repetition-{index:02d}: metric fields "
                    f"for {condition} do not match repetition-01"
                )

        condition_metrics: dict[
            str,
            dict[str, Any],
        ] = {}

        for metric in sorted(baseline_fields):
            values = [
                summary[metric]
                for summary in summaries
            ]

            condition_metrics[metric] = (
                _summarize_metric(values)
            )

        aggregate[condition] = condition_metrics

    return aggregate


def _compare_repetition_runs(
    repetitions: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline = repetitions[0]["indexed_runs"]
    baseline_keys = set(baseline)

    inconsistent_runs: list[dict[str, Any]] = []

    for repetition_number, repetition in enumerate(
        repetitions[1:],
        start=2,
    ):
        indexed_runs = repetition["indexed_runs"]
        keys = set(indexed_runs)

        missing_keys = sorted(baseline_keys - keys)
        unexpected_keys = sorted(keys - baseline_keys)

        if missing_keys or unexpected_keys:
            raise E1AggregationError(
                f"repetition-{repetition_number:02d}: "
                f"run-key mismatch; missing={missing_keys!r}, "
                f"unexpected={unexpected_keys!r}"
            )

        for key in sorted(baseline_keys):
            baseline_run = _normalize_run(baseline[key])
            compared_run = _normalize_run(indexed_runs[key])

            differing_fields = sorted(
                field
                for field in (
                    set(baseline_run)
                    | set(compared_run)
                )
                if baseline_run.get(field)
                != compared_run.get(field)
            )

            if differing_fields:
                inconsistent_runs.append(
                    {
                        "scenario_id": key[0],
                        "condition": key[1],
                        "baseline_repetition": 1,
                        "compared_repetition": (
                            repetition_number
                        ),
                        "differing_fields": (
                            differing_fields
                        ),
                    }
                )

    return {
        "all_consistent": not inconsistent_runs,
        "compared_run_keys": len(baseline_keys),
        "comparison_count": (
            len(baseline_keys)
            * (len(repetitions) - 1)
        ),
        "excluded_volatile_fields": sorted(
            VOLATILE_RUN_FIELDS
        ),
        "order_insensitive_fields": sorted(
            ORDER_INSENSITIVE_RUN_FIELDS
        ),
        "inconsistent_run_count": len(
            inconsistent_runs
        ),
        "inconsistent_runs": inconsistent_runs,
    }


def aggregate_e1_results(
    root: str | Path,
    *,
    expected_repetitions: int = 5,
    expected_scenarios: int = 16,
    expected_runs_per_repetition: int = 64,
) -> dict[str, Any]:
    root_path = Path(root)

    if not root_path.is_dir():
        raise E1AggregationError(
            f"E1 result directory does not exist: "
            f"{root_path}"
        )

    repetition_directories = [
        root_path / f"repetition-{number:02d}"
        for number in range(
            1,
            expected_repetitions + 1,
        )
    ]

    repetitions = [
        _load_repetition(
            directory,
            expected_scenarios=expected_scenarios,
            expected_runs=expected_runs_per_repetition,
        )
        for directory in repetition_directories
    ]

    condition_metrics = _aggregate_condition_metrics(
        repetitions
    )
    repetition_consistency = (
        _compare_repetition_runs(repetitions)
    )

    return {
        "root": str(root_path),
        "repetition_count": expected_repetitions,
        "scenario_count": expected_scenarios,
        "condition_count": len(CONDITION_ORDER),
        "conditions": list(CONDITION_ORDER),
        "runs_per_repetition": (
            expected_runs_per_repetition
        ),
        "total_runs": (
            expected_repetitions
            * expected_runs_per_repetition
        ),
        "condition_metrics": condition_metrics,
        "repetition_consistency": (
            repetition_consistency
        ),
    }


def write_e1_aggregate(
    root: str | Path,
    *,
    expected_repetitions: int = 5,
    expected_scenarios: int = 16,
    expected_runs_per_repetition: int = 64,
) -> dict[str, Path]:
    root_path = Path(root)

    aggregate = aggregate_e1_results(
        root_path,
        expected_repetitions=expected_repetitions,
        expected_scenarios=expected_scenarios,
        expected_runs_per_repetition=(
            expected_runs_per_repetition
        ),
    )

    aggregate_json_path = root_path / "aggregate.json"
    aggregate_csv_path = root_path / "aggregate.csv"
    consistency_path = (
        root_path / "repetition-consistency.json"
    )

    aggregate_json_path.write_text(
        json.dumps(
            aggregate,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    consistency_path.write_text(
        json.dumps(
            aggregate["repetition_consistency"],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    repetition_columns = [
        f"repetition_{number:02d}"
        for number in range(
            1,
            expected_repetitions + 1,
        )
    ]

    fieldnames = [
        "condition",
        "metric",
        *repetition_columns,
        "non_null_repetitions",
        "mean",
        "population_standard_deviation",
        "minimum",
        "maximum",
        "identical",
    ]

    with aggregate_csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fieldnames,
        )
        writer.writeheader()

        for condition in CONDITION_ORDER:
            metrics = aggregate[
                "condition_metrics"
            ][condition]

            for metric in sorted(metrics):
                statistics = metrics[metric]
                row: dict[str, Any] = {
                    "condition": condition,
                    "metric": metric,
                    "non_null_repetitions": (
                        statistics[
                            "non_null_repetitions"
                        ]
                    ),
                    "mean": statistics["mean"],
                    (
                        "population_standard_deviation"
                    ): statistics[
                        "population_standard_deviation"
                    ],
                    "minimum": statistics["minimum"],
                    "maximum": statistics["maximum"],
                    "identical": statistics["identical"],
                }

                for column, value in zip(
                    repetition_columns,
                    statistics["values"],
                    strict=True,
                ):
                    row[column] = (
                        ""
                        if value is None
                        else value
                    )

                writer.writerow(row)

    return {
        "aggregate_json": aggregate_json_path,
        "aggregate_csv": aggregate_csv_path,
        "repetition_consistency_json": (
            consistency_path
        ),
    }


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected an integer, found {value!r}"
        ) from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            "value must be greater than zero"
        )

    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate repeated E1 experiment artifacts "
            "and verify cross-repetition consistency."
        )
    )

    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help=(
            "E1 result directory containing "
            "repetition-01, repetition-02, and so on."
        ),
    )
    parser.add_argument(
        "--expected-repetitions",
        type=_positive_integer,
        default=5,
        help="Expected repetition count. Default: 5.",
    )
    parser.add_argument(
        "--expected-scenarios",
        type=_positive_integer,
        default=16,
        help=(
            "Expected scenario count per repetition. "
            "Default: 16."
        ),
    )
    parser.add_argument(
        "--expected-runs-per-repetition",
        type=_positive_integer,
        default=64,
        help=(
            "Expected governed run count per repetition. "
            "Default: 64."
        ),
    )

    arguments = parser.parse_args(argv)

    try:
        paths = write_e1_aggregate(
            arguments.root,
            expected_repetitions=(
                arguments.expected_repetitions
            ),
            expected_scenarios=(
                arguments.expected_scenarios
            ),
            expected_runs_per_repetition=(
                arguments.expected_runs_per_repetition
            ),
        )
    except E1AggregationError as exc:
        parser.error(str(exc))

    aggregate = _read_json(
        paths["aggregate_json"]
    )
    consistency = aggregate[
        "repetition_consistency"
    ]

    for name, path in paths.items():
        print(f"{name}: {path}")

    print(f"total_runs: {aggregate['total_runs']}")
    print(
        "all_consistent:",
        str(
            consistency["all_consistent"]
        ).lower(),
    )
    print(
        "inconsistent_run_count:",
        consistency["inconsistent_run_count"],
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

