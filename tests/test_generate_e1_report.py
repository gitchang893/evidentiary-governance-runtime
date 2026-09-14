from __future__ import annotations

import json
from pathlib import Path

import pytest

from eg_runtime.e1_report import (
    CONDITION_LABELS,
    METRICS,
    ReportGenerationError,
    build_report,
    main,
    validate_fences,
    validate_report,
)


def _write_source_artifacts(
    tmp_path: Path,
    *,
    c2_unsafe_execution_rate: float = 0.5,
    config_scenario_count: int = 4,
) -> tuple[Path, Path]:
    root = tmp_path / "results"
    root.mkdir()

    conditions = tuple(CONDITION_LABELS)

    means = {
        "c0_logging": {
            "benign_completion_rate": 1.0,
            "unsafe_execution_rate": 1.0,
            "violation_detection_recall": 0.0,
            "containment_success_rate": 0.0,
            "false_positive_rate": 0.0,
            "mean_evidence_completeness": 0.4,
            "evidence_validity_rate": 1.0,
            "semantic_evidence_validity_rate": 1.0,
            "preservation_bundle_validity_rate": 1.0,
            "preservation_directive_accuracy": 0.5,
        },
        "c1_post_hoc": {
            "benign_completion_rate": 1.0,
            "unsafe_execution_rate": 1.0,
            "violation_detection_recall": 1.0,
            "containment_success_rate": 0.0,
            "false_positive_rate": 0.0,
            "mean_evidence_completeness": 1.0,
            "evidence_validity_rate": 1.0,
            "semantic_evidence_validity_rate": 1.0,
            "preservation_bundle_validity_rate": 1.0,
            "preservation_directive_accuracy": 1.0,
        },
        "c2_action_level": {
            "benign_completion_rate": 1.0,
            "unsafe_execution_rate": (
                c2_unsafe_execution_rate
            ),
            "violation_detection_recall": 0.5,
            "containment_success_rate": 0.5,
            "false_positive_rate": 0.0,
            "mean_evidence_completeness": 0.8,
            "evidence_validity_rate": 1.0,
            "semantic_evidence_validity_rate": 1.0,
            "preservation_bundle_validity_rate": 1.0,
            "preservation_directive_accuracy": 0.75,
        },
        "c3_organizational": {
            "benign_completion_rate": 1.0,
            "unsafe_execution_rate": 0.0,
            "violation_detection_recall": 1.0,
            "containment_success_rate": 1.0,
            "false_positive_rate": 0.0,
            "mean_evidence_completeness": 1.0,
            "evidence_validity_rate": 1.0,
            "semantic_evidence_validity_rate": 1.0,
            "preservation_bundle_validity_rate": 1.0,
            "preservation_directive_accuracy": 1.0,
        },
    }

    condition_metrics = {
        condition: {
            metric: {
                "mean": means[condition][metric],
                "population_standard_deviation": 0.0,
            }
            for metric, _ in METRICS
        }
        for condition in conditions
    }

    aggregate = {
        "repetition_count": 2,
        "scenario_count": 4,
        "condition_count": 4,
        "conditions": list(conditions),
        "runs_per_repetition": 16,
        "total_runs": 32,
        "condition_metrics": condition_metrics,
        "repetition_consistency": {
            "all_consistent": True,
            "inconsistent_run_count": 0,
        },
    }

    family_payload = {
        "family_count": 2,
        "rows": [
            {
                "family": "data_classification",
                "unsafe_scenario_id": (
                    "data_classification_unsafe"
                ),
                "control_class": "action-local",
                "c0_logging": (
                    "executed + undetected"
                ),
                "c1_post_hoc": (
                    "executed + detected"
                ),
                "c2_action_level": (
                    "prevented + detected"
                ),
                "c3_organizational": (
                    "prevented + detected"
                ),
            },
            {
                "family": "approval_binding",
                "unsafe_scenario_id": (
                    "approval_binding_unsafe"
                ),
                "control_class": (
                    "organizational-context"
                ),
                "c0_logging": (
                    "executed + undetected"
                ),
                "c1_post_hoc": (
                    "executed + detected"
                ),
                "c2_action_level": (
                    "executed + undetected"
                ),
                "c3_organizational": (
                    "prevented + detected"
                ),
            },
        ],
    }

    config = {
        "experiment_id": "test-e1",
        "scenario_directory": "scenarios/canonical",
        "scenario_ids": [
            f"scenario-{index}"
            for index in range(
                config_scenario_count
            )
        ],
        "conditions": list(conditions),
        "repetitions": 2,
    }

    (root / "aggregate.json").write_text(
        json.dumps(
            aggregate,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    (
        root / "family-outcome-matrix.json"
    ).write_text(
        json.dumps(
            family_payload,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    config_path = tmp_path / "e1-config.json"
    config_path.write_text(
        json.dumps(
            config,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return root, config_path


def test_report_cli_generates_valid_markdown(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, config_path = _write_source_artifacts(
        tmp_path
    )
    output_path = tmp_path / "report.md"

    result = main(
        [
            "--root",
            str(root),
            "--config",
            str(config_path),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    assert output_path.is_file()

    report = output_path.read_text(
        encoding="utf-8"
    )

    fenced_blocks, table_count = validate_report(
        report
    )

    assert fenced_blocks == 8
    assert table_count == 2

    captured = capsys.readouterr()

    assert (
        "validated Markdown fences: 8"
        in captured.out
    )
    assert (
        "validated Markdown tables: 2"
        in captured.out
    )
    assert (
        "validated required headings: 9"
        in captured.out
    )


def test_report_uses_aggregate_metric_values(
    tmp_path: Path,
) -> None:
    root, config_path = _write_source_artifacts(
        tmp_path,
        c2_unsafe_execution_rate=0.25,
    )

    report = build_report(
        root=root,
        config_path=config_path,
    )

    assert (
        "| Unsafe execution rate | "
        "100.00% | 100.00% | 25.00% | 0.00% |"
        in report
    )

    assert (
        "reducing unsafe execution to 25.00%"
        in report
    )


def test_report_rejects_config_scenario_mismatch(
    tmp_path: Path,
) -> None:
    root, config_path = _write_source_artifacts(
        tmp_path,
        config_scenario_count=3,
    )

    with pytest.raises(
        ReportGenerationError,
        match="scenario count 3 does not match",
    ):
        build_report(
            root=root,
            config_path=config_path,
        )


def test_report_cli_rejects_missing_family_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, config_path = _write_source_artifacts(
        tmp_path
    )

    (
        root / "family-outcome-matrix.json"
    ).unlink()

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--root",
                str(root),
                "--config",
                str(config_path),
                "--output",
                str(tmp_path / "report.md"),
            ]
        )

    assert exc_info.value.code == 2

    captured = capsys.readouterr()

    assert (
        "family-outcome-matrix.json"
        in captured.err
    )
    assert (
        "does not exist"
        in captured.err
    )


def test_validate_fences_rejects_unclosed_block(
) -> None:
    markdown = (
        "# Report\n"
        "\n"
        "```text\n"
        "unclosed block\n"
    )

    with pytest.raises(
        ReportGenerationError,
        match="unclosed Markdown fence",
    ):
        validate_fences(markdown)


def test_report_cli_requires_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--root",
                str(tmp_path),
            ]
        )

    assert exc_info.value.code == 2

    captured = capsys.readouterr()

    assert "--output" in captured.err
    assert "required" in captured.err
