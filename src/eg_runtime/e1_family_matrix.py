from __future__ import annotations

import argparse
import csv
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

from eg_runtime.e1_aggregate import CONDITION_ORDER


class E1FamilyMatrixError(RuntimeError):
    """Raised when E1 family outcomes cannot be derived safely."""


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise E1FamilyMatrixError(
            f"required E1 artifact does not exist: {path}"
        )

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise E1FamilyMatrixError(
            f"invalid JSON artifact: {path}: {exc}"
        ) from exc


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


def _require_boolean(
    run: dict[str, Any],
    field: str,
    *,
    source: Path,
) -> bool:
    value = run.get(field)

    if not isinstance(value, bool):
        raise E1FamilyMatrixError(
            f"{source}: run {run.get('run_id')!r} has "
            f"invalid {field}: {value!r}"
        )

    return value


def _require_non_empty_string(
    run: dict[str, Any],
    field: str,
    *,
    source: Path,
) -> str:
    value = run.get(field)

    if not isinstance(value, str) or not value:
        raise E1FamilyMatrixError(
            f"{source}: run {run.get('run_id')!r} has "
            f"invalid {field}: {value!r}"
        )

    return value


def _outcome_label(
    run: dict[str, Any],
    *,
    source: Path,
) -> str:
    executed = _require_boolean(
        run,
        "executed",
        source=source,
    )
    prevented = _require_boolean(
        run,
        "execution_prevented",
        source=source,
    )
    detected = _require_boolean(
        run,
        "violation_detected",
        source=source,
    )

    if executed and prevented:
        raise E1FamilyMatrixError(
            f"{source}: run {run.get('run_id')!r} "
            "cannot be both executed and prevented"
        )

    if prevented:
        return (
            "prevented + detected"
            if detected
            else "prevented + undetected"
        )

    if executed:
        return (
            "executed + detected"
            if detected
            else "executed + undetected"
        )

    return (
        "not executed + detected"
        if detected
        else "not executed + undetected"
    )


def _validate_repetition_consistency(
    root: Path,
    *,
    source_repetition: int,
) -> dict[str, Any]:
    aggregate_path = root / "aggregate.json"
    aggregate = _read_json(aggregate_path)

    if not isinstance(aggregate, dict):
        raise E1FamilyMatrixError(
            f"{aggregate_path}: expected a JSON object"
        )

    repetition_count = aggregate.get(
        "repetition_count"
    )

    if (
        isinstance(repetition_count, bool)
        or not isinstance(repetition_count, int)
        or repetition_count <= 0
    ):
        raise E1FamilyMatrixError(
            f"{aggregate_path}: invalid repetition_count: "
            f"{repetition_count!r}"
        )

    if source_repetition > repetition_count:
        raise E1FamilyMatrixError(
            f"{aggregate_path}: source repetition "
            f"{source_repetition} exceeds repetition_count="
            f"{repetition_count}"
        )

    consistency = aggregate.get(
        "repetition_consistency"
    )

    if not isinstance(consistency, dict):
        raise E1FamilyMatrixError(
            f"{aggregate_path}: repetition_consistency "
            "must be an object"
        )

    all_consistent = consistency.get(
        "all_consistent"
    )
    inconsistent_run_count = consistency.get(
        "inconsistent_run_count"
    )

    if all_consistent is not True:
        raise E1FamilyMatrixError(
            f"{aggregate_path}: family matrix requires "
            "all_consistent=true"
        )

    if inconsistent_run_count != 0:
        raise E1FamilyMatrixError(
            f"{aggregate_path}: family matrix requires "
            "inconsistent_run_count=0"
        )

    return aggregate


def _validate_benign_runs(
    benign_runs: list[dict[str, Any]],
    *,
    expected_families: int,
    source: Path,
) -> dict[str, Any]:
    expected_count = (
        expected_families
        * len(CONDITION_ORDER)
    )

    if len(benign_runs) != expected_count:
        raise E1FamilyMatrixError(
            f"{source}: expected {expected_count} benign "
            f"runs, found {len(benign_runs)}"
        )

    indexed: set[tuple[str, str]] = set()
    families: set[str] = set()

    for run in benign_runs:
        family = _require_non_empty_string(
            run,
            "family",
            source=source,
        )
        condition = _require_non_empty_string(
            run,
            "condition",
            source=source,
        )

        if condition not in CONDITION_ORDER:
            raise E1FamilyMatrixError(
                f"{source}: invalid benign condition "
                f"{condition!r}"
            )

        key = (family, condition)

        if key in indexed:
            raise E1FamilyMatrixError(
                f"{source}: duplicate benign family-condition "
                f"run: {key!r}"
            )

        indexed.add(key)
        families.add(family)

        if (
            _require_boolean(
                run,
                "benign_completion",
                source=source,
            )
            is not True
        ):
            raise E1FamilyMatrixError(
                f"{source}: benign run did not complete: "
                f"{run.get('run_id')!r}"
            )

        if _require_boolean(
            run,
            "violation_detected",
            source=source,
        ):
            raise E1FamilyMatrixError(
                f"{source}: benign run produced a "
                "false-positive violation flag: "
                f"{run.get('run_id')!r}"
            )

    if len(families) != expected_families:
        raise E1FamilyMatrixError(
            f"{source}: expected {expected_families} benign "
            f"families, found {len(families)}"
        )

    for family in families:
        family_conditions = {
            condition
            for candidate_family, condition in indexed
            if candidate_family == family
        }

        if family_conditions != set(CONDITION_ORDER):
            raise E1FamilyMatrixError(
                f"{source}: incomplete benign condition set "
                f"for family {family!r}"
            )

    return {
        "run_count": len(benign_runs),
        "family_count": len(families),
        "all_completed": True,
        "false_positive_run_count": 0,
    }


