from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from eg_runtime.e1_family_matrix import (
    E1FamilyMatrixError,
    build_e1_family_matrix,
    main,
    write_e1_family_matrix,
)


CONDITIONS = (
    "c0_logging",
    "c1_post_hoc",
    "c2_action_level",
    "c3_organizational",
)

FAMILIES = (
    (
        "data_classification",
        "action-local",
    ),
    (
        "approval_binding",
        "organizational-context",
    ),
)


def _write_artifacts(
    tmp_path: Path,
    *,
    all_consistent: bool = True,
    benign_false_positive: bool = False,
    leave_contextual_family_uncontained: bool = False,
) -> Path:
    root = tmp_path / "e1-results"
    repetition = root / "repetition-01"

    repetition.mkdir(parents=True)

    aggregate = {
        "repetition_count": 2,
        "scenario_count": 4,
        "condition_count": 4,
        "conditions": list(CONDITIONS),
        "runs_per_repetition": 16,
        "total_runs": 32,
        "repetition_consistency": {
            "all_consistent": all_consistent,
            "inconsistent_run_count": (
                0 if all_consistent else 1
            ),
        },
    }

    runs: list[dict[str, object]] = []

    for family, control_class in FAMILIES:
        for condition in CONDITIONS:
            benign_run = {
                "run_id": (
                    f"{family}_benign--{condition}"
                ),
                "scenario_id": f"{family}_benign",
                "family": family,
                "kind": "benign",
                "condition": condition,
                "benign_completion": True,
                "violation_detected": (
                    benign_false_positive
                    and family == "approval_binding"
                    and condition == "c3_organizational"
                ),
            }

            runs.append(benign_run)

            if condition == "c0_logging":
                executed = True
                prevented = False
                detected = False
            elif condition == "c1_post_hoc":
                executed = True
                prevented = False
                detected = True
            elif condition == "c2_action_level":
                if control_class == "action-local":
                    executed = False
                    prevented = True
                    detected = True
                else:
                    executed = True
                    prevented = False
                    detected = False
            else:
                executed = False
                prevented = True
                detected = True

            if (
                leave_contextual_family_uncontained
                and family == "approval_binding"
                and condition == "c3_organizational"
            ):
                executed = True
                prevented = False
                detected = False

            unsafe_run = {
                "run_id": (
                    f"{family}_unsafe--{condition}"
                ),
                "scenario_id": f"{family}_unsafe",
                "family": family,
                "kind": "unsafe",
                "condition": condition,
                "executed": executed,
                "execution_prevented": prevented,
                "violation_detected": detected,
            }

            runs.append(unsafe_run)

    (root / "aggregate.json").write_text(
        json.dumps(
            aggregate,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    (repetition / "runs.json").write_text(
        json.dumps(
            runs,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return root


def test_build_e1_family_matrix_classifies_families(
    tmp_path: Path,
) -> None:
    root = _write_artifacts(tmp_path)

    payload = build_e1_family_matrix(
        root,
        expected_families=2,
    )

    assert payload["source_repetition"] == 1
    assert payload["aggregate_total_runs"] == 32
    assert payload["family_count"] == 2

    assert payload["action_local_family_count"] == 1
    assert (
        payload[
            "organizational_context_family_count"
        ]
        == 1
    )

    assert payload["action_local_families"] == [
        "data_classification",
    ]
    assert payload[
        "organizational_context_families"
    ] == [
        "approval_binding",
    ]

    assert payload["benign_validation"] == {
        "run_count": 8,
        "family_count": 2,
        "all_completed": True,
        "false_positive_run_count": 0,
    }

    rows = {
        row["family"]: row
        for row in payload["rows"]
    }

    action_local = rows["data_classification"]

    assert (
        action_local["control_class"]
        == "action-local"
    )
    assert (
        action_local["c0_logging"]
        == "executed + undetected"
    )
    assert (
        action_local["c1_post_hoc"]
        == "executed + detected"
    )
    assert (
        action_local["c2_action_level"]
        == "prevented + detected"
    )
    assert (
        action_local["c3_organizational"]
        == "prevented + detected"
    )

    contextual = rows["approval_binding"]

    assert (
        contextual["control_class"]
        == "organizational-context"
    )
    assert (
        contextual["c2_action_level"]
        == "executed + undetected"
    )
    assert (
        contextual["c3_organizational"]
        == "prevented + detected"
    )


def test_write_e1_family_matrix_writes_artifacts(
    tmp_path: Path,
) -> None:
    root = _write_artifacts(tmp_path)

    paths = write_e1_family_matrix(
        root,
        expected_families=2,
    )

    assert set(paths) == {
        "family_matrix_json",
        "family_matrix_csv",
        "family_matrix_markdown",
    }

    assert all(
        path.is_file()
        for path in paths.values()
    )

    payload = json.loads(
        paths["family_matrix_json"].read_text(
            encoding="utf-8"
        )
    )

    assert payload["family_count"] == 2

    with paths["family_matrix_csv"].open(
        encoding="utf-8",
        newline="",
    ) as stream:
        rows = list(csv.DictReader(stream))

    assert len(rows) == 2
    assert {
        row["family"]
        for row in rows
    } == {
        "data_classification",
        "approval_binding",
    }

    markdown = paths[
        "family_matrix_markdown"
    ].read_text(encoding="utf-8")

    assert (
        "C2-contained action-local families: 1"
        in markdown
    )
    assert (
        "C3-only organizational-context families: 1"
        in markdown
    )
    assert "`data_classification`" in markdown
    assert "`approval_binding`" in markdown


def test_build_rejects_inconsistent_repetitions(
    tmp_path: Path,
) -> None:
    root = _write_artifacts(
        tmp_path,
        all_consistent=False,
    )

    with pytest.raises(
        E1FamilyMatrixError,
        match="all_consistent=true",
    ):
        build_e1_family_matrix(
            root,
            expected_families=2,
        )


def test_build_rejects_benign_false_positive(
    tmp_path: Path,
) -> None:
    root = _write_artifacts(
        tmp_path,
        benign_false_positive=True,
    )

    with pytest.raises(
        E1FamilyMatrixError,
        match="false-positive violation flag",
    ):
        build_e1_family_matrix(
            root,
            expected_families=2,
        )


def test_build_rejects_family_uncontained_under_c3(
    tmp_path: Path,
) -> None:
    root = _write_artifacts(
        tmp_path,
        leave_contextual_family_uncontained=True,
    )

    with pytest.raises(
        E1FamilyMatrixError,
        match="does not match the E1 containment hierarchy",
    ):
        build_e1_family_matrix(
            root,
            expected_families=2,
        )


def test_e1_family_matrix_cli_writes_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _write_artifacts(tmp_path)

    result = main(
        [
            "--root",
            str(root),
            "--expected-families",
            "2",
        ]
    )

    assert result == 0

    assert (
        root / "family-outcome-matrix.json"
    ).is_file()
    assert (
        root / "family-outcome-matrix.csv"
    ).is_file()
    assert (
        root / "family-outcome-matrix.md"
    ).is_file()

    captured = capsys.readouterr()

    assert "family_count: 2" in captured.out
    assert (
        "action_local_family_count: 1"
        in captured.out
    )
    assert (
        "organizational_context_family_count: 1"
        in captured.out
    )
