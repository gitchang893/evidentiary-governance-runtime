from __future__ import annotations

import argparse
import csv
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import Field

from eg_runtime.conditions import (
    ComparativeConditionRunner,
    DecisionPhase,
    GovernanceCondition,
)
from eg_runtime.evidence import (
    EvidenceRecorder,
    EvidenceVerifier,
    VerificationStatus,
)
from eg_runtime.evidence_quality import (
    assess_evidence_completeness,
)
from eg_runtime.evidence_semantics import (
    EvidenceSemanticsVerifier,
    SemanticVerificationStatus,
)
from eg_runtime.generation_failures import (
    GenerationFailureRecord,
    ProposalGenerationError,
)
from eg_runtime.models import (
    Disposition,
    FrozenModel,
    Preservation,
)
from eg_runtime.preservation import (
    PreservationVerificationStatus,
    create_preservation_bundle,
    verify_preservation_bundle,
)
from eg_runtime.proposal_generation import (
    DeterministicProposalGenerator,
    ProposalGenerationRequest,
    ProposalGenerationResult,
    ProposalGenerator,
    RequestProposalGenerator,
    build_proposal_generation_request,
    generate_proposal,
)
from eg_runtime.scenarios import (
    SandboxState,
    ScenarioDefinition,
    ScenarioKind,
    load_scenarios,
    validate_scenario_pairs,
)
from eg_runtime.tools import (
    ModifyRecordTool,
    ReadDocumentTool,
    ReadDocumentsTool,
    RequestHumanApprovalTool,
    SendMessageTool,
    ToolRegistry,
    WriteReportTool,
)


DEFAULT_SIMULATION_TIME = datetime(
    2026,
    7,
    22,
    10,
    0,
    tzinfo=UTC,
)


class SimulationError(RuntimeError):
    """Raised when a comparative simulation cannot be completed."""


class IncrementingClock:
    """Return deterministic timezone-aware timestamps."""

    def __init__(
        self,
        start: datetime = DEFAULT_SIMULATION_TIME,
        step: timedelta = timedelta(milliseconds=1),
    ) -> None:
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("clock start must be timezone-aware")

        if step <= timedelta(0):
            raise ValueError("clock step must be positive")

        self._current = start
        self._step = step

    def __call__(self) -> datetime:
        value = self._current
        self._current += self._step
        return value


class SimulationRunRecord(FrozenModel):
    """Run-level result for one scenario and governance condition."""

    run_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    kind: ScenarioKind
    condition: GovernanceCondition
    trial_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    repetition_index: int = Field(ge=0)
    generation_id: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    generator_name: str = Field(min_length=1)
    declared_plan: list[str] = Field(min_length=1)
    decision_phase: DecisionPhase
    expected_disposition: Disposition
    expected_preservation: Preservation
    actual_disposition: Disposition | None
    actual_preservation: Preservation | None
    applied_preservation: Preservation
    preservation_directive_matches_ground_truth: bool
    preservation_bundle_valid: bool
    preservation_bundle_status: PreservationVerificationStatus
    preservation_payload_mode: str = Field(
        pattern=r"^(summary|full)$"
    )
    preservation_incident_lock: bool
    preservation_source_match: bool
    preservation_bundle_path: str = Field(min_length=1)
    executed: bool
    side_effect: bool
    operational_outcome: str = Field(min_length=1)
    violation_detected: bool
    execution_prevented: bool
    unsafe_if_executed: bool
    unsafe_execution: bool
    benign_completion: bool
    decision_matches_ground_truth: bool | None
    evidence_valid: bool
    evidence_status: VerificationStatus
    semantic_evidence_valid: bool
    semantic_evidence_status: SemanticVerificationStatus
    semantic_issue_codes: list[str] = Field(
        default_factory=list
    )
    evidence_completeness: float = Field(
        ge=0.0,
        le=1.0,
    )
    evidence_required_count: int = Field(ge=1)
    evidence_present_count: int = Field(ge=0)
    evidence_missing: list[str] = Field(default_factory=list)
    evidence_event_count: int = Field(ge=1)
    evidence_final_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    evidence_path: str = Field(min_length=1)


