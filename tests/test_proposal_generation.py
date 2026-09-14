from __future__ import annotations

import json
from pathlib import Path

from eg_runtime.models import ActionProposal
from eg_runtime.proposal_generation import (
    DeterministicProposalGenerator,
    ProposalGenerationResult,
)
from eg_runtime.scenarios import (
    ScenarioDefinition,
    load_scenario,
)
from eg_runtime.simulation import SimulationRunner


SCENARIO_PATH = Path(
    "scenarios/canonical/approval_binding_benign.yaml"
)


def _recorded_proposal_ids(
    evidence_path: str,
) -> set[str]:
    identifiers: set[str] = set()

    for line in Path(evidence_path).read_text(
        encoding="utf-8"
    ).splitlines():
        event = json.loads(line)
        payload = event.get("payload", {})

        if not isinstance(payload, dict):
            continue

        proposal = payload.get("proposal")

        if not isinstance(proposal, dict):
            continue

        proposal_id = proposal.get("proposal_id")

        if isinstance(proposal_id, str):
            identifiers.add(proposal_id)

    return identifiers


def test_deterministic_generator_is_stable() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    generator = DeterministicProposalGenerator()

    first = generator.generate(scenario)
    second = generator.generate(scenario)

    assert first.generation_id == second.generation_id
    assert first.declared_plan == scenario.context.declared_plan
    assert first.proposal == scenario.proposal
    assert first.proposal is not scenario.proposal


class CountingGenerator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(
        self,
        scenario: ScenarioDefinition,
    ) -> ProposalGenerationResult:
        self.calls.append(scenario.scenario_id)

        proposal = ActionProposal(
            proposal_id=(
                f"generated-{scenario.scenario_id}"
            ),
            tool=scenario.proposal.tool,
            arguments=dict(
                scenario.proposal.arguments
            ),
            justification=(
                scenario.proposal.justification
            ),
            declared_purpose=(
                scenario.proposal.declared_purpose
            ),
            instruction_source_ids=(
                list(
                    scenario.proposal
                    .instruction_source_ids
                )
                if (
                    scenario.proposal
                    .instruction_source_ids
                    is not None
                )
                else None
            ),
        )

        return ProposalGenerationResult(
            generation_id="a" * 64,
            generator_name="counting-test-generator",
            declared_plan=[
                "generated shared plan",
            ],
            proposal=proposal,
        )


def test_generation_is_reused_across_c0_c3(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(SCENARIO_PATH)
    generator = CountingGenerator()

    artifacts = SimulationRunner(
        proposal_generator=generator,
    ).run(
        [scenario],
        tmp_path / "generated-simulation",
    )

    assert generator.calls == [
        scenario.scenario_id,
    ]

    assert len(artifacts.runs) == 4

    expected_proposal_id = (
        f"generated-{scenario.scenario_id}"
    )

    for record in artifacts.runs:
        assert _recorded_proposal_ids(
            record.evidence_path
        ) == {
            expected_proposal_id,
        }

