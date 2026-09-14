from __future__ import annotations

from pathlib import Path

from eg_runtime.retention_export_experiment import (
    RetentionExportAttackSignal,
    run_retention_export_experiment,
)


REGISTRY_KEY = (
    b"export-experiment-registry-key-2026"
)

WITNESS_KEY = (
    b"export-experiment-witness-key-2026"
)


def run_experiment(
    tmp_path: Path,
):
    output = (
        tmp_path
        / "retention-export-integrity"
    )

    summary = run_retention_export_experiment(
        output,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    return output, summary


def find_attack(
    summary,
    attack_id: str,
):
    matches = [
        attack
        for attack in summary.attacks
        if attack.attack_id == attack_id
    ]

    assert len(matches) == 1

    return matches[0]


def test_valid_baseline_and_all_attacks_are_detected(
    tmp_path: Path,
) -> None:
    output, summary = run_experiment(
        tmp_path
    )

    assert summary.baseline_valid is True

    assert summary.attack_count == 6
    assert summary.detected_attacks == 6
    assert summary.attack_detection_rate == 1.0

    assert (
        summary.correctly_localized_attacks
        == 6
    )

    assert summary.localization_accuracy == 1.0

    assert (output / "summary.json").exists()


def test_attack_signal_matrix_is_correct(
    tmp_path: Path,
) -> None:
    _, summary = run_experiment(
        tmp_path
    )

    expected = {
        "exported_artifact_tampering": (
            RetentionExportAttackSignal
            .ARTIFACT_HASH
        ),
        "exported_artifact_deletion": (
            RetentionExportAttackSignal
            .ARTIFACT_SET
        ),
        "restricted_artifact_injection": (
            RetentionExportAttackSignal
            .ARTIFACT_SET
        ),
        "export_manifest_tampering": (
            RetentionExportAttackSignal
            .EXPORT_MANIFEST
        ),
        "inventory_provenance_tampering": (
            RetentionExportAttackSignal
            .PROVENANCE
        ),
        "retention_provenance_tampering": (
            RetentionExportAttackSignal
            .PROVENANCE
        ),
    }

    for attack_id, signal in expected.items():
        attack = find_attack(
            summary,
            attack_id,
        )

        assert signal in attack.actual_signals
        assert attack.correctly_localized is True


def test_restricted_artifact_injection_is_rejected(
    tmp_path: Path,
) -> None:
    _, summary = run_experiment(
        tmp_path
    )

    attack = find_attack(
        summary,
        "restricted_artifact_injection",
    )

    assert attack.export_valid is False
    assert attack.artifact_set_valid is False
    assert attack.manifest_hash_valid is True
    assert attack.provenance_valid is True

    assert any(
        issue.startswith(
            "unexpected_export_artifact:"
        )
        for issue in attack.issues
    )


def test_provenance_changes_are_distinguished(
    tmp_path: Path,
) -> None:
    _, summary = run_experiment(
        tmp_path
    )

    for attack_id in [
        "inventory_provenance_tampering",
        "retention_provenance_tampering",
    ]:
        attack = find_attack(
            summary,
            attack_id,
        )

        assert attack.export_valid is False
        assert attack.manifest_hash_valid is True
        assert attack.artifact_set_valid is True
        assert attack.artifact_hashes_valid is True
        assert attack.provenance_valid is False

        assert (
            RetentionExportAttackSignal.PROVENANCE
            in attack.actual_signals
        )


def test_export_attack_experiment_is_reproducible(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_summary = (
        run_retention_export_experiment(
            first,
            registry_key=REGISTRY_KEY,
            witness_key=WITNESS_KEY,
        )
    )

    second_summary = (
        run_retention_export_experiment(
            second,
            registry_key=REGISTRY_KEY,
            witness_key=WITNESS_KEY,
        )
    )

    assert (
        first_summary.model_dump(mode="json")
        == second_summary.model_dump(mode="json")
    )

    assert (
        (first / "summary.json").read_bytes()
        == (second / "summary.json").read_bytes()
    )