class ConditionMetrics(FrozenModel):
    """Aggregated metrics for one governance condition."""

    condition: GovernanceCondition
    runs: int = Field(ge=0)
    unsafe_runs: int = Field(ge=0)
    benign_runs: int = Field(ge=0)
    unsafe_executions: int = Field(ge=0)
    detected_unsafe_runs: int = Field(ge=0)
    prevented_unsafe_runs: int = Field(ge=0)
    false_positive_runs: int = Field(ge=0)
    completed_benign_runs: int = Field(ge=0)
    valid_evidence_runs: int = Field(ge=0)
    semantic_valid_evidence_runs: int = Field(ge=0)
    valid_preservation_bundle_runs: int = Field(ge=0)
    correct_preservation_directive_runs: int = Field(ge=0)
    decision_evaluated_runs: int = Field(ge=0)
    correct_decision_runs: int = Field(ge=0)
    unsafe_execution_rate: float | None
    violation_detection_recall: float | None
    containment_success_rate: float | None
    false_positive_rate: float | None
    benign_completion_rate: float | None
    evidence_validity_rate: float | None
    semantic_evidence_validity_rate: float | None
    preservation_bundle_validity_rate: float | None
    preservation_directive_accuracy: float | None
    decision_accuracy: float | None
    mean_evidence_completeness: float | None
    mean_evidence_event_count: float | None


class SimulationSummary(FrozenModel):
    """Condition-level summary for a comparative simulation."""

    total_runs: int = Field(ge=0)
    scenario_count: int = Field(ge=0)

    attempted_generations: int = Field(default=0, ge=0)
    successful_generations: int = Field(default=0, ge=0)
    failed_generations: int = Field(default=0, ge=0)
    generation_completion_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    conditions: dict[str, ConditionMetrics]


class SimulationGenerationFailureRecord(FrozenModel):
    """Generation failure associated with one scenario."""

    scenario_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    failure: GenerationFailureRecord


class SimulationArtifacts(FrozenModel):
    """Paths and records produced by a simulation."""

    runs: list[SimulationRunRecord]

    generation_failures: list[
        SimulationGenerationFailureRecord
    ] = Field(default_factory=list)
    summary: SimulationSummary
    runs_json: str
    runs_csv: str
    summary_json: str
    summary_csv: str



    generation_failures_json: str = ""
    generation_failures_csv: str = ""
def build_tool_registry(
    sandbox: SandboxState,
) -> ToolRegistry:
    """Create a fresh sandboxed capability registry."""

    documents = deepcopy(sandbox.documents)
    reports = deepcopy(sandbox.reports)
    messages = deepcopy(sandbox.messages)
    records = deepcopy(sandbox.records)
    approval_requests = deepcopy(sandbox.approval_requests)

    return ToolRegistry(
        [
            ReadDocumentTool(documents),
            ReadDocumentsTool(documents),
            WriteReportTool(reports),
            SendMessageTool(messages),
            ModifyRecordTool(records),
            RequestHumanApprovalTool(approval_requests),
        ]
    )


def _ratio(
    numerator: int,
    denominator: int,
) -> float | None:
    """Calculate a rate while preserving an empty denominator."""

    if denominator == 0:
        return None

    return numerator / denominator


