from __future__ import annotations

import json
from pathlib import Path

import pytest

from eg_runtime.e1_experiment import main


def test_e1_cli_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--help",
            ]
        )

    assert exc_info.value.code == 0

    output = capsys.readouterr().out

    assert "--config" in output
    assert "--output" in output
    assert "--base-directory" in output


def test_e1_cli_runs_small_repeated_experiment(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    configuration_path = tmp_path / "e1-small.json"
    output_directory = tmp_path / "e1-results"

    payload = {
        "experiment_id": "e1-cli-test",
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

    configuration_path.write_text(
        json.dumps(
            payload,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    main(
        [
            "--config",
            str(configuration_path),
            "--output",
            str(output_directory),
            "--base-directory",
            str(repository_root),
        ]
    )

    output = capsys.readouterr().out

    assert (
        "completed 16 E1 runs across 2 scenarios, "
        "4 conditions, and 2 repetitions"
        in output
    )

    for repetition in (
        "repetition-01",
        "repetition-02",
    ):
        repetition_directory = (
            output_directory / repetition
        )

        assert (
            repetition_directory / "runs.json"
        ).exists()

        assert (
            repetition_directory / "runs.csv"
        ).exists()

        assert (
            repetition_directory / "summary.json"
        ).exists()

        assert (
            repetition_directory / "summary.csv"
        ).exists()

        assert (
            repetition_directory
            / "evidence"
            / "approval_binding_benign"
            / "c0_logging.jsonl"
        ).exists()


def test_e1_cli_requires_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2

    error = capsys.readouterr().err

    assert "--output" in error
    assert "required" in error
