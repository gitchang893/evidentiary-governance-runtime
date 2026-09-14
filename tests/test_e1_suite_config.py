from __future__ import annotations

import json
from pathlib import Path


CONFIG_PATH = Path(
    "configs/experiment-suite-e1.json"
)

EXPECTED_COMMANDS = [
    "e1-governance-comparison",
    "e1-governance-comparison-aggregate",
    "e1-governance-comparison-family-matrix",
    "e1-governance-comparison-report",
]


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

    index = argv.index(option)
    value = argv[index + 1]

    assert isinstance(value, str)
    assert value

    return value


def test_e1_suite_has_dedicated_identity_and_order(
) -> None:
    payload = json.loads(
        CONFIG_PATH.read_text(encoding="utf-8")
    )

    commands = payload["commands"]

    assert (
        payload["suite_id"]
        == "evidentiary-governance-e1"
    )

    assert [
        command["name"]
        for command in commands
    ] == EXPECTED_COMMANDS


def test_e1_suite_uses_one_shared_result_root(
) -> None:
    payload = json.loads(
        CONFIG_PATH.read_text(encoding="utf-8")
    )
    commands = payload["commands"]

    result_root = _argument_value(
        commands[0],
        "--output",
    )

    assert result_root == (
        "results/e1-governance-comparison"
    )

    for command in commands[1:]:
        assert _argument_value(
            command,
            "--root",
        ) == result_root

    assert _argument_value(
        commands[3],
        "--config",
    ) == "configs/e1-governance-comparison.json"

    assert _argument_value(
        commands[3],
        "--output",
    ) == (
        f"{result_root}/"
        "e1-governance-comparison.md"
    )