def summarize_runs(
    records: Sequence[SimulationRunRecord],
) -> SimulationSummary:
    """Aggregate run-level records by governance condition."""

    metrics: dict[str, ConditionMetrics] = {}

    present_conditions = sorted(
        {
            record.condition
            for record in records
        },
        key=lambda condition: condition.value,
    )

    for condition in present_conditions:
        subset = [
            record
            for record in records
            if record.condition is condition
        ]

        unsafe = [
            record
            for record in subset
            if record.kind is ScenarioKind.UNSAFE
        ]

        benign = [
            record
            for record in subset
            if record.kind is ScenarioKind.BENIGN
        ]

        unsafe_executions = sum(
            record.unsafe_execution
            for record in unsafe
        )

        detected_unsafe = sum(
            record.violation_detected
            for record in unsafe
        )

        prevented_unsafe = sum(
            record.execution_prevented
            for record in unsafe
        )

        false_positives = sum(
            record.violation_detected
            for record in benign
        )

        completed_benign = sum(
            record.benign_completion
            for record in benign
        )

        valid_evidence = sum(
            record.evidence_valid
            for record in subset
        )

        semantic_valid_evidence = sum(
            record.semantic_evidence_valid
            for record in subset
        )

        valid_preservation_bundles = sum(
            record.preservation_bundle_valid
            for record in subset
        )

        correct_preservation_directives = sum(
            record.preservation_directive_matches_ground_truth
            for record in subset
        )

        evaluated_decisions = [
            record
            for record in subset
            if record.decision_matches_ground_truth is not None
        ]

        correct_decisions = sum(
            record.decision_matches_ground_truth is True
            for record in evaluated_decisions
        )

        mean_evidence_completeness = (
            sum(
                record.evidence_completeness
                for record in subset
            )
            / len(subset)
            if subset
            else None
        )

        mean_event_count = (
            sum(
                record.evidence_event_count
                for record in subset
            )
            / len(subset)
            if subset
            else None
        )

        condition_metrics = ConditionMetrics(
            condition=condition,
            runs=len(subset),
            unsafe_runs=len(unsafe),
            benign_runs=len(benign),
            unsafe_executions=unsafe_executions,
            detected_unsafe_runs=detected_unsafe,
            prevented_unsafe_runs=prevented_unsafe,
            false_positive_runs=false_positives,
            completed_benign_runs=completed_benign,
            valid_evidence_runs=valid_evidence,
            semantic_valid_evidence_runs=(
                semantic_valid_evidence
            ),
            valid_preservation_bundle_runs=(
                valid_preservation_bundles
            ),
            correct_preservation_directive_runs=(
                correct_preservation_directives
            ),
            decision_evaluated_runs=len(evaluated_decisions),
            correct_decision_runs=correct_decisions,
            unsafe_execution_rate=_ratio(
                unsafe_executions,
                len(unsafe),
            ),
            violation_detection_recall=_ratio(
                detected_unsafe,
                len(unsafe),
            ),
            containment_success_rate=_ratio(
                prevented_unsafe,
                len(unsafe),
            ),
            false_positive_rate=_ratio(
                false_positives,
                len(benign),
            ),
            benign_completion_rate=_ratio(
                completed_benign,
                len(benign),
            ),
            evidence_validity_rate=_ratio(
                valid_evidence,
                len(subset),
            ),
            semantic_evidence_validity_rate=_ratio(
                semantic_valid_evidence,
                len(subset),
            ),
            preservation_bundle_validity_rate=_ratio(
                valid_preservation_bundles,
                len(subset),
            ),
            preservation_directive_accuracy=_ratio(
                correct_preservation_directives,
                len(subset),
            ),
            decision_accuracy=_ratio(
                correct_decisions,
                len(evaluated_decisions),
            ),
            mean_evidence_completeness=(
                mean_evidence_completeness
            ),
            mean_evidence_event_count=mean_event_count,
        )

        metrics[condition.value] = condition_metrics

    return SimulationSummary(
        total_runs=len(records),
        scenario_count=len(
            {
                record.scenario_id
                for record in records
            }
        ),
        conditions=metrics,
    )


def _proposal_generator_name(
    generator: object,
) -> str:
    """Return a stable generator identifier."""

    for attribute in (
        "name",
        "generator_name",
        "_generator_name",
    ):
        value = getattr(generator, attribute, None)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return generator.__class__.__name__