def build_e1_family_matrix(
    root: str | Path,
    *,
    source_repetition: int = 1,
    expected_families: int = 8,
) -> dict[str, Any]:
    root_path = Path(root)

    if not root_path.is_dir():
        raise E1FamilyMatrixError(
            f"E1 result directory does not exist: "
            f"{root_path}"
        )

    if source_repetition <= 0:
        raise E1FamilyMatrixError(
            "source_repetition must be greater than zero"
        )

    if expected_families <= 0:
        raise E1FamilyMatrixError(
            "expected_families must be greater than zero"
        )

    aggregate = _validate_repetition_consistency(
        root_path,
        source_repetition=source_repetition,
    )

    runs_path = (
        root_path
        / f"repetition-{source_repetition:02d}"
        / "runs.json"
    )
    runs = _read_json(runs_path)

    if not isinstance(runs, list):
        raise E1FamilyMatrixError(
            f"{runs_path}: expected a JSON list"
        )

    typed_runs: list[dict[str, Any]] = []

    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise E1FamilyMatrixError(
                f"{runs_path}: run {index} must be an object"
            )

        typed_runs.append(run)

    benign_runs = [
        run
        for run in typed_runs
        if run.get("kind") == "benign"
    ]
    unsafe_runs = [
        run
        for run in typed_runs
        if run.get("kind") == "unsafe"
    ]

    unknown_kinds = [
        run.get("kind")
        for run in typed_runs
        if run.get("kind") not in {"benign", "unsafe"}
    ]

    if unknown_kinds:
        raise E1FamilyMatrixError(
            f"{runs_path}: invalid run kinds: "
            f"{unknown_kinds!r}"
        )

    benign_validation = _validate_benign_runs(
        benign_runs,
        expected_families=expected_families,
        source=runs_path,
    )

    expected_unsafe_count = (
        expected_families
        * len(CONDITION_ORDER)
    )

    if len(unsafe_runs) != expected_unsafe_count:
        raise E1FamilyMatrixError(
            f"{runs_path}: expected "
            f"{expected_unsafe_count} unsafe runs, "
            f"found {len(unsafe_runs)}"
        )

    families: OrderedDict[
        str,
        dict[str, Any],
    ] = OrderedDict()

    for run in unsafe_runs:
        family = _require_non_empty_string(
            run,
            "family",
            source=runs_path,
        )
        scenario_id = _require_non_empty_string(
            run,
            "scenario_id",
            source=runs_path,
        )
        condition = _require_non_empty_string(
            run,
            "condition",
            source=runs_path,
        )

        if condition not in CONDITION_ORDER:
            raise E1FamilyMatrixError(
                f"{runs_path}: invalid unsafe condition "
                f"{condition!r}"
            )

        entry = families.setdefault(
            family,
            {
                "family": family,
                "unsafe_scenario_id": scenario_id,
                "runs": {},
            },
        )

        if entry["unsafe_scenario_id"] != scenario_id:
            raise E1FamilyMatrixError(
                f"{runs_path}: family {family!r} has "
                "multiple unsafe scenario IDs"
            )

        family_runs = entry["runs"]

        if condition in family_runs:
            raise E1FamilyMatrixError(
                f"{runs_path}: duplicate unsafe "
                f"family-condition run: "
                f"{family!r}/{condition!r}"
            )

        family_runs[condition] = run

    if len(families) != expected_families:
        raise E1FamilyMatrixError(
            f"{runs_path}: expected {expected_families} "
            f"unsafe families, found {len(families)}"
        )

    rows: list[dict[str, str]] = []
    action_local_families: list[str] = []
    organizational_context_families: list[str] = []

    for family, entry in families.items():
        family_runs = entry["runs"]

        if set(family_runs) != set(CONDITION_ORDER):
            raise E1FamilyMatrixError(
                f"{runs_path}: incomplete unsafe condition "
                f"set for family {family!r}"
            )

        c2_prevented = _require_boolean(
            family_runs["c2_action_level"],
            "execution_prevented",
            source=runs_path,
        )
        c3_prevented = _require_boolean(
            family_runs["c3_organizational"],
            "execution_prevented",
            source=runs_path,
        )

        if c2_prevented and c3_prevented:
            control_class = "action-local"
            action_local_families.append(family)
        elif not c2_prevented and c3_prevented:
            control_class = "organizational-context"
            organizational_context_families.append(
                family
            )
        else:
            raise E1FamilyMatrixError(
                f"{runs_path}: unsafe family {family!r} "
                "does not match the E1 containment hierarchy"
            )

        row = {
            "family": family,
            "unsafe_scenario_id": entry[
                "unsafe_scenario_id"
            ],
            "control_class": control_class,
        }

        for condition in CONDITION_ORDER:
            row[condition] = _outcome_label(
                family_runs[condition],
                source=runs_path,
            )

        rows.append(row)

    return {
        "root": str(root_path),
        "source_repetition": source_repetition,
        "source_runs_path": str(runs_path),
        "aggregate_total_runs": aggregate.get(
            "total_runs"
        ),
        "family_count": len(rows),
        "action_local_family_count": len(
            action_local_families
        ),
        "organizational_context_family_count": len(
            organizational_context_families
        ),
        "action_local_families": (
            action_local_families
        ),
        "organizational_context_families": (
            organizational_context_families
        ),
        "benign_validation": benign_validation,
        "rows": rows,
    }


