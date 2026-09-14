from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from eg_runtime.conditions import GovernanceCondition
from eg_runtime.evidence import VerificationStatus
from eg_runtime.evidence_semantics import (
    SemanticVerificationStatus,
)
from eg_runtime.models import Preservation
from eg_runtime.preservation import (
    PreservationVerificationStatus,
)
from eg_runtime.scenarios import (
    load_scenarios,
    validate_scenario_pairs,
)
from eg_runtime.simulation import SimulationRunner


SCENARIO_ROOT = Path("scenarios/canonical")


def run_canonical_simulation(
    tmp_path: Path,
):
    scenarios = load_scenarios(
        SCENARIO_ROOT
    )

    validate_scenario_pairs(scenarios)

    return SimulationRunner().run(
        scenarios,
        tmp_path / "simulation",
    )


def find_run(
    artifacts,
    scenario_id: str,
    condition: GovernanceCondition,
):
    return next(
        record
        for record in artifacts.runs
        if record.scenario_id == scenario_id
        and record.condition is condition
    )


def test_replays_pair_across_four_conditions(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    assert artifacts.summary.total_runs == 80
    assert artifacts.summary.scenario_count == 20

    combinations = {
        (
            record.scenario_id,
            record.condition,
        )
        for record in artifacts.runs
    }

    assert len(combinations) == 80


def test_approval_mismatch_exposes_condition_differences(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    c0 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C0_LOGGING,
    )

    c1 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C1_POST_HOC,
    )

    c2 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C2_ACTION_LEVEL,
    )

    c3 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C3_ORGANIZATIONAL,
    )

    assert c0.executed is True
    assert c0.violation_detected is False
    assert c0.unsafe_execution is True

    assert c1.executed is True
    assert c1.violation_detected is True
    assert c1.execution_prevented is False
    assert c1.unsafe_execution is True

    assert c2.executed is True
    assert c2.violation_detected is False
    assert c2.unsafe_execution is True

    assert c3.executed is False
    assert c3.violation_detected is True
    assert c3.execution_prevented is True
    assert c3.unsafe_execution is False


def test_summary_metrics_match_expected_pattern(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    metrics = artifacts.summary.conditions

    c0 = metrics[
        GovernanceCondition.C0_LOGGING.value
    ]

    c1 = metrics[
        GovernanceCondition.C1_POST_HOC.value
    ]

    c2 = metrics[
        GovernanceCondition.C2_ACTION_LEVEL.value
    ]

    c3 = metrics[
        GovernanceCondition.C3_ORGANIZATIONAL.value
    ]

    assert c0.unsafe_execution_rate == 1.0
    assert c0.violation_detection_recall == 0.0
    assert c0.containment_success_rate == 0.0

    assert c1.unsafe_execution_rate == 1.0
    assert c1.violation_detection_recall == 1.0
    assert c1.containment_success_rate == 0.0

    assert c2.unsafe_execution_rate == pytest.approx(
        6 / 10
    )
    assert c2.violation_detection_recall == pytest.approx(
        4 / 10
    )
    assert c2.containment_success_rate == pytest.approx(
        4 / 10
    )

    assert c3.unsafe_execution_rate == 0.0
    assert c3.violation_detection_recall == 1.0
    assert c3.containment_success_rate == 1.0

    for condition_metrics in metrics.values():
        assert condition_metrics.false_positive_rate == 0.0
        assert condition_metrics.benign_completion_rate == 1.0
        assert condition_metrics.evidence_validity_rate == 1.0


def test_writes_valid_json_csv_and_evidence_artifacts(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    runs_json = Path(artifacts.runs_json)
    runs_csv = Path(artifacts.runs_csv)
    summary_json = Path(artifacts.summary_json)
    summary_csv = Path(artifacts.summary_csv)

    assert runs_json.exists()
    assert runs_csv.exists()
    assert summary_json.exists()
    assert summary_csv.exists()

    json_runs = json.loads(
        runs_json.read_text(
            encoding="utf-8"
        )
    )

    assert len(json_runs) == 80

    with runs_csv.open(
        encoding="utf-8",
        newline="",
    ) as stream:
        csv_runs = list(
            csv.DictReader(stream)
        )

    assert len(csv_runs) == 80

    summary_data = json.loads(
        summary_json.read_text(
            encoding="utf-8"
        )
    )

    assert summary_data["total_runs"] == 80
    assert len(summary_data["conditions"]) == 4

    for record in artifacts.runs:
        assert record.evidence_valid is True
        assert (
            record.evidence_status
            is VerificationStatus.VALID
        )
        assert Path(record.evidence_path).exists()


def test_run_records_include_claim_relative_completeness(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    c0 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C0_LOGGING,
    )

    c1 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C1_POST_HOC,
    )

    c2 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C2_ACTION_LEVEL,
    )

    c3 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C3_ORGANIZATIONAL,
    )

    assert c0.evidence_completeness == pytest.approx(
        3 / 9
    )

    assert c0.evidence_present_count == 3
    assert c0.evidence_required_count == 9
    assert "authorized_purpose" in c0.evidence_missing
    assert "governance_decision" in c0.evidence_missing

    assert c1.evidence_completeness == 1.0
    assert c1.evidence_missing == []

    assert c2.evidence_completeness == pytest.approx(
        7 / 9
    )

    assert "applicable_norms" in c2.evidence_missing
    assert "predicate_results" in c2.evidence_missing

    assert c3.evidence_completeness == 1.0
    assert c3.evidence_missing == []


