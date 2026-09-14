from __future__ import annotations

from collections.abc import Callable

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import (
    Field,
    ValidationError,
    field_validator,
)

from eg_runtime.conditions import GovernanceCondition
from eg_runtime.models import FrozenModel
from eg_runtime.scenarios import ScenarioDefinition, load_scenario
from eg_runtime.proposal_generation import (
    ProposalGenerator,
    RequestProposalGenerator,
)
from eg_runtime.simulation import SimulationRunner


ProposalGeneratorFactory = Callable[
    [int],
    ProposalGenerator | RequestProposalGenerator,
]


class E1ConfigurationError(RuntimeError):
    """Raised when an E1 experiment configuration is invalid."""


class E1Configuration(FrozenModel):
    """Validated protocol configuration for the E1 experiment."""

    experiment_id: str = Field(min_length=1)
    scenario_directory: Path
    scenario_ids: list[str] = Field(min_length=1)
    conditions: list[GovernanceCondition] = Field(
        min_length=1
    )
    repetitions: int

    @field_validator("scenario_ids")
    @classmethod
    def validate_scenario_ids(
        cls,
        value: list[str],
    ) -> list[str]:
        """Require non-empty, unique scenario identifiers."""

        if any(
            not isinstance(scenario_id, str)
            or not scenario_id.strip()
            for scenario_id in value
        ):
            raise ValueError(
                "scenario_ids must contain "
                "non-empty strings"
            )

        if len(set(value)) != len(value):
            raise ValueError(
                "scenario_ids must contain unique values"
            )

        return value

    @field_validator("conditions")
    @classmethod
    def validate_conditions(
        cls,
        value: list[GovernanceCondition],
    ) -> list[GovernanceCondition]:
        """Require exactly the four comparative conditions."""

        expected = set(GovernanceCondition)

        if (
            len(value) != len(expected)
            or set(value) != expected
        ):
            raise ValueError(
                "conditions must contain exactly "
                "c0_logging, c1_post_hoc, "
                "c2_action_level, and c3_organizational"
            )

        return value

    @field_validator("repetitions", mode="before")
    @classmethod
    def validate_repetitions(
        cls,
        value: Any,
    ) -> int:
        """Require a strictly positive integer repetition count."""

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
        ):
            raise ValueError(
                "repetitions must be a positive integer"
            )

        return value

    @property
    def scenario_count(self) -> int:
        """Return the number of selected scenarios."""

        return len(self.scenario_ids)

    @property
    def run_count(self) -> int:
        """Return the total governed run count."""

        return (
            self.scenario_count
            * len(self.conditions)
            * self.repetitions
        )