def _write_generation_failure_artifacts(
    records: list[SimulationGenerationFailureRecord],
    output_root: Path,
) -> tuple[Path, Path]:
    """Write generation failures independently of governed runs."""

    json_path = output_root / "generation-failures.json"
    csv_path = output_root / "generation-failures.csv"

    json_path.write_text(
        json.dumps(
            [
                record.model_dump(mode="json")
                for record in records
            ],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    fieldnames = [
        "scenario_id",
        "pair_id",
        "family",
        "failure_id",
        "trial_id",
        "repetition_index",
        "generator_name",
        "kind",
        "message",
        "retryable",
        "raw_response_hash",
        "details",
    ]

    with csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for record in records:
            failure = record.failure

            writer.writerow(
                {
                    "scenario_id": record.scenario_id,
                    "pair_id": record.pair_id,
                    "family": record.family,
                    "failure_id": failure.failure_id,
                    "trial_id": failure.trial_id,
                    "repetition_index": (
                        failure.repetition_index
                    ),
                    "generator_name": (
                        failure.generator_name
                    ),
                    "kind": failure.kind.value,
                    "message": failure.message,
                    "retryable": failure.retryable,
                    "raw_response_hash": (
                        failure.raw_response_hash
                    ),
                    "details": json.dumps(
                        failure.details,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )

    return json_path, csv_path


class SimulationRunner:
    """Replay typed scenarios across comparative conditions."""

    def __init__(
        self,
        *,
        base_time: datetime = DEFAULT_SIMULATION_TIME,
        proposal_generator: (
            ProposalGenerator
            | RequestProposalGenerator
            | None
        ) = None,
        experiment_id: str = "deterministic-simulation",
        repetition_index: int = 0,
        continue_on_generation_failure: bool = False,
    ) -> None:
        if (
            base_time.tzinfo is None
            or base_time.utcoffset() is None
        ):
            raise ValueError(
                "simulation base_time must be timezone-aware"
            )

        self._base_time = base_time
        self._proposal_generator = (
            proposal_generator
            if proposal_generator is not None
            else DeterministicProposalGenerator()
        )

        normalized_experiment_id = experiment_id.strip()

        if not normalized_experiment_id:
            raise ValueError(
                "experiment_id must not be empty"
            )

        if repetition_index < 0:
            raise ValueError(
                "repetition_index must not be negative"
            )

        self._experiment_id = normalized_experiment_id
        self._repetition_index = repetition_index

        self._continue_on_generation_failure = (
            continue_on_generation_failure
        )
    def run(
        self,
        scenarios: Sequence[ScenarioDefinition],
        output_directory: str | Path,
        *,
        conditions: Iterable[GovernanceCondition] | None = None,
    ) -> SimulationArtifacts:
        """Run each scenario under each selected condition."""

        scenario_list = sorted(
            scenarios,
            key=lambda scenario: scenario.scenario_id,
        )

        condition_list = list(
            conditions
            if conditions is not None
            else GovernanceCondition
        )

        if not scenario_list:
            raise SimulationError(
                "at least one scenario is required"
            )

        if not condition_list:
            raise SimulationError(
                "at least one governance condition is required"
            )

        output_root = Path(output_directory)
        evidence_root = output_root / "evidence"
        evidence_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        records: list[SimulationRunRecord] = []


        generation_failures: list[
            SimulationGenerationFailureRecord
        ] = []
        for scenario in scenario_list:
            request = build_proposal_generation_request(
                scenario,
                experiment_id=self._experiment_id,
                repetition_index=self._repetition_index,
            )

            try:
                generation = generate_proposal(
                    self._proposal_generator,
                    scenario=scenario,
                    request=request,
                )
            except ProposalGenerationError as exc:
                if not self._continue_on_generation_failure:
                    raise

                generation_failures.append(
                    SimulationGenerationFailureRecord(
                        scenario_id=scenario.scenario_id,
                        pair_id=scenario.pair_id,
                        family=scenario.family,
                        failure=exc.to_record(
                            request=request,
                            generator_name=(
                                _proposal_generator_name(
                                    self._proposal_generator
                                )
                            ),
                        ),
                    )
                )

                continue

            generated_context = (
                scenario.context.model_copy(
                    update={
                        "declared_plan": list(
                            generation.declared_plan
                        ),
                    },
                    deep=True,
                )
            )

            generated_scenario = scenario.model_copy(
                update={
                    "context": generated_context,
                    "proposal": generation.proposal,
                },
                deep=True,
            )

            for condition in condition_list:
                records.append(
                    self._run_one(
                        scenario=generated_scenario,
                        condition=condition,
                        request=request,
                        generation=generation,
                        evidence_root=evidence_root,
                    )
                )

        attempted_generations = len(
            scenario_list
        )

        failed_generations = len(
            generation_failures
        )

        successful_generations = (
            attempted_generations
            - failed_generations
        )

        generation_completion_rate = (
            successful_generations
            / attempted_generations
        )

        summary = summarize_runs(
            records
        ).model_copy(
            update={
                "attempted_generations": (
                    attempted_generations
                ),
                "successful_generations": (
                    successful_generations
                ),
                "failed_generations": (
                    failed_generations
                ),
                "generation_completion_rate": (
                    generation_completion_rate
                ),
            },
            deep=True,
        )

        artifacts = self._write_artifacts(
            records=records,
            summary=summary,
            output_root=output_root,
        )

        (
            generation_failures_json,
            generation_failures_csv,
        ) = _write_generation_failure_artifacts(
            generation_failures,
            output_root,
        )

        return artifacts.model_copy(
            update={
                "generation_failures": (
                    generation_failures
                ),
                "generation_failures_json": str(
                    generation_failures_json
                ),
                "generation_failures_csv": str(
                    generation_failures_csv
                ),
            },
            deep=True,
        )

    def _run_one(
        self,
        *,
        scenario: ScenarioDefinition,
        condition: GovernanceCondition,
        request: ProposalGenerationRequest,
        generation: ProposalGenerationResult,
        evidence_root: Path,
    ) -> SimulationRunRecord:
        """Run one isolated scenario-condition combination."""

        run_id = (
            f"{scenario.scenario_id}--{condition.value}"
        )

        evidence_path = (
            evidence_root
            / scenario.scenario_id
            / f"{condition.value}.jsonl"
        )

        registry = build_tool_registry(
            scenario.sandbox
        )

        condition_runner = ComparativeConditionRunner(
            registry,
            clock=IncrementingClock(
                start=self._base_time,
            ),
        )

        condition_result = condition_runner.run(
            condition=condition,
            context=scenario.context,
            proposal=scenario.proposal,
            norms=scenario.norms,
            recorder=EvidenceRecorder(
                evidence_path,
                run_id,
                overwrite=True,
            ),
            run_metadata={
                "trial_id": request.trial_id,
                "repetition_index": (
                    request.repetition_index
                ),
                "generation_id": generation.generation_id,
                "generator_name": generation.generator_name,
                "declared_plan": list(
                    generation.declared_plan
                ),
            },
        )

        verification = EvidenceVerifier().verify(
            evidence_path
        )

        completeness = assess_evidence_completeness(
            evidence_path,
            scenario.required_evidence,
        )

        semantic_verification = (
            EvidenceSemanticsVerifier().verify(
                evidence_path
            )
        )

        decision = condition_result.decision

        actual_disposition = (
            decision.disposition
            if decision is not None
            else None
        )

        actual_preservation = (
            decision.preservation
            if decision is not None
            else None
        )

        applied_preservation = (
            actual_preservation
            if actual_preservation is not None
            else Preservation.MINIMAL
        )

        incident_reason: str | None = None

        if applied_preservation is Preservation.INCIDENT:
            if decision is not None and decision.reasons:
                incident_reason = "; ".join(
                    decision.reasons
                )
            else:
                incident_reason = scenario.claim

        preservation_path = (
            evidence_root.parent
            / "preservation"
            / scenario.scenario_id
            / f"{condition.value}.json"
        )

        preservation_bundle = create_preservation_bundle(
            evidence_path,
            preservation_path,
            applied_preservation,
            incident_reason=incident_reason,
            clock=lambda: (
                self._base_time
                + timedelta(days=1)
            ),
            overwrite=True,
        )

        preservation_verification = (
            verify_preservation_bundle(
                preservation_path,
                source_path=evidence_path,
            )
        )

        preservation_matches_ground_truth = (
            applied_preservation
            is scenario.ground_truth.expected_preservation
        )

        decision_matches = (
            actual_disposition
            is scenario.ground_truth.expected_disposition
            if actual_disposition is not None
            else None
        )

        unsafe_execution = (
            scenario.ground_truth.unsafe_if_executed
            and condition_result.execution.executed
        )

        benign_completion = (
            scenario.kind is ScenarioKind.BENIGN
            and condition_result.execution.executed
            and condition_result.operational_outcome == "executed"
        )

        return SimulationRunRecord(
            run_id=run_id,
            scenario_id=scenario.scenario_id,
            pair_id=scenario.pair_id,
            family=scenario.family,
            kind=scenario.kind,
            condition=condition,
            trial_id=request.trial_id,
            repetition_index=request.repetition_index,
            generation_id=generation.generation_id,
            generator_name=generation.generator_name,
            declared_plan=list(
                generation.declared_plan
            ),
            decision_phase=condition_result.decision_phase,
            expected_disposition=(
                scenario.ground_truth.expected_disposition
            ),
            expected_preservation=(
                scenario.ground_truth.expected_preservation
            ),
            actual_disposition=actual_disposition,
            actual_preservation=actual_preservation,
            applied_preservation=applied_preservation,
            preservation_directive_matches_ground_truth=(
                preservation_matches_ground_truth
            ),
            preservation_bundle_valid=(
                preservation_verification.status
                is PreservationVerificationStatus.VALID
            ),
            preservation_bundle_status=(
                preservation_verification.status
            ),
            preservation_payload_mode=(
                preservation_bundle.payload_mode
            ),
            preservation_incident_lock=(
                preservation_bundle.incident_lock
            ),
            preservation_source_match=(
                preservation_verification.source_match
                is True
            ),
            preservation_bundle_path=str(
                preservation_path
            ),
            executed=condition_result.execution.executed,
            side_effect=condition_result.execution.side_effect,
            operational_outcome=(
                condition_result.operational_outcome
            ),
            violation_detected=(
                condition_result.violation_detected
            ),
            execution_prevented=(
                condition_result.execution_prevented
            ),
            unsafe_if_executed=(
                scenario.ground_truth.unsafe_if_executed
            ),
            unsafe_execution=unsafe_execution,
            benign_completion=benign_completion,
            decision_matches_ground_truth=decision_matches,
            evidence_valid=(
                verification.status
                is VerificationStatus.VALID
            ),
            evidence_status=verification.status,
            semantic_evidence_valid=(
                semantic_verification.status
                is SemanticVerificationStatus.VALID
            ),
            semantic_evidence_status=(
                semantic_verification.status
            ),
            semantic_issue_codes=[
                issue.code
                for issue in semantic_verification.issues
            ],
            evidence_completeness=(
                completeness.completeness
            ),
            evidence_required_count=len(
                completeness.required
            ),
            evidence_present_count=len(
                completeness.present
            ),
            evidence_missing=completeness.missing,
            evidence_event_count=(
                condition_result.evidence_event_count
            ),
            evidence_final_hash=(
                condition_result.evidence_final_hash
            ),
            evidence_path=str(evidence_path),
        )

    def _write_artifacts(
        self,
        *,
        records: list[SimulationRunRecord],
        summary: SimulationSummary,
        output_root: Path,
    ) -> SimulationArtifacts:
        """Write JSON and CSV simulation artifacts."""

        output_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        runs_json = output_root / "runs.json"
        runs_csv = output_root / "runs.csv"
        summary_json = output_root / "summary.json"
        summary_csv = output_root / "summary.csv"

        runs_json.write_text(
            json.dumps(
                [
                    record.model_dump(mode="json")
                    for record in records
                ],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        summary_json.write_text(
            json.dumps(
                summary.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        self._write_runs_csv(
            records,
            runs_csv,
        )

        self._write_summary_csv(
            summary,
            summary_csv,
        )

        return SimulationArtifacts(
            runs=records,
            summary=summary,
            runs_json=str(runs_json),
            runs_csv=str(runs_csv),
            summary_json=str(summary_json),
            summary_csv=str(summary_csv),
        )

    @staticmethod
    def _write_runs_csv(
        records: Sequence[SimulationRunRecord],
        path: Path,
    ) -> None:
        """Write one CSV row per scenario-condition run."""

        rows: list[dict[str, object]] = []

        for record in records:
            row = record.model_dump(mode="json")
            row["evidence_missing"] = ";".join(
                record.evidence_missing
            )
            row["semantic_issue_codes"] = ";".join(
                record.semantic_issue_codes
            )
            row["declared_plan"] = ";".join(
                record.declared_plan
            )
            rows.append(row)

        if not rows:
            raise SimulationError(
                "run CSV requires at least one record"
            )

        fieldnames = list(rows[0].keys())

        with path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _write_summary_csv(
        summary: SimulationSummary,
        path: Path,
    ) -> None:
        """Write one CSV row per governance condition."""

        rows = [
            metrics.model_dump(mode="json")
            for _, metrics in sorted(
                summary.conditions.items()
            )
        ]

        if not rows:
            raise SimulationError(
                "summary CSV requires at least one condition"
            )

        fieldnames = list(rows[0].keys())

        with path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    """Run canonical scenarios from the command line."""

    parser = argparse.ArgumentParser(
        description=(
            "Replay evidentiary-governance scenarios "
            "under C0-C3 conditions."
        )
    )

    parser.add_argument(
        "--scenarios",
        default="scenarios/canonical",
        help="Directory containing YAML scenario definitions.",
    )

    parser.add_argument(
        "--output",
        default="results/canonical",
        help="Directory receiving evidence and result artifacts.",
    )

    arguments = parser.parse_args()

    scenarios = load_scenarios(
        arguments.scenarios
    )

    validate_scenario_pairs(scenarios)

    artifacts = SimulationRunner().run(
        scenarios,
        arguments.output,
    )

    print(
        f"completed {artifacts.summary.total_runs} runs "
        f"across {artifacts.summary.scenario_count} scenarios"
    )

    for condition_name, metrics in sorted(
        artifacts.summary.conditions.items()
    ):
        print(
            condition_name,
            f"unsafe_execution_rate="
            f"{metrics.unsafe_execution_rate}",
            f"detection_recall="
            f"{metrics.violation_detection_recall}",
            f"containment="
            f"{metrics.containment_success_rate}",
            f"benign_completion="
            f"{metrics.benign_completion_rate}",
            f"evidence_completeness="
            f"{metrics.mean_evidence_completeness}",
            f"semantic_validity="
            f"{metrics.semantic_evidence_validity_rate}",
            f"preservation_accuracy="
            f"{metrics.preservation_directive_accuracy}",
            f"preservation_bundle_validity="
            f"{metrics.preservation_bundle_validity_rate}",
        )


if __name__ == "__main__":
    main()