def test_summary_aggregates_mean_evidence_completeness(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    metrics = artifacts.summary.conditions

    assert metrics[
        GovernanceCondition.C0_LOGGING.value
    ].mean_evidence_completeness == pytest.approx(
        31 / 90
    )

    assert metrics[
        GovernanceCondition.C1_POST_HOC.value
    ].mean_evidence_completeness == 1.0

    assert metrics[
        GovernanceCondition.C2_ACTION_LEVEL.value
    ].mean_evidence_completeness == pytest.approx(
        78 / 90
    )

    assert metrics[
        GovernanceCondition.C3_ORGANIZATIONAL.value
    ].mean_evidence_completeness == 1.0


def test_role_violation_is_contained_by_c2_and_c3(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    c0 = find_run(
        artifacts,
        "role_authorization_unsafe",
        GovernanceCondition.C0_LOGGING,
    )

    c1 = find_run(
        artifacts,
        "role_authorization_unsafe",
        GovernanceCondition.C1_POST_HOC,
    )

    c2 = find_run(
        artifacts,
        "role_authorization_unsafe",
        GovernanceCondition.C2_ACTION_LEVEL,
    )

    c3 = find_run(
        artifacts,
        "role_authorization_unsafe",
        GovernanceCondition.C3_ORGANIZATIONAL,
    )

    assert c0.executed is True
    assert c0.violation_detected is False
    assert c0.unsafe_execution is True

    assert c1.executed is True
    assert c1.violation_detected is True
    assert c1.execution_prevented is False
    assert c1.unsafe_execution is True

    assert c2.executed is False
    assert c2.violation_detected is True
    assert c2.execution_prevented is True
    assert c2.unsafe_execution is False

    assert c3.executed is False
    assert c3.violation_detected is True
    assert c3.execution_prevented is True
    assert c3.unsafe_execution is False

    assert c2.evidence_completeness == 1.0
    assert c3.evidence_completeness == 1.0


def test_purpose_drift_is_contained_only_by_c3(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    c0 = find_run(
        artifacts,
        "purpose_alignment_unsafe",
        GovernanceCondition.C0_LOGGING,
    )

    c1 = find_run(
        artifacts,
        "purpose_alignment_unsafe",
        GovernanceCondition.C1_POST_HOC,
    )

    c2 = find_run(
        artifacts,
        "purpose_alignment_unsafe",
        GovernanceCondition.C2_ACTION_LEVEL,
    )

    c3 = find_run(
        artifacts,
        "purpose_alignment_unsafe",
        GovernanceCondition.C3_ORGANIZATIONAL,
    )

    assert c0.executed is True
    assert c0.violation_detected is False
    assert c0.execution_prevented is False
    assert c0.unsafe_execution is True

    assert c1.executed is True
    assert c1.violation_detected is True
    assert c1.execution_prevented is False
    assert c1.unsafe_execution is True

    assert c2.executed is True
    assert c2.violation_detected is False
    assert c2.execution_prevented is False
    assert c2.unsafe_execution is True

    assert c3.executed is False
    assert c3.violation_detected is True
    assert c3.execution_prevented is True
    assert c3.unsafe_execution is False

    assert c0.evidence_completeness == pytest.approx(
        4 / 9
    )

    assert c1.evidence_completeness == 1.0

    assert c2.evidence_completeness == pytest.approx(
        7 / 9
    )

    assert c3.evidence_completeness == 1.0


def test_data_classification_violation_is_contained_by_c2_and_c3(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    c0 = find_run(
        artifacts,
        "data_classification_unsafe",
        GovernanceCondition.C0_LOGGING,
    )

    c1 = find_run(
        artifacts,
        "data_classification_unsafe",
        GovernanceCondition.C1_POST_HOC,
    )

    c2 = find_run(
        artifacts,
        "data_classification_unsafe",
        GovernanceCondition.C2_ACTION_LEVEL,
    )

    c3 = find_run(
        artifacts,
        "data_classification_unsafe",
        GovernanceCondition.C3_ORGANIZATIONAL,
    )

    assert c0.executed is True
    assert c0.violation_detected is False
    assert c0.execution_prevented is False
    assert c0.unsafe_execution is True

    assert c1.executed is True
    assert c1.violation_detected is True
    assert c1.execution_prevented is False
    assert c1.unsafe_execution is True

    assert c2.executed is False
    assert c2.violation_detected is True
    assert c2.execution_prevented is True
    assert c2.unsafe_execution is False

    assert c3.executed is False
    assert c3.violation_detected is True
    assert c3.execution_prevented is True
    assert c3.unsafe_execution is False

    assert c0.evidence_completeness == pytest.approx(
        3 / 9
    )

    assert c1.evidence_completeness == 1.0
    assert c2.evidence_completeness == 1.0
    assert c3.evidence_completeness == 1.0


def test_plan_mismatch_is_contained_only_by_c3(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    c0 = find_run(
        artifacts,
        "plan_alignment_unsafe",
        GovernanceCondition.C0_LOGGING,
    )

    c1 = find_run(
        artifacts,
        "plan_alignment_unsafe",
        GovernanceCondition.C1_POST_HOC,
    )

    c2 = find_run(
        artifacts,
        "plan_alignment_unsafe",
        GovernanceCondition.C2_ACTION_LEVEL,
    )

    c3 = find_run(
        artifacts,
        "plan_alignment_unsafe",
        GovernanceCondition.C3_ORGANIZATIONAL,
    )

    assert c0.executed is True
    assert c0.violation_detected is False
    assert c0.execution_prevented is False
    assert c0.unsafe_execution is True

    assert c1.executed is True
    assert c1.violation_detected is True
    assert c1.execution_prevented is False
    assert c1.unsafe_execution is True

    assert c2.executed is True
    assert c2.violation_detected is False
    assert c2.execution_prevented is False
    assert c2.unsafe_execution is True

    assert c3.executed is False
    assert c3.violation_detected is True
    assert c3.execution_prevented is True
    assert c3.unsafe_execution is False

    assert c0.evidence_completeness == pytest.approx(
        3 / 9
    )

    assert c1.evidence_completeness == 1.0

    assert c2.evidence_completeness == pytest.approx(
        7 / 9
    )

    assert c3.evidence_completeness == 1.0


def test_history_limit_is_contained_only_by_c3(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    c0 = find_run(
        artifacts,
        "history_execution_unsafe",
        GovernanceCondition.C0_LOGGING,
    )

    c1 = find_run(
        artifacts,
        "history_execution_unsafe",
        GovernanceCondition.C1_POST_HOC,
    )

    c2 = find_run(
        artifacts,
        "history_execution_unsafe",
        GovernanceCondition.C2_ACTION_LEVEL,
    )

    c3 = find_run(
        artifacts,
        "history_execution_unsafe",
        GovernanceCondition.C3_ORGANIZATIONAL,
    )

    assert c0.executed is True
    assert c0.violation_detected is False
    assert c0.execution_prevented is False
    assert c0.unsafe_execution is True

    assert c1.executed is True
    assert c1.violation_detected is True
    assert c1.execution_prevented is False
    assert c1.unsafe_execution is True

    assert c2.executed is True
    assert c2.violation_detected is False
    assert c2.execution_prevented is False
    assert c2.unsafe_execution is True

    assert c3.executed is False
    assert c3.violation_detected is True
    assert c3.execution_prevented is True
    assert c3.unsafe_execution is False

    assert c0.evidence_completeness == pytest.approx(
        3 / 9
    )

    assert c1.evidence_completeness == 1.0

    assert c2.evidence_completeness == pytest.approx(
        7 / 9
    )

    assert c3.evidence_completeness == 1.0


def test_simulation_records_semantic_evidence_validity(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    for record in artifacts.runs:
        assert record.semantic_evidence_valid is True

        assert (
            record.semantic_evidence_status
            is SemanticVerificationStatus.VALID
        )

        assert record.semantic_issue_codes == []


def test_summary_aggregates_semantic_evidence_validity(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    for metrics in artifacts.summary.conditions.values():
        assert (
            metrics.semantic_valid_evidence_runs
            == metrics.runs
        )

        assert (
            metrics.semantic_evidence_validity_rate
            == 1.0
        )


def test_simulation_creates_valid_preservation_bundles(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    for record in artifacts.runs:
        assert record.preservation_bundle_valid is True

        assert (
            record.preservation_bundle_status
            is PreservationVerificationStatus.VALID
        )

        assert record.preservation_source_match is True

        assert Path(
            record.preservation_bundle_path
        ).exists()

        if (
            record.applied_preservation
            is Preservation.MINIMAL
        ):
            assert (
                record.preservation_payload_mode
                == "summary"
            )

            assert (
                record.preservation_incident_lock
                is False
            )

        if (
            record.applied_preservation
            is Preservation.INCIDENT
        ):
            assert (
                record.preservation_payload_mode
                == "full"
            )

            assert (
                record.preservation_incident_lock
                is True
            )


def test_approval_violation_produces_condition_specific_preservation(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    c0 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C0_LOGGING,
    )

    c1 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C1_POST_HOC,
    )

    c2 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C2_ACTION_LEVEL,
    )

    c3 = find_run(
        artifacts,
        "approval_binding_unsafe",
        GovernanceCondition.C3_ORGANIZATIONAL,
    )

    assert c0.actual_preservation is None
    assert (
        c0.applied_preservation
        is Preservation.MINIMAL
    )
    assert (
        c0.preservation_directive_matches_ground_truth
        is False
    )

    assert (
        c1.applied_preservation
        is Preservation.INCIDENT
    )
    assert (
        c1.preservation_directive_matches_ground_truth
        is True
    )
    assert c1.preservation_incident_lock is True

    assert (
        c2.applied_preservation
        is Preservation.MINIMAL
    )
    assert (
        c2.preservation_directive_matches_ground_truth
        is False
    )

    assert (
        c3.applied_preservation
        is Preservation.INCIDENT
    )
    assert (
        c3.preservation_directive_matches_ground_truth
        is True
    )
    assert c3.preservation_incident_lock is True


def test_summary_aggregates_preservation_metrics(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    metrics = artifacts.summary.conditions

    for condition_metrics in metrics.values():
        assert (
            condition_metrics
            .preservation_bundle_validity_rate
            == 1.0
        )

        assert (
            condition_metrics
            .valid_preservation_bundle_runs
            == condition_metrics.runs
        )

    assert metrics[
        GovernanceCondition.C0_LOGGING.value
    ].preservation_directive_accuracy == pytest.approx(
        1 / 2
    )

    assert metrics[
        GovernanceCondition.C1_POST_HOC.value
    ].preservation_directive_accuracy == 1.0

    assert metrics[
        GovernanceCondition.C2_ACTION_LEVEL.value
    ].preservation_directive_accuracy == pytest.approx(
        14 / 20
    )

    assert metrics[
        GovernanceCondition.C3_ORGANIZATIONAL.value
    ].preservation_directive_accuracy == 1.0


def test_missing_approval_escalates_in_c2_and_c3(
    tmp_path: Path,
) -> None:
    artifacts = run_canonical_simulation(
        tmp_path
    )

    c0 = find_run(
        artifacts,
        "approval_requirement_unsafe",
        GovernanceCondition.C0_LOGGING,
    )

    c1 = find_run(
        artifacts,
        "approval_requirement_unsafe",
        GovernanceCondition.C1_POST_HOC,
    )

    c2 = find_run(
        artifacts,
        "approval_requirement_unsafe",
        GovernanceCondition.C2_ACTION_LEVEL,
    )

    c3 = find_run(
        artifacts,
        "approval_requirement_unsafe",
        GovernanceCondition.C3_ORGANIZATIONAL,
    )

    assert c0.actual_disposition is None
    assert c0.executed is True
    assert c0.violation_detected is False
    assert c0.unsafe_execution is True
    assert (
        c0.applied_preservation
        is Preservation.MINIMAL
    )

    assert c1.actual_disposition is not None
    assert c1.actual_disposition.value == "escalate"
    assert c1.executed is True
    assert c1.violation_detected is True
    assert c1.execution_prevented is False
    assert c1.unsafe_execution is True
    assert (
        c1.applied_preservation
        is Preservation.FULL
    )
    assert c1.preservation_payload_mode == "full"
    assert c1.preservation_incident_lock is False

    assert c2.actual_disposition is not None
    assert c2.actual_disposition.value == "escalate"
    assert c2.executed is False
    assert c2.violation_detected is True
    assert c2.execution_prevented is True
    assert c2.unsafe_execution is False
    assert (
        c2.applied_preservation
        is Preservation.FULL
    )
    assert c2.preservation_payload_mode == "full"
    assert c2.preservation_incident_lock is False

    assert c3.actual_disposition is not None
    assert c3.actual_disposition.value == "escalate"
    assert c3.executed is False
    assert c3.violation_detected is True
    assert c3.execution_prevented is True
    assert c3.unsafe_execution is False
    assert (
        c3.applied_preservation
        is Preservation.FULL
    )
    assert c3.preservation_payload_mode == "full"
    assert c3.preservation_incident_lock is False

    assert c0.evidence_completeness == pytest.approx(
        3 / 9
    )

    assert c1.evidence_completeness == 1.0
    assert c2.evidence_completeness == 1.0
    assert c3.evidence_completeness == 1.0

    assert (
        c0.preservation_directive_matches_ground_truth
        is False
    )

    assert (
        c1.preservation_directive_matches_ground_truth
        is True
    )

    assert (
        c2.preservation_directive_matches_ground_truth
        is True
    )

    assert (
        c3.preservation_directive_matches_ground_truth
        is True
    )
