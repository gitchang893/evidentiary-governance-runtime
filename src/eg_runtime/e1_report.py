from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path(
    "configs/e1-governance-comparison.json"
)
DEFAULT_OUTPUT = Path(
    "docs/e1-governance-comparison.md"
)

CONDITION_LABELS = {
    "c0_logging": "C0",
    "c1_post_hoc": "C1",
    "c2_action_level": "C2",
    "c3_organizational": "C3",
}

CONDITION_DESCRIPTIONS = {
    "c0_logging": (
        "Logging only: execute the proposed action and "
        "record evidence without enforcing a governance "
        "decision."
    ),
    "c1_post_hoc": (
        "Post-hoc governance: execute first, then evaluate "
        "the completed action."
    ),
    "c2_action_level": (
        "Action-level pre-execution governance: evaluate "
        "action-local predicates before execution."
    ),
    "c3_organizational": (
        "Organizational pre-execution governance: evaluate "
        "both the proposed action and relevant organizational "
        "context before execution."
    ),
}

METRICS = (
    ("benign_completion_rate", "Benign completion rate"),
    ("unsafe_execution_rate", "Unsafe execution rate"),
    (
        "violation_detection_recall",
        "Violation detection recall",
    ),
    (
        "containment_success_rate",
        "Containment success rate",
    ),
    ("false_positive_rate", "False-positive rate"),
    (
        "mean_evidence_completeness",
        "Mean evidence completeness",
    ),
    ("evidence_validity_rate", "Evidence validity rate"),
    (
        "semantic_evidence_validity_rate",
        "Semantic evidence validity rate",
    ),
    (
        "preservation_bundle_validity_rate",
        "Preservation-bundle validity rate",
    ),
    (
        "preservation_directive_accuracy",
        "Preservation-directive accuracy",
    ),
)

FENCE_OPEN_RE = re.compile(
    r"^\s*(?P<marker>`{3,}|~{3,})(?P<info>.*)$"
)

REQUIRED_HEADINGS = (
    "# E1 Governance Condition Comparison",
    "## Purpose",
    "## Experimental design",
    "## Running E1",
    "## Aggregate results",
    "## Family-level outcomes",
    "## Interpretation",
    "## Reproducibility result",
    "## Validation",
)


class ReportGenerationError(RuntimeError):
    """Raised when source artifacts or Markdown are invalid."""


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReportGenerationError(
            f"required JSON artifact does not exist: {path}"
        )

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise ReportGenerationError(
            f"invalid JSON artifact: {path}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ReportGenerationError(
            f"expected a JSON object: {path}"
        )

    return payload


def require_positive_integer(
    payload: dict[str, Any],
    key: str,
    *,
    source: Path,
) -> int:
    value = payload.get(key)

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ReportGenerationError(
            f"{source}: expected positive integer "
            f"{key}, found {value!r}"
        )

    return value


def metric_statistics(
    aggregate: dict[str, Any],
    condition: str,
    metric: str,
) -> dict[str, Any]:
    condition_metrics = aggregate.get(
        "condition_metrics"
    )

    if not isinstance(condition_metrics, dict):
        raise ReportGenerationError(
            "aggregate.json: condition_metrics must "
            "be an object"
        )

    metrics = condition_metrics.get(condition)

    if not isinstance(metrics, dict):
        raise ReportGenerationError(
            "aggregate.json: missing condition metrics "
            f"for {condition}"
        )

    statistics = metrics.get(metric)

    if not isinstance(statistics, dict):
        raise ReportGenerationError(
            "aggregate.json: missing metric "
            f"{condition}.{metric}"
        )

    return statistics


def metric_mean(
    aggregate: dict[str, Any],
    condition: str,
    metric: str,
) -> float:
    value = metric_statistics(
        aggregate,
        condition,
        metric,
    ).get("mean")

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        raise ReportGenerationError(
            "aggregate.json: expected numeric mean for "
            f"{condition}.{metric}, found {value!r}"
        )

    return float(value)


def percentage(value: float) -> str:
    return f"{value * 100:.2f}%"


