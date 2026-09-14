from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError, model_validator

from eg_runtime.models import (
    ActionProposal,
    Disposition,
    FrozenModel,
    GovernanceContext,
    Preservation,
    RuntimeNorm,
)


class ScenarioLoadError(RuntimeError):
    """Raised when a scenario file cannot be loaded or validated."""


class ScenarioKind(StrEnum):
    """Ground-truth category assigned to a scenario."""

    UNSAFE = "unsafe"
    BENIGN = "benign"


class ScenarioGroundTruth(FrozenModel):
    """Expected organization-level outcome for one scenario."""

    expected_disposition: Disposition
    expected_preservation: Preservation
    unsafe_if_executed: bool


class SandboxState(FrozenModel):
    """Initial isolated state supplied to sandboxed tools."""

    documents: dict[str, str] = Field(default_factory=dict)
    reports: dict[str, str] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    records: dict[str, dict[str, Any]] = Field(default_factory=dict)
    approval_requests: list[dict[str, Any]] = Field(
        default_factory=list
    )


class ScenarioDefinition(FrozenModel):
    """Complete deterministic input for one comparative simulation."""

    scenario_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    family: str = Field(min_length=1)
    kind: ScenarioKind
    claim: str = Field(min_length=1)
    required_evidence: list[str] = Field(min_length=1)
    ground_truth: ScenarioGroundTruth
    context: GovernanceContext
    proposal: ActionProposal
    norms: list[RuntimeNorm] = Field(min_length=1)
    sandbox: SandboxState

    @model_validator(mode="after")
    def validate_internal_consistency(
        self,
    ) -> ScenarioDefinition:
        """Validate identifiers and scenario ground truth."""

        if len(set(self.required_evidence)) != len(
            self.required_evidence
        ):
            raise ValueError(
                "required_evidence must contain unique elements"
            )

        norm_ids = [
            norm.norm_id
            for norm in self.norms
        ]

        if len(set(norm_ids)) != len(norm_ids):
            raise ValueError(
                "runtime norm identifiers must be unique "
                "within a scenario"
            )

        if (
            self.kind is ScenarioKind.UNSAFE
            and self.ground_truth.unsafe_if_executed is False
        ):
            raise ValueError(
                "an unsafe scenario must be marked unsafe_if_executed"
            )

        if (
            self.kind is ScenarioKind.BENIGN
            and self.ground_truth.unsafe_if_executed is True
        ):
            raise ValueError(
                "a benign scenario cannot be marked unsafe_if_executed"
            )

        return self


def load_scenario(
    path: str | Path,
) -> ScenarioDefinition:
    """Load and validate one YAML scenario."""

    scenario_path = Path(path)

    if not scenario_path.exists():
        raise ScenarioLoadError(
            f"scenario file does not exist: {scenario_path}"
        )

    try:
        raw = yaml.safe_load(
            scenario_path.read_text(encoding="utf-8")
        )
    except yaml.YAMLError as exc:
        raise ScenarioLoadError(
            f"invalid YAML in {scenario_path}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ScenarioLoadError(
            f"scenario root must be a mapping: {scenario_path}"
        )

    try:
        return ScenarioDefinition.model_validate(raw)
    except ValidationError as exc:
        raise ScenarioLoadError(
            f"invalid scenario in {scenario_path}: {exc}"
        ) from exc


def load_scenarios(
    directory: str | Path,
) -> list[ScenarioDefinition]:
    """Load all YAML scenarios below a directory."""

    root = Path(directory)

    if not root.exists():
        raise ScenarioLoadError(
            f"scenario directory does not exist: {root}"
        )

    paths = sorted(
        [
            *root.rglob("*.yaml"),
            *root.rglob("*.yml"),
        ]
    )

    scenarios = [
        load_scenario(path)
        for path in paths
    ]

    identifiers = [
        scenario.scenario_id
        for scenario in scenarios
    ]

    if len(set(identifiers)) != len(identifiers):
        raise ScenarioLoadError(
            "scenario identifiers must be unique"
        )

    return scenarios


def validate_scenario_pairs(
    scenarios: list[ScenarioDefinition],
) -> None:
    """Require one unsafe and one benign scenario in each pair."""

    pairs: dict[str, list[ScenarioDefinition]] = {}

    for scenario in scenarios:
        pairs.setdefault(
            scenario.pair_id,
            [],
        ).append(scenario)

    errors: list[str] = []

    for pair_id, members in sorted(pairs.items()):
        kinds = [
            member.kind
            for member in members
        ]

        if len(members) != 2:
            errors.append(
                f"{pair_id}: expected 2 scenarios, found "
                f"{len(members)}"
            )
            continue

        if set(kinds) != {
            ScenarioKind.UNSAFE,
            ScenarioKind.BENIGN,
        }:
            errors.append(
                f"{pair_id}: expected one unsafe and one benign "
                "scenario"
            )

    if errors:
        raise ScenarioLoadError(
            "invalid scenario pairs: " + "; ".join(errors)
        )
