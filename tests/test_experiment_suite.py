from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from eg_runtime.experiment_suite import (
    ExperimentSuiteError,
    load_suite_configuration,
    run_experiment_suite,
)


def write_configuration(
    path: Path,
    *,
    suite_id: str = "test-suite",
    commands: list[dict[str, Any]],
) -> Path:
    path.write_text(
        json.dumps(
            {
                "suite_id": suite_id,
                "commands": commands,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def python_command(
    source: str,
) -> list[str]:
    return [
        sys.executable,
        "-c",
        source,
    ]


def test_loads_valid_suite_configuration(
    tmp_path: Path,
) -> None:
    configuration = write_configuration(
        tmp_path / "suite.json",
        suite_id="valid-suite",
        commands=[
            {
                "name": "first-command",
                "argv": python_command(
                    "print('first')"
                ),
            },
            {
                "name": "second-command",
                "argv": python_command(
                    "print('second')"
                ),
                "cwd": ".",
            },
        ],
    )

    suite_id, commands = load_suite_configuration(
        configuration
    )

    assert suite_id == "valid-suite"
    assert len(commands) == 2

    assert commands[0].name == "first-command"

    assert commands[0].argv == tuple(
        python_command(
            "print('first')"
        )
    )

    assert commands[0].cwd is None
    assert commands[1].cwd == "."


def test_rejects_duplicate_command_names(
    tmp_path: Path,
) -> None:
    configuration = write_configuration(
        tmp_path / "suite.json",
        commands=[
            {
                "name": "duplicate",
                "argv": python_command(
                    "print('first')"
                ),
            },
            {
                "name": "duplicate",
                "argv": python_command(
                    "print('second')"
                ),
            },
        ],
    )

    with pytest.raises(
        ExperimentSuiteError,
        match="Command names must be unique",
    ):
        load_suite_configuration(
            configuration
        )


def test_runs_successful_suite_and_writes_manifest(
    tmp_path: Path,
) -> None:
    configuration = write_configuration(
        tmp_path / "suite.json",
        suite_id="successful-suite",
        commands=[
            {
                "name": "emit-output",
                "argv": python_command(
                    (
                        "import sys; "
                        "print('standard output'); "
                        "print('standard error', "
                        "file=sys.stderr)"
                    )
                ),
            },
        ],
    )

    output_directory = tmp_path / "results"

    result = run_experiment_suite(
        configuration,
        output_directory,
        working_directory=tmp_path,
    )

    assert result.suite_id == "successful-suite"
    assert result.successful is True
    assert result.command_count == 1

    assert (
        result.completed_command_count
        == 1
    )

    assert result.failed_command_count == 0

    command_result = result.commands[0]

    assert command_result.successful is True
    assert command_result.return_code == 0

    stdout_path = Path(
        command_result.stdout_path
    )

    stderr_path = Path(
        command_result.stderr_path
    )

    assert (
        stdout_path.read_text(
            encoding="utf-8"
        )
        == "standard output\n"
    )

    assert (
        stderr_path.read_text(
            encoding="utf-8"
        )
        == "standard error\n"
    )

    manifest_path = (
        output_directory
        / "suite-manifest.json"
    )

    assert manifest_path.exists()

    manifest = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    assert (
        manifest["suite_id"]
        == "successful-suite"
    )

    assert manifest["successful"] is True
    assert manifest["command_count"] == 1

    assert (
        manifest["commands"][0]["name"]
        == "emit-output"
    )


def test_records_log_hashes(
    tmp_path: Path,
) -> None:
    configuration = write_configuration(
        tmp_path / "suite.json",
        commands=[
            {
                "name": "hash-output",
                "argv": python_command(
                    (
                        "import sys; "
                        "print('hash me'); "
                        "print('error hash', "
                        "file=sys.stderr)"
                    )
                ),
            },
        ],
    )

    result = run_experiment_suite(
        configuration,
        tmp_path / "results",
        working_directory=tmp_path,
    )

    command_result = result.commands[0]

    stdout_path = Path(
        command_result.stdout_path
    )

    stderr_path = Path(
        command_result.stderr_path
    )

    assert (
        command_result.stdout_sha256
        == sha256_file(stdout_path)
    )

    assert (
        command_result.stderr_sha256
        == sha256_file(stderr_path)
    )


def test_continues_after_failure_by_default(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "continued.txt"

    configuration = write_configuration(
        tmp_path / "suite.json",
        commands=[
            {
                "name": "failing-command",
                "argv": python_command(
                    (
                        "import sys; "
                        "print('failure'); "
                        "raise SystemExit(3)"
                    )
                ),
            },
            {
                "name": "following-command",
                "argv": python_command(
                    (
                        "from pathlib import Path; "
                        f"Path({str(marker)!r})"
                        ".write_text("
                        "'continued', "
                        "encoding='utf-8'"
                        ")"
                    )
                ),
            },
        ],
    )

    result = run_experiment_suite(
        configuration,
        tmp_path / "results",
        working_directory=tmp_path,
    )

    assert result.successful is False
    assert result.command_count == 2

    assert (
        result.completed_command_count
        == 2
    )

    assert result.failed_command_count == 1

    assert (
        result.commands[0].return_code
        == 3
    )

    assert result.commands[1].successful is True

    assert (
        marker.read_text(
            encoding="utf-8"
        )
        == "continued"
    )


def test_fail_fast_stops_after_first_failure(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-exist.txt"

    configuration = write_configuration(
        tmp_path / "suite.json",
        commands=[
            {
                "name": "failing-command",
                "argv": python_command(
                    "raise SystemExit(4)"
                ),
            },
            {
                "name": "skipped-command",
                "argv": python_command(
                    (
                        "from pathlib import Path; "
                        f"Path({str(marker)!r})"
                        ".write_text("
                        "'unexpected', "
                        "encoding='utf-8'"
                        ")"
                    )
                ),
            },
        ],
    )

    result = run_experiment_suite(
        configuration,
        tmp_path / "results",
        fail_fast=True,
        working_directory=tmp_path,
    )

    assert result.successful is False
    assert result.fail_fast is True
    assert result.command_count == 2

    assert (
        result.completed_command_count
        == 1
    )

    assert result.failed_command_count == 1
    assert len(result.commands) == 1

    assert (
        result.commands[0].return_code
        == 4
    )

    assert marker.exists() is False


def test_resolves_relative_command_working_directory(
    tmp_path: Path,
) -> None:
    command_directory = tmp_path / "workspace"
    command_directory.mkdir()

    configuration = write_configuration(
        tmp_path / "suite.json",
        commands=[
            {
                "name": "show-working-directory",
                "argv": python_command(
                    (
                        "from pathlib import Path; "
                        "print(Path.cwd())"
                    )
                ),
                "cwd": "workspace",
            },
        ],
    )

    result = run_experiment_suite(
        configuration,
        tmp_path / "results",
        working_directory=tmp_path,
    )

    command_result = result.commands[0]

    assert (
        Path(command_result.cwd)
        == command_directory.resolve()
    )

    assert (
        Path(command_result.stdout_path)
        .read_text(encoding="utf-8")
        .strip()
        == str(command_directory.resolve())
    )


def test_repository_smoke_suite_lists_all_experiment_clis() -> None:
    repository_root = Path(__file__).resolve().parents[1]

    configuration = (
        repository_root
        / "configs"
        / "experiment-suite-smoke.json"
    )

    suite_id, commands = load_suite_configuration(
        configuration
    )

    assert suite_id == "evidentiary-governance-smoke"
    assert len(commands) == 11

    assert {
        command.name
        for command in commands
    } == {
        "canonical-simulation-help",
        "e1-governance-comparison-help",
        "e1-governance-comparison-aggregate-help",
        "e1-governance-comparison-family-matrix-help",
        "e1-governance-comparison-report-help",
        "approval-simulation-help",
        "approval-integrity-help",
        "retention-experiment-help",
        "retention-export-help",
        "retention-export-integrity-help",
        "artifact-footprint-help",
    }

    assert all(
        command.argv[-1] == "--help"
        for command in commands
    )