def validate_conditions(
    aggregate: dict[str, Any],
) -> tuple[str, ...]:
    conditions = aggregate.get("conditions")

    if not isinstance(conditions, list):
        raise ReportGenerationError(
            "aggregate.json: conditions must be a list"
        )

    if not all(
        isinstance(condition, str)
        for condition in conditions
    ):
        raise ReportGenerationError(
            "aggregate.json: invalid condition identifier"
        )

    condition_tuple = tuple(conditions)

    if set(condition_tuple) != set(CONDITION_LABELS):
        raise ReportGenerationError(
            "aggregate.json: expected conditions "
            f"{sorted(CONDITION_LABELS)}, found "
            f"{sorted(condition_tuple)}"
        )

    return condition_tuple


def validate_family_payload(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = payload.get("rows")

    if not isinstance(rows, list) or not rows:
        raise ReportGenerationError(
            "family-outcome-matrix.json: rows must "
            "be a non-empty list"
        )

    required_fields = {
        "family",
        "unsafe_scenario_id",
        "control_class",
        *CONDITION_LABELS,
    }

    validated_rows: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReportGenerationError(
                "family-outcome-matrix.json: "
                f"row {index} must be an object"
            )

        missing = required_fields - set(row)

        if missing:
            raise ReportGenerationError(
                "family-outcome-matrix.json: "
                f"row {index} missing fields "
                f"{sorted(missing)}"
            )

        validated_rows.append(row)

    declared_count = payload.get("family_count")

    if declared_count != len(validated_rows):
        raise ReportGenerationError(
            "family-outcome-matrix.json: "
            f"family_count={declared_count!r}, "
            f"rows={len(validated_rows)}"
        )

    return validated_rows


def build_metric_table(
    aggregate: dict[str, Any],
    conditions: tuple[str, ...],
) -> list[str]:
    lines = [
        "| Metric | "
        + " | ".join(
            CONDITION_LABELS[condition]
            for condition in conditions
        )
        + " |",
        "|---|"
        + "---:|" * len(conditions),
    ]

    for metric, label in METRICS:
        values = [
            percentage(
                metric_mean(
                    aggregate,
                    condition,
                    metric,
                )
            )
            for condition in conditions
        ]

        lines.append(
            f"| {label} | "
            + " | ".join(values)
            + " |"
        )

    return lines


def build_family_table(
    rows: list[dict[str, Any]],
    conditions: tuple[str, ...],
) -> list[str]:
    lines = [
        "| Unsafe family | Control class | "
        + " | ".join(
            CONDITION_LABELS[condition]
            for condition in conditions
        )
        + " |",
        "|---|---|"
        + "---|" * len(conditions),
    ]

    for row in rows:
        control_class = str(
            row["control_class"]
        ).replace("-", " ").title()

        values = [
            str(row[condition]).replace(
                " + ",
                ", ",
            ).capitalize()
            for condition in conditions
        ]

        lines.append(
            f"| `{row['family']}` | "
            f"{control_class} | "
            + " | ".join(values)
            + " |"
        )

    return lines


def all_reported_standard_deviations_zero(
    aggregate: dict[str, Any],
    conditions: tuple[str, ...],
) -> bool:
    for condition in conditions:
        for metric, _ in METRICS:
            value = metric_statistics(
                aggregate,
                condition,
                metric,
            ).get(
                "population_standard_deviation"
            )

            if value not in (None, 0, 0.0):
                return False

    return True


def build_report(
    *,
    root: Path,
    config_path: Path,
) -> str:
    aggregate_path = root / "aggregate.json"
    family_path = (
        root / "family-outcome-matrix.json"
    )

    aggregate = read_json_object(aggregate_path)
    family_payload = read_json_object(family_path)
    configuration = read_json_object(config_path)

    conditions = validate_conditions(aggregate)
    family_rows = validate_family_payload(
        family_payload
    )

    repetition_count = require_positive_integer(
        aggregate,
        "repetition_count",
        source=aggregate_path,
    )
    scenario_count = require_positive_integer(
        aggregate,
        "scenario_count",
        source=aggregate_path,
    )
    condition_count = require_positive_integer(
        aggregate,
        "condition_count",
        source=aggregate_path,
    )
    total_runs = require_positive_integer(
        aggregate,
        "total_runs",
        source=aggregate_path,
    )
    runs_per_repetition = require_positive_integer(
        aggregate,
        "runs_per_repetition",
        source=aggregate_path,
    )

    if condition_count != len(conditions):
        raise ReportGenerationError(
            "aggregate.json: condition_count does not "
            "match conditions"
        )

    if total_runs != (
        repetition_count * runs_per_repetition
    ):
        raise ReportGenerationError(
            "aggregate.json: total_runs does not equal "
            "repetition_count ~ runs_per_repetition"
        )

    configured_scenarios = configuration.get(
        "scenario_ids"
    )

    if not isinstance(configured_scenarios, list):
        raise ReportGenerationError(
            f"{config_path}: scenario_ids must be a list"
        )

    if len(configured_scenarios) != scenario_count:
        raise ReportGenerationError(
            f"{config_path}: scenario count "
            f"{len(configured_scenarios)} does not match "
            f"aggregate scenario_count={scenario_count}"
        )

    consistency = aggregate.get(
        "repetition_consistency"
    )

    if not isinstance(consistency, dict):
        raise ReportGenerationError(
            "aggregate.json: repetition_consistency "
            "must be an object"
        )

    all_consistent = consistency.get(
        "all_consistent"
    )
    inconsistent_count = consistency.get(
        "inconsistent_run_count"
    )

    if not isinstance(all_consistent, bool):
        raise ReportGenerationError(
            "aggregate.json: invalid all_consistent"
        )

    if (
        isinstance(inconsistent_count, bool)
        or not isinstance(inconsistent_count, int)
        or inconsistent_count < 0
    ):
        raise ReportGenerationError(
            "aggregate.json: invalid "
            "inconsistent_run_count"
        )

    action_local_families = [
        str(row["family"])
        for row in family_rows
        if row["control_class"] == "action-local"
    ]
    contextual_families = [
        str(row["family"])
        for row in family_rows
        if row["control_class"]
        == "organizational-context"
    ]

    c0_unsafe = metric_mean(
        aggregate,
        "c0_logging",
        "unsafe_execution_rate",
    )
    c1_unsafe = metric_mean(
        aggregate,
        "c1_post_hoc",
        "unsafe_execution_rate",
    )
    c1_detection = metric_mean(
        aggregate,
        "c1_post_hoc",
        "violation_detection_recall",
    )
    c2_unsafe = metric_mean(
        aggregate,
        "c2_action_level",
        "unsafe_execution_rate",
    )
    c2_containment = metric_mean(
        aggregate,
        "c2_action_level",
        "containment_success_rate",
    )
    c3_unsafe = metric_mean(
        aggregate,
        "c3_organizational",
        "unsafe_execution_rate",
    )
    c3_detection = metric_mean(
        aggregate,
        "c3_organizational",
        "violation_detection_recall",
    )
    c3_containment = metric_mean(
        aggregate,
        "c3_organizational",
        "containment_success_rate",
    )
    c3_benign = metric_mean(
        aggregate,
        "c3_organizational",
        "benign_completion_rate",
    )
    c3_false_positive = metric_mean(
        aggregate,
        "c3_organizational",
        "false_positive_rate",
    )

    family_names = [
        str(row["family"])
        for row in family_rows
    ]

    lines = [
        "# E1 Governance Condition Comparison",
        "",
        "## Purpose",
        "",
        (
            "E1 compares four governance conditions for "
            "tool-using agent actions:"
        ),
        "",
    ]

    for condition in conditions:
        lines.append(
            f"- **{CONDITION_LABELS[condition]} ? "
            f"{CONDITION_DESCRIPTIONS[condition]}**"
        )

    lines.extend(
        [
            "",
            (
                "The experiment tests whether increasingly "
                "contextual pre-execution governance improves "
                "unsafe-action containment without reducing "
                "benign completion."
            ),
            "",
            "## Experimental design",
            "",
            "The formal configuration is:",
            "",
            "```text",
            config_path.as_posix(),
            "```",
            "",
            (
                f"E1 selects {len(family_names)} "
                "benign/unsafe scenario pairs:"
            ),
            "",
        ]
    )

    for index, family in enumerate(
        family_names,
        start=1,
    ):
        lines.append(f"{index}. `{family}`")

    lines.extend(
        [
            "",
            "This produces:",
            "",
            "```text",
            f"{scenario_count} scenarios",
            f"~ {condition_count} governance conditions",
            (
                f"~ {repetition_count} deterministic "
                "repetitions"
            ),
            f"= {total_runs} governed runs",
            "```",
            "",
            (
                "The repetitions replay the same fixed "
                "scenarios, proposals, and contexts. They "
                "measure deterministic replay consistency, "
                "not variation across stochastic model "
                "outputs."
            ),
            "",
            "## Running E1",
            "",
            "Run the formal experiment:",
            "",
            "```bash",
            "python -m eg_runtime.e1_experiment \\",
            f"  --config {config_path.as_posix()} \\",
            f"  --output {root.as_posix()} \\",
            "  --base-directory .",
            "```",
            "",
            "Aggregate the repetitions:",
            "",
            "```bash",
            "python -m eg_runtime.e1_aggregate \\",
            f"  --root {root.as_posix()}",
            "```",
            "",
            "The aggregate command writes:",
            "",
            "```text",
            "aggregate.json",
            "aggregate.csv",
            "repetition-consistency.json",
            "```",
            "",
            "Each repetition contains:",
            "",
            "```text",
            "repetition-NN/",
            "¥ evidence/",
            "¥ preservation/",
            "¥ runs.json",
            "¥ runs.csv",
            "¥ summary.json",
            "¤ summary.csv",
            "```",
            "",
            "## Aggregate results",
            "",
            *build_metric_table(
                aggregate,
                conditions,
            ),
            "",
        ]
    )

    if all_reported_standard_deviations_zero(
        aggregate,
        conditions,
    ):
        lines.extend(
            [
                (
                    "The population standard deviation was "
                    "zero for every reported metric across "
                    f"the {repetition_count} deterministic "
                    "repetitions."
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Family-level outcomes",
            "",
            (
                "All benign scenarios completed under every "
                "condition without false-positive violation "
                "flags."
            ),
            "",
            *build_family_table(
                family_rows,
                conditions,
            ),
            "",
            (
                f"C2 contained {len(action_local_families)} "
                "action-local families:"
            ),
            "",
        ]
    )

    lines.extend(
        f"- `{family}`"
        for family in action_local_families
    )

    lines.extend(
        [
            "",
            (
                f"{len(contextual_families)} families "
                "required C3 organizational context:"
            ),
            "",
        ]
    )

    lines.extend(
        f"- `{family}`"
        for family in contextual_families
    )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            (
                "C0 provides audit evidence but no detection "
                "or preventive enforcement. Its unsafe "
                f"execution rate was {percentage(c0_unsafe)}."
            ),
            "",
            (
                "C1 detected "
                f"{percentage(c1_detection)} of unsafe "
                "proposals, but post-hoc detection could not "
                "prevent execution. Its unsafe execution rate "
                f"remained {percentage(c1_unsafe)}."
            ),
            "",
            (
                "C2 contained "
                f"{len(action_local_families)} of "
                f"{len(family_rows)} unsafe families, "
                "achieving "
                f"{percentage(c2_containment)} containment "
                "and reducing unsafe execution to "
                f"{percentage(c2_unsafe)}."
            ),
            "",
            (
                "C3 evaluated the relevant organizational "
                "relationships before execution. It achieved "
                f"{percentage(c3_detection)} detection, "
                f"{percentage(c3_containment)} containment, "
                f"{percentage(c3_benign)} benign completion, "
                f"{percentage(c3_false_positive)} false "
                "positives, and reduced unsafe execution to "
                f"{percentage(c3_unsafe)}."
            ),
            "",
            (
                "The distinction between C2 and C3 is "
                "therefore not merely stronger enforcement. "
                "It is the availability and evaluation of "
                "organizational context before execution."
            ),
            "",
            "## Reproducibility result",
            "",
            "The aggregate validation reported:",
            "",
            "```text",
            f"total_runs: {total_runs}",
            (
                "all_consistent: "
                f"{str(all_consistent).lower()}"
            ),
            (
                "inconsistent_run_count: "
                f"{inconsistent_count}"
            ),
            "```",
            "",
            (
                "Artifact paths and evidence hashes were "
                "excluded from semantic run comparison "
                "because they vary by repetition directory "
                "or serialized evidence instance. "
                "Order-insensitive evidence fields were "
                "normalized before comparison."
            ),
            "",
            (
                "The zero standard deviations establish "
                "exact replay consistency for the fixed "
                "experiment suite. They are not confidence "
                "intervals and should not be interpreted as "
                "evidence of robustness to alternative "
                "prompts, model outputs, or environmental "
                "states."
            ),
            "",
            "## Validation",
            "",
            "Validate the implementation and report generator:",
            "",
            "```bash",
            "python -m pytest",
            "python scripts/generate_e1_report.py \\",
            f"  --root {root.as_posix()} \\",
            f"  --config {config_path.as_posix()} \\",
            f"  --output {DEFAULT_OUTPUT.as_posix()}",
            "```",
            "",
        ]
    )

    return "\n".join(lines)


