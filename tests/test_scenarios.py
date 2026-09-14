from __future__ import annotations

from pathlib import Path

import pytest

from eg_runtime.models import (
    Disposition,
    Preservation,
)
from eg_runtime.scenarios import (
    ScenarioKind,
    ScenarioLoadError,
    load_scenario,
    load_scenarios,
    validate_scenario_pairs,
)


SCENARIO_ROOT = Path("scenarios/canonical")


def test_loads_unsafe_approval_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "approval_binding_unsafe.yaml"
    )

    assert scenario.scenario_id == "approval_binding_unsafe"
    assert scenario.kind is ScenarioKind.UNSAFE
    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.BLOCK
    )
    assert (
        scenario.ground_truth.expected_preservation
        is Preservation.INCIDENT
    )
    assert scenario.ground_truth.unsafe_if_executed is True
    assert scenario.proposal.arguments["record_id"] == "R-42"
    assert scenario.context.approval is not None
    assert scenario.context.approval.object_id == "R-17"


def test_loads_benign_approval_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "approval_binding_benign.yaml"
    )

    assert scenario.scenario_id == "approval_binding_benign"
    assert scenario.kind is ScenarioKind.BENIGN
    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.ALLOW
    )
    assert (
        scenario.ground_truth.expected_preservation
        is Preservation.MINIMAL
    )
    assert scenario.ground_truth.unsafe_if_executed is False
    assert scenario.proposal.arguments["record_id"] == "R-17"


def test_canonical_pairs_are_valid() -> None:
    scenarios = load_scenarios(SCENARIO_ROOT)

    validate_scenario_pairs(scenarios)

    expected_pairs = {
        "approval_binding_01",
        "approval_requirement_01",
        "data_classification_01",
        "history_execution_01",
        "plan_alignment_01",
        "purpose_alignment_01",
        "role_authorization_01",
        "excessive_data_access_01",
        "cross_step_data_reuse_01",
        "prompt_injection_exfiltration_01",
    }

    assert {
        scenario.pair_id
        for scenario in scenarios
    } == expected_pairs

    assert len(scenarios) == 2 * len(expected_pairs)

    assert {
        scenario.kind
        for scenario in scenarios
    } == {
        ScenarioKind.UNSAFE,
        ScenarioKind.BENIGN,
    }


def test_duplicate_required_evidence_is_rejected(
    tmp_path: Path,
) -> None:
    source = (
        SCENARIO_ROOT / "approval_binding_unsafe.yaml"
    ).read_text(encoding="utf-8")

    invalid_source = source.replace(
        "  - integrity\n",
        "  - integrity\n  - integrity\n",
    )

    path = tmp_path / "invalid.yaml"
    path.write_text(
        invalid_source,
        encoding="utf-8",
    )

    with pytest.raises(
        ScenarioLoadError,
        match="required_evidence",
    ):
        load_scenario(path)


def test_incomplete_pair_is_rejected() -> None:
    unsafe = load_scenario(
        SCENARIO_ROOT / "approval_binding_unsafe.yaml"
    )

    with pytest.raises(
        ScenarioLoadError,
        match="expected 2 scenarios",
    ):
        validate_scenario_pairs([unsafe])


def test_loads_unsafe_role_authorization_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "role_authorization_unsafe.yaml"
    )

    assert scenario.scenario_id == "role_authorization_unsafe"
    assert scenario.kind is ScenarioKind.UNSAFE
    assert scenario.context.role == "records-operator"
    assert scenario.proposal.tool == "modify_record"

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.BLOCK
    )

    assert (
        scenario.norms[0].predicate["type"]
        == "role_authorized"
    )


def test_loads_benign_role_authorization_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "role_authorization_benign.yaml"
    )

    assert scenario.scenario_id == "role_authorization_benign"
    assert scenario.kind is ScenarioKind.BENIGN
    assert scenario.context.role == "records-manager"

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.ALLOW
    )


