from __future__ import annotations

import json
from pathlib import Path


CANONICAL_CONFIG = Path(
    "configs/experiment-suite-canonical.json"
)
CANONICAL_TEMPLATE = Path(
    "configs/experiment-suite-canonical.template.json"
)

E1_COMMAND = "e1-governance-comparison"
AGGREGATE_COMMAND = (
    "e1-governance-comparison-aggregate"
)
FAMILY_MATRIX_COMMAND = (
    "e1-governance-comparison-family-matrix"
)
REPORT_COMMAND = (
    "e1-governance-comparison-report"
)


def _load_commands(
    path: Path,
) -> list[dict[str, object]]:
    payload = json.loads(
        path.read_text(encoding="utf-8")
    )

    commands = payload.get("commands")

    assert isinstance(commands, list)
    assert all(
        isinstance(command, dict)
        for command in commands
    )

    return commands


def _command_by_name(
    commands: list[dict[str, object]],
    name: str,
) -> dict[str, object]:
    matches = [
        command
        for command in commands
        if command.get("name") == name
    ]

    assert len(matches) == 1

    return matches[0]


def _argument_value(
    command: dict[str, object],
    option: str,
) -> str:
    argv = command.get("argv")

    assert isinstance(argv, list)
    assert all(
        isinstance(argument, str)
        for argument in argv
    )

    option_index = argv.index(option)
    value = argv[option_index + 1]

    assert isinstance(value, str)
    assert value

    return value


def test_canonical_suite_processes_e1_in_order(
) -> None:
    commands = _load_commands(
        CANONICAL_CONFIG
    )
    names = [
        command["name"]
        for command in commands
    ]

    e1_index = names.index(E1_COMMAND)
    aggregate_index = names.index(
        AGGREGATE_COMMAND
    )
    family_matrix_index = names.index(
        FAMILY_MATRIX_COMMAND
    )
    report_index = names.index(
        REPORT_COMMAND
    )

    assert aggregate_index == e1_index + 1
    assert family_matrix_index == aggregate_index + 1
    assert report_index == family_matrix_index + 1

    e1_command = _command_by_name(
        commands,
        E1_COMMAND,
    )
    aggregate_command = _command_by_name(
        commands,
        AGGREGATE_COMMAND,
    )
    family_matrix_command = _command_by_name(
        commands,
        FAMILY_MATRIX_COMMAND,
    )
    report_command = _command_by_name(
        commands,
        REPORT_COMMAND,
    )

    e1_root = _argument_value(
        e1_command,
        "--output",
    )

    assert _argument_value(
        aggregate_command,
        "--root",
    ) == e1_root

    assert _argument_value(
        family_matrix_command,
        "--root",
    ) == e1_root

    assert _argument_value(
        report_command,
        "--root",
    ) == e1_root

    assert _argument_value(
        report_command,
        "--config",
    ) == "configs/e1-governance-comparison.json"

    assert _argument_value(
        report_command,
        "--output",
    ) == (
        f"{e1_root}/e1-governance-comparison.md"
    )


def test_canonical_template_contains_e1_aggregate(
) -> None:
    commands = _load_commands(
        CANONICAL_TEMPLATE
    )

    aggregate_command = _command_by_name(
        commands,
        AGGREGATE_COMMAND,
    )

    argv = aggregate_command["argv"]

    assert isinstance(argv, list)
    assert argv[1:3] == [
        "-m",
        "eg_runtime.e1_aggregate",
    ]

    root = _argument_value(
        aggregate_command,
        "--root",
    )

    assert root == (
        "__E1_GOVERNANCE_COMPARISON_"
        "AGGREGATE_ROOT__"
    )


def test_canonical_template_contains_e1_family_matrix(
) -> None:
    commands = _load_commands(
        CANONICAL_TEMPLATE
    )

    command = _command_by_name(
        commands,
        FAMILY_MATRIX_COMMAND,
    )

    argv = command["argv"]

    assert isinstance(argv, list)
    assert argv[1:3] == [
        "-m",
        "eg_runtime.e1_family_matrix",
    ]

    root = _argument_value(
        command,
        "--root",
    )

    assert root == (
        "__E1_GOVERNANCE_COMPARISON_"
        "FAMILY_MATRIX_ROOT__"
    )


def test_canonical_template_contains_e1_report(
) -> None:
    commands = _load_commands(
        CANONICAL_TEMPLATE
    )

    command = _command_by_name(
        commands,
        REPORT_COMMAND,
    )

    argv = command["argv"]

    assert isinstance(argv, list)
    assert argv[1:3] == [
        "-m",
        "eg_runtime.e1_report",
    ]

    root = _argument_value(
        command,
        "--root",
    )
    output = _argument_value(
        command,
        "--output",
    )

    assert root == (
        "__E1_GOVERNANCE_COMPARISON_"
        "REPORT_ROOT__"
    )
    assert output == (
        "__E1_GOVERNANCE_COMPARISON_"
        "REPORT_OUTPUT__"
    )

