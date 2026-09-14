"""Recalculate the four JURIX paper tables from archived research inputs.

Run from the repository root: python3 reproduce_paper_tables.py
This script reads existing files and does not modify any archived results.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
E2 = Path("artifacts/e2-blind-validation")
V2 = E2 / "clarified-spec-v2"
CONDITIONS = (
    "c0_logging",
    "c1_post_hoc",
    "c2_action_level",
    "c3_organizational",
)
MODEL_FILES = {
    "Qwen 3.5 9B": (
        E2 / "qwen35_structured_all_41.csv",
        V2 / "qwen35_v2_batch_all_41.csv",
        V2 / "qwen35_v2_clarified_all_12.csv",
    ),
    "Gemma 4 12B": (
        E2 / "gemma4_12b_structured_all_41.csv",
        V2 / "gemma4_v2_batch_all_41.csv",
        V2 / "gemma4_v2_clarified_all_12.csv",
    ),
    "Llama 3.1 8B": (
        E2 / "llama31_8b_structured_all_41.csv",
        V2 / "llama31_v2_batch_all_41.csv",
        V2 / "llama31_v2_clarified_all_12.csv",
    ),
    "Phi-4 14B": (
        E2 / "phi4_14b_structured_all_41.csv",
        V2 / "phi4_v2_batch_all_41.csv",
        V2 / "phi4_v2_clarified_all_12.csv",
    ),
    "Microsoft 365 Copilot": (
        E2 / "copilot_all_41.csv",
        E2 / "copilot_batch_v2_all_41.csv",
        V2 / "copilot_v2_clarified_all_12.csv",
    ),
    "Gemini 3.6 Flash": (
        E2 / "gemini_3.6_flash_all_41.csv",
        V2 / "gemini_v2_batch_all_41.csv",
        V2 / "gemini_v2_clarified_all_12.csv",
    ),
}


def rows(path: Path) -> list[dict[str, str]]:
    with (ROOT / path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def labels(path: Path, expected_ids: set[str]) -> dict[str, str]:
    observed = rows(path)
    result = {r["blind_id"].strip(): r["compliance"].strip() for r in observed}
    assert len(observed) == len(result) == len(expected_ids), path
    assert set(result) == expected_ids, path
    assert set(result.values()) <= {"COMPLIANT", "NONCOMPLIANT", "UNCERTAIN"}
    return result


def true(value: str) -> bool:
    assert value.lower() in {"true", "false"}, value
    return value.lower() == "true"


def table_1() -> None:
    source = Path("results/e1-governance-comparison-2026-07-27")
    metrics = (
        ("Benign completion", "benign_completion_rate"),
        ("Unsafe execution", "unsafe_execution_rate"),
        ("Detection", "violation_detection_recall"),
        ("Containment", "containment_success_rate"),
        ("False positives", "false_positive_rate"),
        ("Evidence completeness", "mean_evidence_completeness"),
        ("Preservation accuracy", "preservation_directive_accuracy"),
    )
    repeats = [
        {r["condition"]: r for r in rows(source / f"repetition-{i:02d}/summary.csv")}
        for i in range(1, 6)
    ]
    assert all(set(repeat) == set(CONDITIONS) for repeat in repeats)
    assert all(int(repeat[c]["runs"]) == 16 for repeat in repeats for c in CONDITIONS)
    print("Table 1: E1 aggregate results (%)")
    for title, field in metrics:
        by_condition = []
        for condition in CONDITIONS:
            values = [float(repeat[condition][field]) for repeat in repeats]
            assert max(values) - min(values) < 1e-12, (condition, field)
            by_condition.append(f"{100 * sum(values) / len(values):.1f}")
        print(f"  {title:24} {'  '.join(by_condition)}")
    print("  E1 runs: 16 scenarios x 4 conditions x 5 repetitions = 320\n")


def table_2(key: list[dict[str, str]]) -> None:
    by_id = {r["generation_id"]: r for r in key}
    assert len(by_id) == len(key) == 41
    assert Counter(r["runtime_proposal_status"] for r in key) == {
        "noncompliant": 24,
        "compliant": 17,
    }
    assert Counter(r["alignment"] for r in key) == {
        "aligned_benign": 12,
        "aligned_unsafe": 15,
        "model_induced_violation": 9,
        "model_avoided_violation": 5,
    }
    source = Path("artifacts/e2-llm-main")
    runs = [
        r
        for i in range(1, 4)
        for r in rows(source / f"repetition-{i:02d}/runs.csv")
    ]
    failures = sum(
        len(rows(source / f"repetition-{i:02d}/generation-failures.csv"))
        for i in range(1, 4)
    )
    assert len(runs) == 164 and failures == 7
    assert {(r["generation_id"], r["condition"]) for r in runs} == {
        (generation_id, condition) for generation_id in by_id for condition in CONDITIONS
    }
    summary: dict[str, list[int]] = {}
    for condition in CONDITIONS:
        selected = [r for r in runs if r["condition"] == condition]
        compliant = [r for r in selected if by_id[r["generation_id"]]["runtime_proposal_status"] == "compliant"]
        noncompliant = [r for r in selected if by_id[r["generation_id"]]["runtime_proposal_status"] == "noncompliant"]
        summary[condition] = [
            sum(true(r["executed"]) for r in compliant),
            sum(true(r["executed"]) for r in noncompliant),
            sum(true(r["violation_detected"]) for r in noncompliant),
            sum(true(r["execution_prevented"]) for r in noncompliant),
        ]
    assert list(summary.values()) == [
        [17, 24, 0, 0],
        [17, 24, 24, 0],
        [17, 20, 4, 4],
        [17, 0, 24, 24],
    ]
    print("Table 2: E2 counts in C0, C1, C2, C3")
    for index, title in enumerate(("Compliant executed", "Noncompliant executed", "Noncompliant detected", "Noncompliant prevented")):
        print(f"  {title:24} {'  '.join(str(summary[c][index]) for c in CONDITIONS)}")
    print(f"  Generation attempts: {len(by_id) + failures}; executable: {len(by_id)}; failures: {failures}\n")


def table_3_and_4(key: list[dict[str, str]]) -> None:
    original_ids = {r["blind_id"] for r in key}
    by_original_id = {r["blind_id"]: r["runtime_proposal_status"].upper() for r in key}
    match_key = rows(V2 / "researcher_v2_key.csv")
    assert len(match_key) == 12
    assert len({r["original_blind_id"] for r in match_key}) == 12
    clarified_ids = {r["clarified_blind_id"] for r in match_key}
    assert len(clarified_ids) == 12
    assert {r["original_blind_id"] for r in match_key} <= original_ids
    by_clarified_id = {
        r["clarified_blind_id"]: by_original_id[r["original_blind_id"]]
        for r in match_key
    }
    assert Counter(by_clarified_id.values()) == {"NONCOMPLIANT": 10, "COMPLIANT": 2}
    sums_41 = [0, 0]
    sums_12 = [0, 0, 0]
    v2_votes: dict[str, list[str]] = {blind_id: [] for blind_id in original_ids}
    print("Table 3: Agreement with runtime-derived labels (A, B)")
    for model, paths in MODEL_FILES.items():
        a = labels(paths[0], original_ids)
        b = labels(paths[1], original_ids)
        c = labels(paths[2], clarified_ids)
        counts_41 = [
            sum(judgments[bid] == by_original_id[bid] for bid in original_ids)
            for judgments in (a, b)
        ]
        counts_12 = [
            sum(a[r["original_blind_id"]] == by_clarified_id[r["clarified_blind_id"]] for r in match_key),
            sum(b[r["original_blind_id"]] == by_clarified_id[r["clarified_blind_id"]] for r in match_key),
            sum(c[bid] == by_clarified_id[bid] for bid in clarified_ids),
        ]
        for i, count in enumerate(counts_41):
            sums_41[i] += count
        for i, count in enumerate(counts_12):
            sums_12[i] += count
        for bid, judgment in b.items():
            v2_votes[bid].append(judgment)
        print(f"  {model:24} {counts_41[0]:2}/41  {counts_41[1]:2}/41     matched 12: {counts_12[0]:2}/12  {counts_12[1]:2}/12  {counts_12[2]:2}/12")
    ties = sum(
        list(Counter(votes).values()).count(max(Counter(votes).values())) > 1
        for votes in v2_votes.values()
    )
    assert sums_41 == [175, 167], sums_41
    assert sums_12 == [35, 36, 67], sums_12
    assert ties == 6, ties
    print(f"  Aggregate 41: {sums_41[0]}/246 (A), {sums_41[1]}/246 (B)")
    print(f"  Table 4 aggregate 12: {sums_12[0]}/72 (I), {sums_12[1]}/72 (II), {sums_12[2]}/72 (III)")
    print(f"  Tied six-annotator top label under B: {ties}/41\n")


def validate_input_hashes() -> None:
    manifest = json.loads((ROOT / E2 / "agreement-6models-v2-unblind/unblind_input_manifest.json").read_text(encoding="utf-8"))
    entries = [manifest["researcher_key"], *manifest["model_files"].values()]
    for entry in entries:
        path = ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], path
    print(f"Input SHA-256 checks: {len(entries)} passed")


def main() -> None:
    key = rows(E2 / "researcher_blind_key.csv")
    table_1()
    table_2(key)
    table_3_and_4(key)
    validate_input_hashes()
    print("All paper-table checks passed.")


if __name__ == "__main__":
    main()
