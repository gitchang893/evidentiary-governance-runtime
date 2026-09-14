from __future__ import annotations

import json
from pathlib import Path

from eg_runtime.approval_integrity_experiment import (
    DetectionLayer,
    run_approval_integrity_experiment,
)


REGISTRY_KEY = (
    b"integrity-registry-test-key-2026"
)

WITNESS_KEY = (
    b"integrity-witness-test-key-2026"
)


def run_experiment(
    tmp_path: Path,
):
    output = tmp_path / "approval-integrity"

    summary = run_approval_integrity_experiment(
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
    assert summary.baseline_packages_valid is True
    assert summary.baseline_registry_valid is True
    assert summary.baseline_witness_valid is True

    assert summary.attack_count == 6
    assert summary.detected_attacks == 6
    assert summary.attack_detection_rate == 1.0

    assert (
        summary.correctly_localized_attacks
        == 6
    )

    assert summary.localization_accuracy == 1.0

    assert (output / "summary.json").exists()


def test_detection_layer_matrix_is_correct(
    tmp_path: Path,
) -> None:
    _, summary = run_experiment(
        tmp_path
    )

    expected = {
        "resolution_tampering": (
            DetectionLayer.PACKAGE
        ),
        "internally_rehashed_manifest": (
            DetectionLayer.REGISTRY
        ),
        "registry_entry_tampering": (
            DetectionLayer.REGISTRY
        ),
        "registry_tail_deletion": (
            DetectionLayer.REGISTRY
        ),
        "valid_registry_rollback": (
            DetectionLayer.WITNESS
        ),
        "witness_receipt_tampering": (
            DetectionLayer.WITNESS
        ),
    }

    actual = {
        attack.attack_id: (
            attack.actual_first_detection
        )
        for attack in summary.attacks
    }

    assert actual == expected


def test_internally_rehashed_package_requires_registry(
    tmp_path: Path,
) -> None:
    _, summary = run_experiment(
        tmp_path
    )

    attack = find_attack(
        summary,
        "internally_rehashed_manifest",
    )

    assert attack.package_valid is True
    assert attack.registry_valid is False
    assert attack.witness_valid is False

    assert (
        attack.actual_first_detection
        is DetectionLayer.REGISTRY
    )

    assert any(
        issue.startswith(
            "package_manifest_hash_mismatch:"
        )
        for issue in attack.registry_issues
    )


def test_valid_registry_rollback_requires_witness(
    tmp_path: Path,
) -> None:
    _, summary = run_experiment(
        tmp_path
    )

    attack = find_attack(
        summary,
        "valid_registry_rollback",
    )

    assert attack.package_valid is True
    assert attack.registry_valid is True
    assert attack.witness_valid is False

    assert (
        attack.actual_first_detection
        is DetectionLayer.WITNESS
    )

    assert (
        "registry_sha256_mismatch"
        in attack.witness_issues
    )

    assert (
        "checkpoint_sha256_mismatch"
        in attack.witness_issues
    )


def test_integrity_experiment_is_reproducible(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_summary = run_approval_integrity_experiment(
        first,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    second_summary = run_approval_integrity_experiment(
        second,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    assert (
        first_summary.model_dump(mode="json")
        == second_summary.model_dump(mode="json")
    )

    assert (
        (first / "summary.json").read_bytes()
        == (second / "summary.json").read_bytes()
    )

    stored = json.loads(
        (
            first / "summary.json"
        ).read_text(encoding="utf-8")
    )

    assert stored["attack_count"] == 6
    assert stored["attack_detection_rate"] == 1.0
    assert stored["localization_accuracy"] == 1.0