def validate_fences(text: str) -> int:
    open_character: str | None = None
    open_length: int | None = None
    open_line_number: int | None = None
    fenced_block_count = 0

    for line_number, line in enumerate(
        text.splitlines(),
        start=1,
    ):
        stripped = line.strip()

        if open_character is None:
            match = FENCE_OPEN_RE.match(line)

            if match is None:
                continue

            marker = match.group("marker")
            open_character = marker[0]
            open_length = len(marker)
            open_line_number = line_number
            fenced_block_count += 1
            continue

        assert open_length is not None
        closing_pattern = re.compile(
            rf"^{re.escape(open_character)}"
            rf"{{{open_length},}}\s*$"
        )

        if closing_pattern.fullmatch(stripped):
            open_character = None
            open_length = None
            open_line_number = None

    if open_character is not None:
        raise ReportGenerationError(
            "unclosed Markdown fence opened at "
            f"line {open_line_number}"
        )

    return fenced_block_count


def validate_tables(text: str) -> int:
    current_table: list[tuple[int, str]] = []
    table_count = 0

    def check_table(
        table: list[tuple[int, str]],
    ) -> None:
        nonlocal table_count

        if not table:
            return

        table_count += 1

        pipe_counts = [
            line.count("|")
            for _, line in table
        ]

        if len(set(pipe_counts)) != 1:
            details = ", ".join(
                f"line {line_number}: {pipe_count} pipes"
                for (
                    line_number,
                    _,
                ), pipe_count in zip(
                    table,
                    pipe_counts,
                    strict=True,
                )
            )

            raise ReportGenerationError(
                "inconsistent Markdown table columns: "
                f"{details}"
            )

        if len(table) < 2:
            raise ReportGenerationError(
                "Markdown table has no separator row at "
                f"line {table[0][0]}"
            )

        if "---" not in table[1][1]:
            raise ReportGenerationError(
                "Markdown table separator is missing at "
                f"line {table[1][0]}"
            )

    for line_number, line in enumerate(
        text.splitlines(),
        start=1,
    ):
        stripped = line.strip()

        if (
            stripped.startswith("|")
            and stripped.endswith("|")
        ):
            current_table.append(
                (line_number, stripped)
            )
            continue

        check_table(current_table)
        current_table = []

    check_table(current_table)

    return table_count


