from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from eg_runtime.approval_registry import (
    ApprovalRegistryVerificationStatus,
    register_approval_package,
    verify_approval_registry,
)
from eg_runtime.approval_simulation import (
    run_approval_workflow_simulation,
)
from eg_runtime.approval_witness import (
    ApprovalWitnessError,
    ApprovalWitnessVerificationStatus,
    create_approval_witness_receipt,
    verify_approval_witness_receipt,
)


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    30,
    tzinfo=UTC,
)

REGISTRY_KEY = (
    b"approval-registry-test-key-2026"
)

WITNESS_KEY = (
    b"independent-witness-test-key-2026"
)


def create_registry(
    tmp_path: Path,
):
    package_root = tmp_path

    workflow_root = (
        package_root / "approval-workflows"
    )

    summary = run_approval_workflow_simulation(
        workflow_root
    )

    registry_path = (
        package_root
        / "approval-registry"
        / "registry.jsonl"
    )

    checkpoint_path = (
        package_root
        / "approval-registry"
        / "checkpoint.json"
    )

    for offset, record in enumerate(
        summary.records
    ):
        package_dir = (
            workflow_root
            / record.package_relative_path
        )

        register_approval_package(
            registry_path=registry_path,
            checkpoint_path=checkpoint_path,
            package_root=package_root,
            package_dir=package_dir,
            secret_key=REGISTRY_KEY,
            recorded_at=(
                BASE_TIME
                + timedelta(seconds=offset)
            ),
        )

    return (
        package_root,
        workflow_root,
        summary,
        registry_path,
        checkpoint_path,
    )


def create_receipt(
    tmp_path: Path,
):
    (
        package_root,
        workflow_root,
        summary,
        registry_path,
        checkpoint_path,
    ) = create_registry(tmp_path)

    receipt_path = (
        package_root
        / "approval-witness"
        / "receipt.json"
    )

    create_approval_witness_receipt(
        output_path=receipt_path,
        registry_path=registry_path,
        checkpoint_path=checkpoint_path,
        package_root=package_root,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
        witness_id="independent-witness-01",
        observed_at=BASE_TIME + timedelta(
            minutes=1
        ),
    )

    return (
        package_root,
        workflow_root,
        summary,
        registry_path,
        checkpoint_path,
        receipt_path,
    )


