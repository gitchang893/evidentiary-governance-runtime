from __future__ import annotations

import csv
import json
from pathlib import Path

from eg_runtime.proposal_generation import ProposalGenerationResult
from eg_runtime.scenarios import ScenarioDefinition, load_scenario
from eg_runtime.simulation import SimulationRunner


SCENARIO_PATH = Path(
    "scenarios/canonical/approval_binding_benign.yaml"
)


class MetadataGenerator:
    def generate(
        self,
        scenario: ScenarioDefinition,
    ) -> ProposalGenerationResult:
        return ProposalGenerationResult(
            generation_id="b" * 64,
            generator_name="metadata-test-generator",
            declared_plan=[
                "shared generated plan",
            ],
            proposal=scenario.proposal.model_copy(
                deep=True,
            ),
        )


def _start_metadata(
    evidence_path: str,
) -> dict[str, object]:
    lines = Path(evidence_path).read_text(
        encoding="utf-8",
    ).splitlines()

    assert lines

    event = json.loads(lines[0])

    assert event["event_type"] == "run_started"

    metadata = event["payload"]["metadata"]

    assert isinstance(metadata, dict)

    return metadata


def test_generation_metadata_is_shared_and_serialized(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(SCENARIO_PATH)

    artifacts = SimulationRunner(
        proposal_generator=MetadataGenerator(),
    ).run(
        [scenario],
        tmp_path / "generation-metadata",
    )

    assert len(artifacts.runs) == 4

    expected_conditions = {
        "c0_logging",
        "c1_post_hoc",
        "c2_action_level",
        "c3_organizational",
    }

    assert {
        record.condition.value
        for record in artifacts.runs
    } == expected_conditions

    for record in artifacts.runs:
        assert record.generation_id == "b" * 64

        assert (
            record.generator_name
            == "metadata-test-generator"
        )

        assert record.declared_plan == [
            "shared generated plan",
        ]

        metadata = _start_metadata(
            record.evidence_path,
        )

        assert metadata["generation_id"] == "b" * 64

        assert (
            metadata["generator_name"]
            == "metadata-test-generator"
        )

        assert metadata["declared_plan"] == [
            "shared generated plan",
        ]

        assert (
            metadata["condition"]
            == record.condition.value
        )

        assert (
            metadata["decision_phase"]
            == record.decision_phase.value
        )

    json_runs = json.loads(
        Path(artifacts.runs_json).read_text(
            encoding="utf-8",
        )
    )

    assert len(json_runs) == 4

    for run in json_runs:
        assert run["generation_id"] == "b" * 64

        assert (
            run["generator_name"]
            == "metadata-test-generator"
        )

        assert run["declared_plan"] == [
            "shared generated plan",
        ]

    with Path(artifacts.runs_csv).open(
        encoding="utf-8",
        newline="",
    ) as stream:
        csv_runs = list(
            csv.DictReader(stream)
        )

    assert len(csv_runs) == 4

    for run in csv_runs:
        assert run["generation_id"] == "b" * 64

        assert (
            run["generator_name"]
            == "metadata-test-generator"
        )

        assert (
            run["declared_plan"]
            == "shared generated plan"
        )