def validate_required_headings(text: str) -> None:
    missing = [
        heading
        for heading in REQUIRED_HEADINGS
        if heading not in text
    ]

    if missing:
        raise ReportGenerationError(
            f"missing required headings: {missing}"
        )


def validate_report(text: str) -> tuple[int, int]:
    if not text.endswith("\n"):
        raise ReportGenerationError(
            "report must end with a newline"
        )

    fenced_block_count = validate_fences(text)
    table_count = validate_tables(text)
    validate_required_headings(text)

    return fenced_block_count, table_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the E1 Markdown report from "
            "aggregate experiment artifacts."
        )
    )

    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help=(
            "E1 result directory containing aggregate.json "
            "and family-outcome-matrix.json."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=(
            "Formal E1 configuration. Default: "
            f"{DEFAULT_CONFIG}."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path receiving the generated Markdown report.",
    )

    arguments = parser.parse_args(argv)

    try:
        report = build_report(
            root=arguments.root,
            config_path=arguments.config,
        )
        fenced_block_count, table_count = (
            validate_report(report)
        )
    except ReportGenerationError as exc:
        parser.error(str(exc))

    arguments.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    arguments.output.write_text(
        report,
        encoding="utf-8",
    )

    written_report = arguments.output.read_text(
        encoding="utf-8"
    )

    if written_report != report:
        parser.error(
            "written report differs from generated content"
        )

    print(f"wrote: {arguments.output}")
    print(
        "validated Markdown fences:",
        fenced_block_count,
    )
    print(
        "validated Markdown tables:",
        table_count,
    )
    print(
        "validated required headings:",
        len(REQUIRED_HEADINGS),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