def load_e1_configuration(
    path: str | Path,
) -> E1Configuration:
    """Load and validate one E1 experiment configuration."""

    configuration_path = Path(path)

    if not configuration_path.exists():
        raise E1ConfigurationError(
            "E1 configuration file does not exist: "
            f"{configuration_path}"
        )

    try:
        raw = json.loads(
            configuration_path.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as exc:
        raise E1ConfigurationError(
            "invalid JSON in E1 configuration "
            f"{configuration_path}: {exc}"
        ) from exc
    except OSError as exc:
        raise E1ConfigurationError(
            "could not read E1 configuration "
            f"{configuration_path}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise E1ConfigurationError(
            "E1 configuration root must be "
            f"a JSON object: {configuration_path}"
        )

    try:
        return E1Configuration.model_validate(raw)
    except ValidationError as exc:
        raise E1ConfigurationError(
            "invalid E1 configuration "
            f"{configuration_path}: {exc}"
        ) from exc



def load_e1_scenarios(
    configuration: E1Configuration,
    *,
    base_directory: str | Path | None = None,
) -> list[ScenarioDefinition]:
    """Load the configured E1 scenarios in declared order."""

    scenario_directory = configuration.scenario_directory

    if not scenario_directory.is_absolute():
        root = (
            Path(base_directory)
            if base_directory is not None
            else Path.cwd()
        )

        scenario_directory = (
            root / scenario_directory
        )

    if not scenario_directory.exists():
        raise E1ConfigurationError(
            "E1 scenario directory does not exist: "
            f"{scenario_directory}"
        )

    if not scenario_directory.is_dir():
        raise E1ConfigurationError(
            "E1 scenario path is not a directory: "
            f"{scenario_directory}"
        )

    scenario_paths = sorted(
        [
            *scenario_directory.glob("*.yaml"),
            *scenario_directory.glob("*.yml"),
        ]
    )

    scenarios_by_id: dict[str, ScenarioDefinition] = {}

    for scenario_path in scenario_paths:
        try:
            scenario = load_scenario(scenario_path)
        except Exception as exc:
            raise E1ConfigurationError(
                "could not load E1 scenario "
                f"{scenario_path}: {exc}"
            ) from exc

        if scenario.scenario_id in scenarios_by_id:
            raise E1ConfigurationError(
                "duplicate scenario_id in E1 scenario "
                f"directory: {scenario.scenario_id}"
            )

        scenarios_by_id[scenario.scenario_id] = scenario

    missing_scenario_ids = [
        scenario_id
        for scenario_id in configuration.scenario_ids
        if scenario_id not in scenarios_by_id
    ]

    if missing_scenario_ids:
        raise E1ConfigurationError(
            "configured E1 scenarios were not found: "
            + ", ".join(missing_scenario_ids)
        )

    selected = [
        scenarios_by_id[scenario_id]
        for scenario_id in configuration.scenario_ids
    ]

    scenarios_by_pair: dict[
        str,
        list[ScenarioDefinition],
    ] = {}

    for scenario in selected:
        scenarios_by_pair.setdefault(
            scenario.pair_id,
            [],
        ).append(scenario)

    incomplete_pairs: list[str] = []

    for pair_id, pair_scenarios in scenarios_by_pair.items():
        kinds = {
            scenario.kind.value
            for scenario in pair_scenarios
        }

        if (
            len(pair_scenarios) != 2
            or kinds != {"benign", "unsafe"}
        ):
            incomplete_pairs.append(pair_id)

    if incomplete_pairs:
        raise E1ConfigurationError(
            "E1 scenario selection must contain "
            "complete benign/unsafe pairs: "
            + ", ".join(sorted(incomplete_pairs))
        )

    return selected


class E1RunSpec(FrozenModel):
    """One deterministic governed run in the E1 protocol."""

    run_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    repetition: int = Field(ge=1)
    scenario_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    condition: GovernanceCondition


def build_e1_run_plan(
    configuration: E1Configuration,
    scenarios: list[ScenarioDefinition],
) -> list[E1RunSpec]:
    """Build the ordered set of governed E1 runs."""

    selected_ids = [
        scenario.scenario_id
        for scenario in scenarios
    ]

    if selected_ids != configuration.scenario_ids:
        raise E1ConfigurationError(
            "E1 scenarios must match configuration order"
        )

    expected_count = configuration.scenario_count

    if len(scenarios) != expected_count:
        raise E1ConfigurationError(
            "E1 scenario count does not match configuration"
        )

    repetition_width = max(
        2,
        len(str(configuration.repetitions)),
    )

    runs: list[E1RunSpec] = []

    for repetition in range(
        1,
        configuration.repetitions + 1,
    ):
        repetition_label = str(repetition).zfill(
            repetition_width
        )

        for scenario in scenarios:
            for condition in configuration.conditions:
                run_id = (
                    f"{configuration.experiment_id}"
                    f"--r{repetition_label}"
                    f"--{scenario.scenario_id}"
                    f"--{condition.value}"
                )

                runs.append(
                    E1RunSpec(
                        run_id=run_id,
                        experiment_id=(
                            configuration.experiment_id
                        ),
                        repetition=repetition,
                        scenario_id=scenario.scenario_id,
                        pair_id=scenario.pair_id,
                        condition=condition,
                    )
                )

    if len(runs) != configuration.run_count:
        raise E1ConfigurationError(
            "generated E1 run count does not match "
            "the configured protocol"
        )

    if len({run.run_id for run in runs}) != len(runs):
        raise E1ConfigurationError(
            "generated E1 run identifiers are not unique"
        )

    return runs


class E1RepetitionResult(FrozenModel):
    """Artifact summary for one complete E1 repetition."""

    repetition: int = Field(ge=1)
    output_directory: Path
    total_runs: int = Field(ge=1)
    scenario_count: int = Field(ge=1)
    attempted_generations: int = Field(default=0, ge=0)
    successful_generations: int = Field(default=0, ge=0)
    failed_generations: int = Field(default=0, ge=0)
    generation_completion_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )


class E1ExperimentArtifacts(FrozenModel):
    """Artifacts produced by a repeated E1 experiment."""

    experiment_id: str = Field(min_length=1)
    scenario_count: int = Field(ge=1)
    condition_count: int = Field(ge=1)
    repetitions: int = Field(ge=1)
    total_runs: int = Field(ge=1)
    attempted_generations: int = Field(default=0, ge=0)
    successful_generations: int = Field(default=0, ge=0)
    failed_generations: int = Field(default=0, ge=0)
    generation_completion_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )

    run_plan: list[E1RunSpec] = Field(min_length=1)
    repetition_results: list[
        E1RepetitionResult
    ] = Field(min_length=1)


