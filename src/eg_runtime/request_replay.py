from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from eg_runtime.models import ActionProposal
from eg_runtime.proposal_generation import (
    ProposalGenerationRequest,
    ProposalGenerationResult,
    build_proposal_generation_request,
)
from eg_runtime.scenarios import ScenarioDefinition


@dataclass(frozen=True)
class ReplayGenerationEntry:
    """Deterministic plan and proposal assigned to one opaque trial."""

    declared_plan: tuple[str, ...]
    proposal: ActionProposal


class DeterministicRequestReplayGenerator:
    """Serve deterministic scenario proposals through the request API."""

    def __init__(
        self,
        entries: dict[
            str,
            ReplayGenerationEntry,
        ],
        *,
        generator_name: str = (
            "deterministic-request-replay"
        ),
    ) -> None:
        normalized_name = generator_name.strip()

        if not normalized_name:
            raise ValueError(
                "generator_name must not be empty"
            )

        if not entries:
            raise ValueError(
                "at least one replay entry is required"
            )

        self._generator_name = normalized_name

        self._entries = {
            trial_id: ReplayGenerationEntry(
                declared_plan=tuple(
                    entry.declared_plan
                ),
                proposal=entry.proposal.model_copy(
                    deep=True,
                ),
            )
            for trial_id, entry in entries.items()
        }

        self._seen_trial_ids: list[str] = []

    @property
    def seen_trial_ids(self) -> tuple[str, ...]:
        """Return trial IDs handled by this generator."""

        return tuple(self._seen_trial_ids)

    def generate_request(
        self,
        request: ProposalGenerationRequest,
    ) -> ProposalGenerationResult:
        """Return the deterministic entry assigned to the trial."""

        try:
            entry = self._entries[
                request.trial_id
            ]
        except KeyError as exc:
            raise KeyError(
                "no deterministic replay entry for "
                f"trial_id={request.trial_id}"
            ) from exc

        self._seen_trial_ids.append(
            request.trial_id
        )

        proposal = entry.proposal.model_copy(
            deep=True,
        )

        generation_material = json.dumps(
            {
                "trial_id": request.trial_id,
                "declared_plan": list(
                    entry.declared_plan
                ),
                "proposal": proposal.model_dump(
                    mode="json",
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        generation_id = hashlib.sha256(
            generation_material.encode(
                "utf-8"
            )
        ).hexdigest()

        return ProposalGenerationResult(
            generation_id=generation_id,
            generator_name=(
                self._generator_name
            ),
            declared_plan=list(
                entry.declared_plan
            ),
            proposal=proposal,
        )


RequestReplayFactory = Callable[
    [int],
    DeterministicRequestReplayGenerator,
]


def build_request_replay_factory(
    scenarios: Iterable[
        ScenarioDefinition
    ],
    *,
    experiment_id: str,
    generator_name: str = (
        "deterministic-request-replay"
    ),
) -> RequestReplayFactory:
    """Build a repetition-scoped request-aware replay factory."""

    scenario_list = list(scenarios)

    if not scenario_list:
        raise ValueError(
            "at least one scenario is required"
        )

    scenario_ids = [
        scenario.scenario_id
        for scenario in scenario_list
    ]

    if len(set(scenario_ids)) != len(
        scenario_ids
    ):
        raise ValueError(
            "scenario IDs must be unique"
        )

    normalized_experiment_id = (
        experiment_id.strip()
    )

    if not normalized_experiment_id:
        raise ValueError(
            "experiment_id must not be empty"
        )

    def factory(
        repetition_index: int,
    ) -> DeterministicRequestReplayGenerator:
        entries: dict[
            str,
            ReplayGenerationEntry,
        ] = {}

        for scenario in scenario_list:
            request = (
                build_proposal_generation_request(
                    scenario,
                    experiment_id=(
                        normalized_experiment_id
                    ),
                    repetition_index=(
                        repetition_index
                    ),
                )
            )

            if request.trial_id in entries:
                raise ValueError(
                    "duplicate trial ID generated for "
                    f"scenario={scenario.scenario_id}"
                )

            entries[
                request.trial_id
            ] = ReplayGenerationEntry(
                declared_plan=tuple(
                    scenario.context.declared_plan
                ),
                proposal=(
                    scenario.proposal.model_copy(
                        deep=True,
                    )
                ),
            )

        return (
            DeterministicRequestReplayGenerator(
                entries,
                generator_name=(
                    generator_name
                ),
            )
        )

    return factory
