from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Sequence


class ExperimentSuiteError(RuntimeError):
    """Raised when an experiment suite cannot be loaded or executed."""


@dataclass(frozen=True)
class ExperimentCommand:
    name: str
    argv: tuple[str, ...]
    cwd: str | None = None

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        index: int,
    ) -> ExperimentCommand:
        name = value.get("name")
        argv = value.get("argv")
        cwd = value.get("cwd")

        if not isinstance(name, str) or not name.strip():
            raise ExperimentSuiteError(
                f"commands[{index}].name must be a non-empty string"
            )

        if not isinstance(argv, list) or not argv:
            raise ExperimentSuiteError(
                f"commands[{index}].argv must be a non-empty list"
            )

        if not all(
            isinstance(argument, str) and argument
            for argument in argv
        ):
            raise ExperimentSuiteError(
                f"commands[{index}].argv must contain "
                "non-empty strings"
            )

        if cwd is not None and (
            not isinstance(cwd, str) or not cwd.strip()
        ):
            raise ExperimentSuiteError(
                f"commands[{index}].cwd must be a non-empty "
                "string when provided"
            )

        return cls(
            name=name.strip(),
            argv=tuple(argv),
            cwd=cwd,
        )


@dataclass(frozen=True)
class CommandExecutionResult:
    name: str
    argv: tuple[str, ...]
    cwd: str
    return_code: int
    successful: bool
    started_at: str
    finished_at: str
    duration_ms: float
    stdout_path: str
    stderr_path: str
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True)
class ExperimentSuiteResult:
    schema_version: str
    suite_id: str
    generated_at: str
    successful: bool
    fail_fast: bool
    git_commit: str | None
    python_version: str
    platform: str
    working_directory: str
    configuration_path: str
    output_directory: str
    command_count: int
    completed_command_count: int
    failed_command_count: int
    commands: tuple[CommandExecutionResult, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _safe_filename(value: str) -> str:
    normalized = re.sub(
        r"[^a-zA-Z0-9._-]+",
        "-",
        value.strip(),
    ).strip("-")

    return normalized or "command"


def _git_commit(working_directory: Path) -> str | None:
    completed = subprocess.run(
        [
            "git",
            "rev-parse",
            "HEAD",
        ],
        cwd=working_directory,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if completed.returncode != 0:
        return None

    commit = completed.stdout.strip()

    return commit or None


def _resolve_command_cwd(
    command: ExperimentCommand,
    *,
    working_directory: Path,
) -> Path:
    if command.cwd is None:
        return working_directory

    candidate = Path(command.cwd)

    if not candidate.is_absolute():
        candidate = working_directory / candidate

    resolved = candidate.resolve()

    if not resolved.exists():
        raise ExperimentSuiteError(
            f"Command working directory does not exist: "
            f"{resolved}"
        )

    if not resolved.is_dir():
        raise ExperimentSuiteError(
            f"Command working directory is not a directory: "
            f"{resolved}"
        )

    return resolved


def load_suite_configuration(
    path: Path,
) -> tuple[str, tuple[ExperimentCommand, ...]]:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise ExperimentSuiteError(
            f"Suite configuration not found: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ExperimentSuiteError(
            f"Invalid suite configuration JSON: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ExperimentSuiteError(
            "Suite configuration must be a JSON object"
        )

    suite_id = raw.get("suite_id")

    if not isinstance(suite_id, str) or not suite_id.strip():
        raise ExperimentSuiteError(
            "suite_id must be a non-empty string"
        )

    raw_commands = raw.get("commands")

    if not isinstance(raw_commands, list) or not raw_commands:
        raise ExperimentSuiteError(
            "commands must be a non-empty list"
        )

    commands = tuple(
        ExperimentCommand.from_mapping(
            command,
            index=index,
        )
        for index, command in enumerate(raw_commands)
        if isinstance(command, Mapping)
    )

    if len(commands) != len(raw_commands):
        raise ExperimentSuiteError(
            "Each command must be a JSON object"
        )

    names = [
        command.name
        for command in commands
    ]

    if len(names) != len(set(names)):
        raise ExperimentSuiteError(
            "Command names must be unique"
        )

    return suite_id.strip(), commands


def _execute_command(
    command: ExperimentCommand,
    *,
    index: int,
    working_directory: Path,
    log_directory: Path,
) -> CommandExecutionResult:
    command_cwd = _resolve_command_cwd(
        command,
        working_directory=working_directory,
    )

    filename = (
        f"{index:02d}-"
        f"{_safe_filename(command.name)}"
    )

    stdout_path = (
        log_directory
        / f"{filename}.stdout.txt"
    )

    stderr_path = (
        log_directory
        / f"{filename}.stderr.txt"
    )

    started = _utc_now()
    started_counter = perf_counter()

    completed = subprocess.run(
        list(command.argv),
        cwd=command_cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    finished = _utc_now()
    duration_ms = (
        perf_counter() - started_counter
    ) * 1000.0

    stdout_path.write_text(
        completed.stdout,
        encoding="utf-8",
    )

    stderr_path.write_text(
        completed.stderr,
        encoding="utf-8",
    )

    return CommandExecutionResult(
        name=command.name,
        argv=command.argv,
        cwd=str(command_cwd),
        return_code=completed.returncode,
        successful=completed.returncode == 0,
        started_at=_format_timestamp(started),
        finished_at=_format_timestamp(finished),
        duration_ms=duration_ms,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        stdout_sha256=_sha256_file(stdout_path),
        stderr_sha256=_sha256_file(stderr_path),
    )


def run_experiment_suite(
    configuration_path: Path,
    output_directory: Path,
    *,
    fail_fast: bool = False,
    working_directory: Path | None = None,
) -> ExperimentSuiteResult:
    configuration_path = configuration_path.resolve()
    output_directory = output_directory.resolve()

    if working_directory is None:
        working_directory = Path.cwd()

    working_directory = working_directory.resolve()

    suite_id, commands = load_suite_configuration(
        configuration_path
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_directory = output_directory / "logs"

    log_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    results: list[CommandExecutionResult] = []

    for index, command in enumerate(
        commands,
        start=1,
    ):
        result = _execute_command(
            command,
            index=index,
            working_directory=working_directory,
            log_directory=log_directory,
        )

        results.append(result)

        if fail_fast and not result.successful:
            break

    failed_count = sum(
        not result.successful
        for result in results
    )

    suite_result = ExperimentSuiteResult(
        schema_version="1.0",
        suite_id=suite_id,
        generated_at=_format_timestamp(
            _utc_now()
        ),
        successful=(
            len(results) == len(commands)
            and failed_count == 0
        ),
        fail_fast=fail_fast,
        git_commit=_git_commit(
            working_directory
        ),
        python_version=sys.version,
        platform=platform.platform(),
        working_directory=str(
            working_directory
        ),
        configuration_path=str(
            configuration_path
        ),
        output_directory=str(
            output_directory
        ),
        command_count=len(commands),
        completed_command_count=len(results),
        failed_command_count=failed_count,
        commands=tuple(results),
    )

    manifest_path = (
        output_directory
        / "suite-manifest.json"
    )

    manifest_path.write_text(
        json.dumps(
            asdict(suite_result),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return suite_result


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a reproducible sequence of experiment "
            "commands and write a suite manifest."
        )
    )

    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help=(
            "Path to a JSON experiment-suite "
            "configuration."
        ),
    )

    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help=(
            "Directory for command logs and the suite "
            "manifest."
        ),
    )

    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help=(
            "Stop after the first failed command."
        ),
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
) -> int:
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)

    try:
        result = run_experiment_suite(
            arguments.config,
            arguments.output,
            fail_fast=arguments.fail_fast,
        )
    except ExperimentSuiteError as exc:
        parser.error(str(exc))

    print(
        f"suite_id={result.suite_id} "
        f"successful={result.successful} "
        f"completed={result.completed_command_count}/"
        f"{result.command_count} "
        f"failed={result.failed_command_count}"
    )

    return 0 if result.successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