def run_e1_experiment(
    configuration: E1Configuration,
    *,
    base_directory: str | Path | None = None,
    output_directory: str | Path,
    proposal_generator_factory: ProposalGeneratorFactory | None = None,
    continue_on_generation_failure: bool = False,
) -> E1ExperimentArtifacts:
    """Execute all configured E1 repetitions."""

    scenarios = load_e1_scenarios(
        configuration,
        base_directory=base_directory,
    )

    run_plan = build_e1_run_plan(
        configuration,
        scenarios,
    )

    output_root = Path(output_directory)
    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    runs_per_repetition = (
        len(scenarios)
        * len(configuration.conditions)
    )

    repetition_width = max(
        2,
        len(str(configuration.repetitions)),
    )

    repetition_results: list[
        E1RepetitionResult
    ] = []

    for repetition in range(
        1,
        configuration.repetitions + 1,
    ):
        repetition_label = str(repetition).zfill(
            repetition_width
        )

        repetition_directory = (
            output_root
            / f"repetition-{repetition_label}"
        )

        proposal_generator = (
            proposal_generator_factory(repetition - 1)
            if proposal_generator_factory is not None
            else None
        )

        simulation_artifacts = SimulationRunner(
            proposal_generator=proposal_generator,
            experiment_id=configuration.experiment_id,
            repetition_index=repetition - 1,
            continue_on_generation_failure=(
                continue_on_generation_failure
            ),
        ).run(
            scenarios,
            repetition_directory,
            conditions=configuration.conditions,
        )

        if (
            not continue_on_generation_failure
            and simulation_artifacts.summary.total_runs
            != runs_per_repetition
        ):
            raise E1ConfigurationError(
                "E1 repetition produced an unexpected "
                f"run count: repetition {repetition}"
            )

        if (
            not continue_on_generation_failure
            and simulation_artifacts.summary.scenario_count
            != len(scenarios)
        ):
            raise E1ConfigurationError(
                "E1 repetition produced an unexpected "
                f"scenario count: repetition {repetition}"
            )

        repetition_results.append(
            E1RepetitionResult(
                repetition=repetition,
                output_directory=(
                    repetition_directory
                ),
                total_runs=(
                    simulation_artifacts.summary.total_runs
                ),
                scenario_count=(
                    simulation_artifacts
                    .summary
                    .scenario_count
                ),
                attempted_generations=(
                    simulation_artifacts.summary
                    .attempted_generations
                ),
                successful_generations=(
                    simulation_artifacts.summary
                    .successful_generations
                ),
                failed_generations=(
                    simulation_artifacts.summary
                    .failed_generations
                ),
                generation_completion_rate=(
                    simulation_artifacts.summary
                    .generation_completion_rate
                ),
            )
        )

    total_runs = sum(
        result.total_runs
        for result in repetition_results
    )

    if (
        not continue_on_generation_failure
        and total_runs != configuration.run_count
    ):
        raise E1ConfigurationError(
            "executed E1 run count does not match "
            "the configured protocol"
        )

    attempted_generations = sum(
        result.attempted_generations
        for result in repetition_results
    )

    successful_generations = sum(
        result.successful_generations
        for result in repetition_results
    )

    failed_generations = sum(
        result.failed_generations
        for result in repetition_results
    )

    if (
        successful_generations
        + failed_generations
        != attempted_generations
    ):
        raise E1ConfigurationError(
            "E1 generation accounting is inconsistent"
        )

    expected_governed_runs = (
        successful_generations
        * len(configuration.conditions)
    )

    if total_runs != expected_governed_runs:
        raise E1ConfigurationError(
            "governed run count does not match "
            "successful generations"
        )

    generation_completion_rate = (
        successful_generations
        / attempted_generations
    )

    return E1ExperimentArtifacts(
        experiment_id=configuration.experiment_id,
        scenario_count=len(scenarios),
        condition_count=len(
            configuration.conditions
        ),
        repetitions=configuration.repetitions,
        total_runs=total_runs,
        attempted_generations=attempted_generations,
        successful_generations=successful_generations,
        failed_generations=failed_generations,
        generation_completion_rate=(
            generation_completion_rate
        ),
        run_plan=run_plan,
        repetition_results=repetition_results,
    )



def main(
    argv: list[str] | None = None,
) -> None:
    """Run the configured E1 experiment from the command line."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the E1 evidentiary-governance comparison "
            "with selected scenarios and repetitions."
        )
    )

    parser.add_argument(
        "--config",
        default="configs/e1-governance-comparison.json",
        help=(
            "Path to the E1 JSON configuration."
        ),
    )

    parser.add_argument(
        "--output",
        required=True,
        help=(
            "Directory receiving repetition artifacts."
        ),
    )

    parser.add_argument(
        "--base-directory",
        default=".",
        help=(
            "Base directory used to resolve a relative "
            "scenario_directory from the configuration."
        ),
    )

    arguments = parser.parse_args(argv)

    try:
        configuration = load_e1_configuration(
            arguments.config
        )

        artifacts = run_e1_experiment(
            configuration,
            base_directory=arguments.base_directory,
            output_directory=arguments.output,
        )
    except E1ConfigurationError as exc:
        parser.error(str(exc))

    print(
        f"completed {artifacts.total_runs} E1 runs "
        f"across {artifacts.scenario_count} scenarios, "
        f"{artifacts.condition_count} conditions, and "
        f"{artifacts.repetitions} repetitions"
    )

    for result in artifacts.repetition_results:
        print(
            f"repetition-{result.repetition:02d}",
            f"runs={result.total_runs}",
            f"output={result.output_directory}",
        )


if __name__ == "__main__":
    main()