def test_valid_registry_snapshot_is_witnessed(
    tmp_path: Path,
) -> None:
    (
        package_root,
        _,
        _,
        registry_path,
        checkpoint_path,
        receipt_path,
    ) = create_receipt(tmp_path)

    result = verify_approval_witness_receipt(
        receipt_path=receipt_path,
        registry_path=registry_path,
        checkpoint_path=checkpoint_path,
        package_root=package_root,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    assert (
        result.status
        is ApprovalWitnessVerificationStatus.VALID
    )

    assert result.issues == []
    assert result.receipt_id_valid is True
    assert result.record_hash_valid is True
    assert result.witness_signature_valid is True
    assert result.registry_valid is True
    assert result.registry_snapshot_match is True

    assert (
        result.checkpoint_snapshot_match
        is True
    )


def test_wrong_witness_key_is_detected(
    tmp_path: Path,
) -> None:
    (
        package_root,
        _,
        _,
        registry_path,
        checkpoint_path,
        receipt_path,
    ) = create_receipt(tmp_path)

    result = verify_approval_witness_receipt(
        receipt_path=receipt_path,
        registry_path=registry_path,
        checkpoint_path=checkpoint_path,
        package_root=package_root,
        registry_key=REGISTRY_KEY,
        witness_key=b"wrong-independent-witness-key",
    )

    assert (
        result.status
        is ApprovalWitnessVerificationStatus.INVALID
    )

    assert (
        result.witness_signature_valid
        is False
    )

    assert (
        "witness_signature_mismatch"
        in result.issues
    )


def test_witness_receipt_tampering_is_detected(
    tmp_path: Path,
) -> None:
    (
        package_root,
        _,
        _,
        registry_path,
        checkpoint_path,
        receipt_path,
    ) = create_receipt(tmp_path)

    raw = json.loads(
        receipt_path.read_text(
            encoding="utf-8"
        )
    )

    raw["witness_id"] = "altered-witness"

    receipt_path.write_text(
        json.dumps(
            raw,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = verify_approval_witness_receipt(
        receipt_path=receipt_path,
        registry_path=registry_path,
        checkpoint_path=checkpoint_path,
        package_root=package_root,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    assert (
        result.status
        is ApprovalWitnessVerificationStatus.INVALID
    )

    assert result.receipt_id_valid is False
    assert result.record_hash_valid is False

    assert (
        "receipt_id_mismatch"
        in result.issues
    )

    assert (
        "receipt_record_hash_mismatch"
        in result.issues
    )


def test_witness_detects_valid_registry_rollback(
    tmp_path: Path,
) -> None:
    (
        package_root,
        workflow_root,
        summary,
        registry_path,
        checkpoint_path,
        receipt_path,
    ) = create_receipt(tmp_path)

    alternate_registry = (
        package_root
        / "alternate-registry"
        / "registry.jsonl"
    )

    alternate_checkpoint = (
        package_root
        / "alternate-registry"
        / "checkpoint.json"
    )

    first_record = summary.records[0]

    first_package = (
        workflow_root
        / first_record.package_relative_path
    )

    register_approval_package(
        registry_path=alternate_registry,
        checkpoint_path=alternate_checkpoint,
        package_root=package_root,
        package_dir=first_package,
        secret_key=REGISTRY_KEY,
        recorded_at=BASE_TIME,
    )

    registry_path.write_bytes(
        alternate_registry.read_bytes()
    )

    checkpoint_path.write_bytes(
        alternate_checkpoint.read_bytes()
    )

    rewritten_registry = (
        verify_approval_registry(
            registry_path=registry_path,
            checkpoint_path=checkpoint_path,
            package_root=package_root,
            secret_key=REGISTRY_KEY,
        )
    )

    assert (
        rewritten_registry.status
        is ApprovalRegistryVerificationStatus.VALID
    )

    result = verify_approval_witness_receipt(
        receipt_path=receipt_path,
        registry_path=registry_path,
        checkpoint_path=checkpoint_path,
        package_root=package_root,
        registry_key=REGISTRY_KEY,
        witness_key=WITNESS_KEY,
    )

    assert (
        result.status
        is ApprovalWitnessVerificationStatus.INVALID
    )

    assert result.registry_valid is True

    assert (
        result.registry_snapshot_match
        is False
    )

    assert (
        result.checkpoint_snapshot_match
        is False
    )

    assert (
        "registry_sha256_mismatch"
        in result.issues
    )

    assert (
        "checkpoint_sha256_mismatch"
        in result.issues
    )

    assert (
        "entry_count_mismatch"
        in result.issues
    )


def test_invalid_registry_cannot_be_witnessed(
    tmp_path: Path,
) -> None:
    (
        package_root,
        _,
        _,
        registry_path,
        checkpoint_path,
    ) = create_registry(tmp_path)

    lines = registry_path.read_text(
        encoding="utf-8"
    ).splitlines()

    first = json.loads(lines[0])

    first["recorded_at"] = (
        "2026-07-22T12:30:00Z"
    )

    lines[0] = json.dumps(
        first,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    registry_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ApprovalWitnessError,
        match="registry is invalid",
    ):
        create_approval_witness_receipt(
            output_path=(
                package_root
                / "approval-witness"
                / "invalid.json"
            ),
            registry_path=registry_path,
            checkpoint_path=checkpoint_path,
            package_root=package_root,
            registry_key=REGISTRY_KEY,
            witness_key=WITNESS_KEY,
            witness_id=(
                "independent-witness-01"
            ),
            observed_at=BASE_TIME + timedelta(
                minutes=1
            ),
        )