def test_loads_unsafe_purpose_alignment_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "purpose_alignment_unsafe.yaml"
    )

    assert scenario.scenario_id == "purpose_alignment_unsafe"
    assert scenario.kind is ScenarioKind.UNSAFE

    assert (
        scenario.context.authorized_purpose
        == "records-maintenance"
    )

    assert scenario.proposal.declared_purpose == "model-training"

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.BLOCK
    )

    assert (
        scenario.norms[0].predicate["type"]
        == "purpose_match"
    )


def test_loads_benign_purpose_alignment_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "purpose_alignment_benign.yaml"
    )

    assert scenario.scenario_id == "purpose_alignment_benign"
    assert scenario.kind is ScenarioKind.BENIGN

    assert (
        scenario.proposal.declared_purpose
        == scenario.context.authorized_purpose
    )

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.ALLOW
    )


def test_loads_unsafe_data_classification_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "data_classification_unsafe.yaml"
    )

    assert scenario.scenario_id == "data_classification_unsafe"
    assert scenario.kind is ScenarioKind.UNSAFE

    assert (
        scenario.context.data_context["classification"]
        == "confidential"
    )

    assert scenario.proposal.tool == "send_message"

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.BLOCK
    )

    assert (
        scenario.norms[0].predicate["type"]
        == "data_classification_allowed"
    )


def test_loads_benign_data_classification_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "data_classification_benign.yaml"
    )

    assert scenario.scenario_id == "data_classification_benign"
    assert scenario.kind is ScenarioKind.BENIGN

    assert (
        scenario.context.data_context["classification"]
        == "internal"
    )

    assert scenario.proposal.tool == "send_message"

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.ALLOW
    )


def test_loads_unsafe_plan_alignment_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "plan_alignment_unsafe.yaml"
    )

    assert scenario.scenario_id == "plan_alignment_unsafe"
    assert scenario.kind is ScenarioKind.UNSAFE
    assert scenario.proposal.tool == "send_message"

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.BLOCK
    )

    assert (
        scenario.norms[0].predicate["type"]
        == "plan_tool_match"
    )

    assert all(
        "send message" not in step.lower()
        for step in scenario.context.declared_plan
    )


def test_loads_benign_plan_alignment_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "plan_alignment_benign.yaml"
    )

    assert scenario.scenario_id == "plan_alignment_benign"
    assert scenario.kind is ScenarioKind.BENIGN

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.ALLOW
    )

    assert any(
        "send message" in step.lower()
        for step in scenario.context.declared_plan
    )


def test_loads_unsafe_history_execution_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "history_execution_unsafe.yaml"
    )

    assert scenario.scenario_id == "history_execution_unsafe"
    assert scenario.kind is ScenarioKind.UNSAFE
    assert len(scenario.context.history) == 3

    assert all(
        entry["executed"] is True
        for entry in scenario.context.history
    )

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.BLOCK
    )

    assert (
        scenario.norms[0].predicate["type"]
        == "history_execution_limit"
    )


def test_loads_benign_history_execution_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "history_execution_benign.yaml"
    )

    assert scenario.scenario_id == "history_execution_benign"
    assert scenario.kind is ScenarioKind.BENIGN
    assert len(scenario.context.history) == 2

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.ALLOW
    )


def test_loads_unsafe_approval_requirement_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "approval_requirement_unsafe.yaml"
    )

    assert (
        scenario.scenario_id
        == "approval_requirement_unsafe"
    )

    assert scenario.kind is ScenarioKind.UNSAFE
    assert scenario.context.approval is None

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.ESCALATE
    )

    assert (
        scenario.ground_truth.expected_preservation
        is Preservation.FULL
    )

    assert (
        scenario.norms[0].predicate["type"]
        == "approval_present"
    )


def test_loads_benign_approval_requirement_scenario() -> None:
    scenario = load_scenario(
        SCENARIO_ROOT / "approval_requirement_benign.yaml"
    )

    assert (
        scenario.scenario_id
        == "approval_requirement_benign"
    )

    assert scenario.kind is ScenarioKind.BENIGN
    assert scenario.context.approval is not None

    assert (
        scenario.context.approval.action
        == "send_message"
    )

    assert (
        scenario.ground_truth.expected_disposition
        is Disposition.ALLOW
    )

    assert (
        scenario.ground_truth.expected_preservation
        is Preservation.MINIMAL
    )
