from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path(
    "results/experiment-suite-smoke/suite-manifest.json"
)

CONTRACTS_PATH = Path(
    "recovery/cli-contracts.json"
)

TEMPLATE_PATH = Path(
    "configs/experiment-suite-canonical.template.json"
)

MODULES = {
    "canonical-simulation-help":
        "eg_runtime.simulation",
    "e1-governance-comparison-help":
        "eg_runtime.e1_experiment",
    "e1-governance-comparison-aggregate-help":
        "eg_runtime.e1_aggregate",
    "e1-governance-comparison-family-matrix-help":
        "eg_runtime.e1_family_matrix",
    "e1-governance-comparison-report-help":
        "eg_runtime.e1_report",
    "approval-simulation-help":
        "eg_runtime.approval_simulation",
    "approval-integrity-help":
        "eg_runtime.approval_integrity_experiment",
    "retention-experiment-help":
        "eg_runtime.retention_experiment",
    "retention-export-help":
        "eg_runtime.retention_export",
    "retention-export-integrity-help":
        "eg_runtime.retention_export_experiment",
    "artifact-footprint-help":
        "eg_runtime.artifact_footprint",
}


class ContractExtractionError(RuntimeError):
    """Raised when CLI contracts cannot be extracted."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise ContractExtractionError(
            f"File not found: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ContractExtractionError(
            f"Invalid JSON in {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise ContractExtractionError(
            f"Expected a JSON object: {path}"
        )

    return value


def extract_usage(help_text: str) -> str:
    lines = help_text.splitlines()

    for index, line in enumerate(lines):
        if not line.startswith("usage:"):
            continue

        usage_lines = [line.strip()]

        for following in lines[index + 1:]:
            if not following.strip():
                break

            if following.startswith((" ", "\t")):
                usage_lines.append(
                    following.strip()
                )
                continue

            break

        return " ".join(usage_lines)

    raise ContractExtractionError(
        "No argparse usage line found"
    )


def remove_optional_groups(usage: str) -> str:
    result: list[str] = []
    depth = 0

    for character in usage:
        if character == "[":
            depth += 1
            continue

        if character == "]":
            depth = max(0, depth - 1)
            continue

        if depth == 0:
            result.append(character)

    return "".join(result)


def extract_required_options(
    usage: str,
) -> list[str]:
    required_usage = remove_optional_groups(
        usage
    )

    return list(
        dict.fromkeys(
            re.findall(
                r"--[a-zA-Z0-9][a-zA-Z0-9-]*",
                required_usage,
            )
        )
    )


def extract_option_help(
    help_text: str,
    option: str,
) -> str | None:
    lines = help_text.splitlines()
    pattern = re.compile(
        rf"^\s*{re.escape(option)}"
        rf"(?:[=\s,]|$)"
    )

    for index, line in enumerate(lines):
        if not pattern.search(line):
            continue

        parts = re.split(
            r"\s{2,}",
            line.strip(),
            maxsplit=1,
        )

        descriptions: list[str] = []

        if len(parts) == 2:
            descriptions.append(parts[1])

        for following in lines[index + 1:]:
            if not following.strip():
                break

            if re.match(
                r"^\s{4,}\S",
                following,
            ):
                stripped = following.strip()

                if stripped.startswith("-"):
                    break

                descriptions.append(stripped)
                continue

            break

        description = " ".join(
            descriptions
        ).strip()

        return description or None

    return None


def placeholder(
    command_name: str,
    option: str,
) -> str:
    normalized_command = (
        command_name
        .removesuffix("-help")
        .replace("-", "_")
        .upper()
    )

    normalized_option = (
        option
        .removeprefix("--")
        .replace("-", "_")
        .upper()
    )

    return (
        f"__{normalized_command}_"
        f"{normalized_option}__"
    )


def main() -> int:
    manifest = load_json(
        MANIFEST_PATH
    )

    commands = manifest.get("commands")

    if not isinstance(commands, list):
        raise ContractExtractionError(
            "Manifest commands must be a list"
        )

    contracts: list[dict[str, Any]] = []
    template_commands: list[dict[str, Any]] = []

    for command in commands:
        if not isinstance(command, dict):
            raise ContractExtractionError(
                "Manifest command must be an object"
            )

        command_name = command.get("name")

        if command_name not in MODULES:
            raise ContractExtractionError(
                f"Unexpected smoke command: "
                f"{command_name}"
            )

        stdout_value = command.get(
            "stdout_path"
        )

        if not isinstance(stdout_value, str):
            raise ContractExtractionError(
                f"Missing stdout path for "
                f"{command_name}"
            )

        stdout_path = Path(stdout_value)

        if not stdout_path.is_absolute():
            stdout_path = (
                Path.cwd()
                / stdout_path
            )

        try:
            help_text = stdout_path.read_text(
                encoding="utf-8"
            )
        except FileNotFoundError as exc:
            raise ContractExtractionError(
                f"Smoke log not found: "
                f"{stdout_path}"
            ) from exc

        usage = extract_usage(help_text)

        required_options = (
            extract_required_options(
                usage
            )
        )

        option_details = {
            option: extract_option_help(
                help_text,
                option,
            )
            for option in required_options
        }

        module = MODULES[command_name]

        contracts.append(
            {
                "command_name": command_name,
                "module": module,
                "usage": usage,
                "required_options":
                    required_options,
                "required_option_help":
                    option_details,
                "stdout_path":
                    str(stdout_path),
            }
        )

        argv = [
            sys.executable,
            "-m",
            module,
        ]

        for option in required_options:
            argv.extend(
                [
                    option,
                    placeholder(
                        command_name,
                        option,
                    ),
                ]
            )

        template_commands.append(
            {
                "name": (
                    command_name
                    .removesuffix("-help")
                ),
                "argv": argv,
            }
        )

    CONTRACTS_PATH.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source_manifest":
                    str(MANIFEST_PATH),
                "command_count":
                    len(contracts),
                "commands": contracts,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    TEMPLATE_PATH.write_text(
        json.dumps(
            {
                "suite_id":
                    "evidentiary-governance-canonical",
                "commands":
                    template_commands,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"wrote: {CONTRACTS_PATH}"
    )

    print(
        f"wrote: {TEMPLATE_PATH}"
    )

    print()

    for contract in contracts:
        print(
            contract["module"]
        )

        print(
            "  required:",
            ", ".join(
                contract["required_options"]
            )
            or "(none)",
        )

        print(
            "  usage:",
            contract["usage"],
        )

        for option, description in (
            contract[
                "required_option_help"
            ].items()
        ):
            print(
                f"  {option}: "
                f"{description or '(no description)'}"
            )

        print()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractExtractionError as exc:
        print(
            f"error: {exc}",
            file=sys.stderr,
        )

        raise SystemExit(1)
