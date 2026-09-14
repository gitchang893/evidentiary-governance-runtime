from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from eg_runtime.approval_package import (
    ApprovalPackageVerificationStatus,
    verify_approval_artifact_package,
)
from eg_runtime.approval_registry import (
    ApprovalRegistryVerificationStatus,
    register_approval_package,
    verify_approval_registry,
)
from eg_runtime.approval_simulation import (
    run_approval_workflow_simulation,
)


BASE_TIME = datetime(
    2026,
    7,
    22,
    10,
    20,
    tzinfo=UTC,
)

SECRET_KEY = (
    b"approval-registry-test-key-2026"
)


def _canonical_json(
    value,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
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
            secret_key=SECRET_KEY,
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


def test_authenticated_registry_accepts_valid_packages(
    tmp_path: Path,
) -> None:
    (
        package_root,
        _,
        _,
        registry_path,
        checkpoint_path,
    ) = create_registry(tmp_path)

    result = verify_approval_registry(
        registry_path=registry_path,
        checkpoint_path=checkpoint_path,
        package_root=package_root,
        secret_key=SECRET_KEY,
    )

    assert (
        result.status
        is ApprovalRegistryVerificationStatus.VALID
    )

    assert result.issues == []
    assert result.entry_count == 2
    assert result.valid_entries == 2
    assert result.chain_valid is True
    assert result.signatures_valid is True
    assert result.checkpoint_valid is True
    assert result.packages_valid is True


def test_wrong_registry_key_is_detected(
    tmp_path: Path,
) -> None:
    (
        package_root,
        _,
        _,
        registry_path,
        checkpoint_path,
    ) = create_registry(tmp_path)

    result = verify_approval_registry(
        registry_path=registry_path,
        checkpoint_path=checkpoint_path,
        package_root=package_root,
        secret_key=b"incorrect-registry-key",
    )

    assert (
        result.status
        is ApprovalRegistryVerificationStatus.INVALID
    )

    assert result.signatures_valid is False

    assert any(
        issue.startswith(
            "entry_signature_mismatch:"
        )
        for issue in result.issues
    )

    assert (
        "checkpoint_signature_mismatch"
        in result.issues
    )


def test_registry_entry_tampering_is_detected(
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
        "2026-07-22T11:20:00Z"
    )

    lines[0] = _canonical_json(first)

    registry_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    result = verify_approval_registry(
        registry_path=registry_path,
        checkpoint_path=checkpoint_path,
        package_root=package_root,
        secret_key=SECRET_KEY,
    )

    assert (
        result.status
        is ApprovalRegistryVerificationStatus.INVALID
    )

    assert (
        "entry_record_hash_mismatch:0"
        in result.issues
    )

    assert (
        "checkpoint_registry_sha256_mismatch"
        in result.issues
    )


def test_registry_tail_deletion_is_detected(
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

    assert len(lines) == 2

    registry_path.write_text(
        lines[0] + "\n",
        encoding="utf-8",
    )

    result = verify_approval_registry(
        registry_path=registry_path,
        checkpoint_path=checkpoint_path,
        package_root=package_root,
        secret_key=SECRET_KEY,
    )

    assert (
        result.status
        is ApprovalRegistryVerificationStatus.INVALID
    )

    assert (
        "checkpoint_entry_count_mismatch"
        in result.issues
    )

    assert (
        "checkpoint_final_hash_mismatch"
        in result.issues
    )

    assert (
        "checkpoint_registry_sha256_mismatch"
        in result.issues
    )


def test_registry_detects_internally_rehashed_package(
    tmp_path: Path,
) -> None:
    (
        package_root,
        workflow_root,
        summary,
        registry_path,
        checkpoint_path,
    ) = create_registry(tmp_path)

    approved = next(
        record
        for record in summary.records
        if record.resolution_status.value == "approved"
    )

    package_dir = (
        workflow_root
        / approved.package_relative_path
    )

    manifest_path = (
        package_dir / "manifest.json"
    )

    raw = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    raw["created_at"] = (
        "2026-07-22T10:11:00Z"
    )

    content = {
        key: value
        for key, value in raw.items()
        if key != "manifest_hash"
    }

    raw["manifest_hash"] = hashlib.sha256(
        _canonical_json(content).encode(
            "utf-8"
        )
    ).hexdigest()

    manifest_path.write_text(
        json.dumps(
            raw,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    internal_verification = (
        verify_approval_artifact_package(
            package_dir
        )
    )

    assert (
        internal_verification.status
        is ApprovalPackageVerificationStatus.VALID
    )

    registry_verification = (
        verify_approval_registry(
            registry_path=registry_path,
            checkpoint_path=checkpoint_path,
            package_root=package_root,
            secret_key=SECRET_KEY,
        )
    )

    assert (
        registry_verification.status
        is ApprovalRegistryVerificationStatus.INVALID
    )

    registered_relative_path = (
        Path("approval-workflows")
        / approved.package_relative_path
    ).as_posix()

    expected_prefix = (
        "package_manifest_hash_mismatch:"
        + registered_relative_path
    )

    assert (
        expected_prefix
        in registry_verification.issues
    )

    expected_sha_issue = (
        "package_manifest_sha256_mismatch:"
        + registered_relative_path
    )

    assert (
        expected_sha_issue
        in registry_verification.issues
    )