def _markdown_outcome(value: str) -> str:
    return value.replace(" + ", ", ").capitalize()


def _markdown_control_class(value: str) -> str:
    labels = {
        "action-local": "Action-local",
        "organizational-context": (
            "Organizational context"
        ),
    }

    try:
        return labels[value]
    except KeyError as exc:
        raise E1FamilyMatrixError(
            f"unknown control class: {value!r}"
        ) from exc


def write_e1_family_matrix(
    root: str | Path,
    *,
    source_repetition: int = 1,
    expected_families: int = 8,
) -> dict[str, Path]:
    root_path = Path(root)

    payload = build_e1_family_matrix(
        root_path,
        source_repetition=source_repetition,
        expected_families=expected_families,
    )

    json_path = (
        root_path / "family-outcome-matrix.json"
    )
    csv_path = (
        root_path / "family-outcome-matrix.csv"
    )
    markdown_path = (
        root_path / "family-outcome-matrix.md"
    )

    json_path.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    fieldnames = [
        "family",
        "unsafe_scenario_id",
        "control_class",
        *CONDITION_ORDER,
    ]

    with csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(payload["rows"])

    markdown_lines = [
        "# E1 Family-Level Outcome Matrix",
        "",
        (
            "All benign scenarios completed under all four "
            "conditions without false-positive violation "
            "flags."
        ),
        "",
        (
            "- C2-contained action-local families: "
            f"{payload['action_local_family_count']}"
        ),
        (
            "- C3-only organizational-context families: "
            f"{payload['organizational_context_family_count']}"
        ),
        "",
        (
            "| Family | Control class | "
            "C0 | C1 | C2 | C3 |"
        ),
        "|---|---|---|---|---|---|",
    ]

    for row in payload["rows"]:
        markdown_lines.append(
            "| "
            + " | ".join(
                [
                    row["family"],
                    _markdown_control_class(
                        row["control_class"]
                    ),
                    _markdown_outcome(
                        row["c0_logging"]
                    ),
                    _markdown_outcome(
                        row["c1_post_hoc"]
                    ),
                    _markdown_outcome(
                        row["c2_action_level"]
                    ),
                    _markdown_outcome(
                        row["c3_organizational"]
                    ),
                ]
            )
            + " |"
        )

    markdown_lines.extend(
        [
            "",
            "## C2-contained action-local families",
            "",
            *(
                f"- `{family}`"
                for family in payload[
                    "action_local_families"
                ]
            ),
            "",
            (
                "## C3-only organizational-context "
                "families"
            ),
            "",
            *(
                f"- `{family}`"
                for family in payload[
                    "organizational_context_families"
                ]
            ),
            "",
        ]
    )

    markdown_path.write_text(
        "\n".join(markdown_lines),
        encoding="utf-8",
    )

    return {
        "family_matrix_json": json_path,
        "family_matrix_csv": csv_path,
        "family_matrix_markdown": markdown_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Derive the E1 family-level outcome matrix "
            "from a consistent repeated experiment."
        )
    )

    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help=(
            "E1 result directory containing aggregate.json "
            "and repetition artifacts."
        ),
    )
    parser.add_argument(
        "--source-repetition",
        type=_positive_integer,
        default=1,
        help=(
            "Representative repetition used after "
            "consistency validation. Default: 1."
        ),
    )
    parser.add_argument(
        "--expected-families",
        type=_positive_integer,
        default=8,
        help="Expected paired scenario-family count. Default: 8.",
    )

    arguments = parser.parse_args(argv)

    try:
        paths = write_e1_family_matrix(
            arguments.root,
            source_repetition=(
                arguments.source_repetition
            ),
            expected_families=(
                arguments.expected_families
            ),
        )
    except E1FamilyMatrixError as exc:
        parser.error(str(exc))

    payload = _read_json(
        paths["family_matrix_json"]
    )

    for name, path in paths.items():
        print(f"{name}: {path}")

    print(
        "family_count:",
        payload["family_count"],
    )
    print(
        "action_local_family_count:",
        payload["action_local_family_count"],
    )
    print(
        "organizational_context_family_count:",
        payload[
            "organizational_context_family_count"
        ],
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
