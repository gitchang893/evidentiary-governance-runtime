# scripts/analyze_unblind_model_agreement.py

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "e2-blind-validation"
OUT = BASE / "agreement-6models-unblind"

KEY_FILE = BASE / "researcher_blind_key.csv"

MODELS = {
    "Qwen3.5-9B": BASE / "qwen35_structured_all_41.csv",
    "Gemma4-12B": BASE / "gemma4_12b_structured_all_41.csv",
    "Llama3.1-8B": BASE / "llama31_8b_structured_all_41.csv",
    "Phi4-14B": BASE / "phi4_14b_structured_all_41.csv",
    "Microsoft365-Copilot": BASE / "copilot_all_41.csv",
    "Gemini3.6-Flash": BASE / "gemini_3.6_flash_all_41.csv",
}

LABELS = [
    "COMPLIANT",
    "NONCOMPLIANT",
    "UNCERTAIN",
]

EXPECTED_CASES = 41


def clean(value: Any) -> str:
    return str(value or "").strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_jsonish(value: Any, default: Any) -> Any:
    text = clean(value)

    if not text:
        return default

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def parse_norm_labels(value: Any) -> frozenset[str]:
    return frozenset(
        re.findall(
            r"\bN\d+\b",
            clean(value).upper(),
        )
    )


def normalize_runtime_label(value: Any) -> str:
    text = clean(value).upper()
    compact = re.sub(r"[^A-Z]", "", text)

    # NONCOMPLIANT must be checked first because it
    # contains the string COMPLIANT.
    if compact in {
        "NONCOMPLIANT",
        "NORMNONCOMPLIANT",
        "VIOLATION",
    }:
        return "NONCOMPLIANT"

    if compact in {
        "COMPLIANT",
        "NORMCOMPLIANT",
    }:
        return "COMPLIANT"

    raise RuntimeError(
        f"Unknown runtime proposal status: {value!r}"
    )


def load_model(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"Missing: {path}")

    with path.open(
        encoding="utf-8",
        newline="",
    ) as f:
        rows = list(csv.DictReader(f))

    if len(rows) != EXPECTED_CASES:
        raise RuntimeError(
            f"{path.name}: expected {EXPECTED_CASES} rows, "
            f"found {len(rows)}"
        )

    result = {}

    for row in rows:
        blind_id = clean(row.get("blind_id"))
        compliance = clean(row.get("compliance"))

        if compliance not in LABELS:
            raise RuntimeError(
                f"{path.name}: bad compliance "
                f"{compliance!r} for {blind_id}"
            )

        if blind_id in result:
            raise RuntimeError(
                f"{path.name}: duplicate {blind_id}"
            )

        result[blind_id] = {
            "compliance": compliance,
            "norms": parse_norm_labels(
                row.get("violated_norm_labels")
            ),
            "confidence": clean(
                row.get("confidence")
            ),
            "reason": clean(
                row.get("brief_reason")
            ),
        }

    return result


def load_key() -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not KEY_FILE.exists():
        raise RuntimeError(
            f"Missing researcher key: {KEY_FILE}"
        )

    with KEY_FILE.open(
        encoding="utf-8",
        newline="",
    ) as f:
        rows = list(csv.DictReader(f))

    if len(rows) != EXPECTED_CASES:
        raise RuntimeError(
            f"researcher key: expected {EXPECTED_CASES}, "
            f"found {len(rows)}"
        )

    order = []
    result = {}

    for row in rows:
        blind_id = clean(row.get("blind_id"))

        if not blind_id:
            raise RuntimeError(
                "Empty blind_id in researcher key"
            )

        if blind_id in result:
            raise RuntimeError(
                f"Duplicate blind_id in researcher key: "
                f"{blind_id}"
            )

        runtime_label = normalize_runtime_label(
            row.get("runtime_proposal_status")
        )

        failed_norm_ids = parse_jsonish(
            row.get("failed_norm_ids"),
            [],
        )

        failed_predicate_types = parse_jsonish(
            row.get("failed_predicate_types"),
            [],
        )

        norm_label_map = parse_jsonish(
            row.get("norm_label_map"),
            {},
        )

        if not isinstance(failed_norm_ids, list):
            raise RuntimeError(
                f"{blind_id}: failed_norm_ids not list"
            )

        if not isinstance(
            failed_predicate_types,
            list,
        ):
            raise RuntimeError(
                f"{blind_id}: failed_predicate_types "
                "not list"
            )

        if not isinstance(norm_label_map, dict):
            raise RuntimeError(
                f"{blind_id}: norm_label_map not dict"
            )

        #
        # Translate runtime norm IDs back into the blind
        # N1/N2/... labels seen by annotators.
        #
        inverse_map = {
            clean(norm_id): clean(blind_label)
            for blind_label, norm_id
            in norm_label_map.items()
        }

        runtime_blind_norms = set()

        for norm_id in failed_norm_ids:
            norm_id = clean(norm_id)

            if norm_id not in inverse_map:
                raise RuntimeError(
                    f"{blind_id}: runtime failed norm "
                    f"{norm_id!r} has no blind-label mapping"
                )

            runtime_blind_norms.add(
                inverse_map[norm_id]
            )

        result[blind_id] = {
            "scenario_id":
                clean(row.get("scenario_id")),

            "repetition_index":
                clean(row.get("repetition_index")),

            "generation_id":
                clean(row.get("generation_id")),

            "family":
                clean(row.get("family")),

            "scenario_kind":
                clean(row.get("scenario_kind")),

            "alignment":
                clean(row.get("alignment")),

            "runtime_label":
                runtime_label,

            "runtime_norms":
                frozenset(runtime_blind_norms),

            "failed_norm_ids":
                tuple(map(clean, failed_norm_ids)),

            "failed_predicate_types":
                tuple(
                    map(
                        clean,
                        failed_predicate_types,
                    )
                ),

            "runtime_reasons":
                parse_jsonish(
                    row.get("runtime_reasons"),
                    [],
                ),
        }

        order.append(blind_id)

    return order, result


def raw_agreement(
    a: list[str],
    b: list[str],
) -> float:
    return sum(
        x == y
        for x, y in zip(a, b)
    ) / len(a)


def cohen_kappa(
    a: list[str],
    b: list[str],
) -> float:
    n = len(a)

    observed = raw_agreement(a, b)

    ca = Counter(a)
    cb = Counter(b)

    expected = sum(
        (ca[label] / n)
        * (cb[label] / n)
        for label in LABELS
    )

    if expected == 1.0:
        return (
            1.0
            if observed == 1.0
            else float("nan")
        )

    return (
        observed - expected
    ) / (
        1.0 - expected
    )


def jaccard(
    a: frozenset[str],
    b: frozenset[str],
) -> float:
    if not a and not b:
        return 1.0

    return len(a & b) / len(a | b)


def majority_label(
    labels: list[str],
) -> str:
    counts = Counter(labels)
    top = max(counts.values())

    winners = [
        label
        for label, count in counts.items()
        if count == top
    ]

    if len(winners) != 1:
        return "TIE"

    return winners[0]


def write_csv(
    path: Path,
    fields: list[str],
    rows: list[dict[str, Any]],
) -> None:
    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(rows)


def summarize_group(
    group_name: str,
    group_value: str,
    ids: list[str],
    key: dict[str, dict],
    models: dict[str, dict],
) -> list[dict[str, Any]]:
    rows = []

    for model, data in models.items():
        runtime = [
            key[i]["runtime_label"]
            for i in ids
        ]

        predicted = [
            data[i]["compliance"]
            for i in ids
        ]

        agreements = sum(
            a == b
            for a, b in zip(runtime, predicted)
        )

        rows.append(
            {
                "group_type": group_name,
                "group_value": group_value,
                "model": model,
                "n": len(ids),
                "runtime_compliant_n":
                    runtime.count("COMPLIANT"),
                "runtime_noncompliant_n":
                    runtime.count("NONCOMPLIANT"),
                "model_compliant_n":
                    predicted.count("COMPLIANT"),
                "model_noncompliant_n":
                    predicted.count("NONCOMPLIANT"),
                "model_uncertain_n":
                    predicted.count("UNCERTAIN"),
                "agreement_n":
                    agreements,
                "agreement_rate":
                    agreements / len(ids),
            }
        )

    return rows


def main() -> None:
    global OUT, MODELS
    parser = argparse.ArgumentParser(description="Compare blinded LLM annotations with runtime labels.")
    parser.add_argument("--version", choices=("v1", "v2"), default="v1")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.version == "v2":
        revised = BASE / "clarified-spec-v2"
        MODELS = {
            "Qwen3.5-9B": revised / "qwen35_v2_batch_all_41.csv",
            "Gemma4-12B": revised / "gemma4_v2_batch_all_41.csv",
            "Llama3.1-8B": revised / "llama31_v2_batch_all_41.csv",
            "Phi4-14B": revised / "phi4_v2_batch_all_41.csv",
            "Microsoft365-Copilot": BASE / "copilot_batch_v2_all_41.csv",
            "Gemini3.6-Flash": revised / "gemini_v2_batch_all_41.csv",
        }
        OUT = BASE / "agreement-6models-v2-unblind"
    if args.output_dir is not None:
        OUT = args.output_dir.resolve()
    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    ids, key = load_key()

    models = {
        name: load_model(path)
        for name, path in MODELS.items()
    }

    reference_set = set(ids)

    for model, data in models.items():
        if set(data) != reference_set:
            raise RuntimeError(
                f"{model}: blind-ID set differs "
                "from researcher key"
            )

    #
    # Freeze exact input hashes at unblinding.
    #
    manifest = {
        "researcher_key": {
            "path": str(KEY_FILE.relative_to(ROOT)),
            "sha256": sha256_file(KEY_FILE),
        },
        "model_files": {
            model: {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_file(path),
            }
            for model, path in MODELS.items()
        },
    }

    (OUT / "unblind_input_manifest.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    runtime_labels = [
        key[i]["runtime_label"]
        for i in ids
    ]

    runtime_counts = Counter(runtime_labels)

    #
    # Overall model-vs-runtime agreement.
    #
    overall_rows = []

    for model, data in models.items():
        labels = [
            data[i]["compliance"]
            for i in ids
        ]

        agreement_n = sum(
            labels[j] == runtime_labels[j]
            for j in range(len(ids))
        )

        decisive_ids = [
            i
            for i in ids
            if data[i]["compliance"]
            != "UNCERTAIN"
        ]

        decisive_agree_n = sum(
            data[i]["compliance"]
            == key[i]["runtime_label"]
            for i in decisive_ids
        )

        runtime_n_ids = [
            i
            for i in ids
            if key[i]["runtime_label"]
            == "NONCOMPLIANT"
        ]

        runtime_c_ids = [
            i
            for i in ids
            if key[i]["runtime_label"]
            == "COMPLIANT"
        ]

        #
        # Among runtime-noncompliant cases.
        #
        model_n_on_runtime_n = sum(
            data[i]["compliance"]
            == "NONCOMPLIANT"
            for i in runtime_n_ids
        )

        model_c_on_runtime_n = sum(
            data[i]["compliance"]
            == "COMPLIANT"
            for i in runtime_n_ids
        )

        model_u_on_runtime_n = sum(
            data[i]["compliance"]
            == "UNCERTAIN"
            for i in runtime_n_ids
        )

        #
        # Among runtime-compliant cases.
        #
        model_c_on_runtime_c = sum(
            data[i]["compliance"]
            == "COMPLIANT"
            for i in runtime_c_ids
        )

        model_n_on_runtime_c = sum(
            data[i]["compliance"]
            == "NONCOMPLIANT"
            for i in runtime_c_ids
        )

        model_u_on_runtime_c = sum(
            data[i]["compliance"]
            == "UNCERTAIN"
            for i in runtime_c_ids
        )

        #
        # Norm-basis agreement where both runtime and
        # model say NONCOMPLIANT.
        #
        jointly_noncompliant = [
            i
            for i in runtime_n_ids
            if data[i]["compliance"]
            == "NONCOMPLIANT"
        ]

        norm_exact_n = sum(
            data[i]["norms"]
            == key[i]["runtime_norms"]
            for i in jointly_noncompliant
        )

        norm_jaccard = (
            sum(
                jaccard(
                    data[i]["norms"],
                    key[i]["runtime_norms"],
                )
                for i in jointly_noncompliant
            )
            / len(jointly_noncompliant)
            if jointly_noncompliant
            else None
        )

        overall_rows.append(
            {
                "model": model,
                "n": len(ids),

                "raw_agreement_n":
                    agreement_n,

                "raw_agreement_rate":
                    agreement_n / len(ids),

                "cohen_kappa":
                    cohen_kappa(
                        labels,
                        runtime_labels,
                    ),

                "uncertain_n":
                    labels.count("UNCERTAIN"),

                "decisive_n":
                    len(decisive_ids),

                "decisive_agreement_n":
                    decisive_agree_n,

                "decisive_agreement_rate":
                    (
                        decisive_agree_n
                        / len(decisive_ids)
                        if decisive_ids
                        else None
                    ),

                "runtime_noncompliant_n":
                    len(runtime_n_ids),

                "model_noncompliant_on_runtime_noncompliant_n":
                    model_n_on_runtime_n,

                "model_noncompliant_on_runtime_noncompliant_rate":
                    model_n_on_runtime_n
                    / len(runtime_n_ids),

                "model_compliant_on_runtime_noncompliant_n":
                    model_c_on_runtime_n,

                "model_uncertain_on_runtime_noncompliant_n":
                    model_u_on_runtime_n,

                "runtime_compliant_n":
                    len(runtime_c_ids),

                "model_compliant_on_runtime_compliant_n":
                    model_c_on_runtime_c,

                "model_compliant_on_runtime_compliant_rate":
                    model_c_on_runtime_c
                    / len(runtime_c_ids),

                "model_noncompliant_on_runtime_compliant_n":
                    model_n_on_runtime_c,

                "model_uncertain_on_runtime_compliant_n":
                    model_u_on_runtime_c,

                "jointly_noncompliant_n":
                    len(jointly_noncompliant),

                "runtime_norm_basis_exact_n":
                    norm_exact_n,

                "runtime_norm_basis_exact_rate":
                    (
                        norm_exact_n
                        / len(jointly_noncompliant)
                        if jointly_noncompliant
                        else None
                    ),

                "runtime_norm_basis_mean_jaccard":
                    norm_jaccard,
            }
        )

    overall_fields = list(
        overall_rows[0].keys()
    )

    write_csv(
        OUT / "model_vs_runtime_overall.csv",
        overall_fields,
        overall_rows,
    )

    #
    # Case-level unblinded table.
    #
    case_rows = []

    for order, blind_id in enumerate(
        ids,
        start=1,
    ):
        labels = {
            model: data[blind_id]["compliance"]
            for model, data in models.items()
        }

        majority = majority_label(
            list(labels.values())
        )

        counts = Counter(labels.values())

        row = {
            "order": order,
            "blind_id": blind_id,
            "scenario_id":
                key[blind_id]["scenario_id"],
            "repetition_index":
                key[blind_id]["repetition_index"],
            "family":
                key[blind_id]["family"],
            "scenario_kind":
                key[blind_id]["scenario_kind"],
            "alignment":
                key[blind_id]["alignment"],
            "runtime_label":
                key[blind_id]["runtime_label"],
            "runtime_norm_labels":
                ";".join(
                    sorted(
                        key[blind_id]["runtime_norms"]
                    )
                ),
            "failed_predicate_types":
                ";".join(
                    key[blind_id][
                        "failed_predicate_types"
                    ]
                ),
            "n_model_compliant":
                counts["COMPLIANT"],
            "n_model_noncompliant":
                counts["NONCOMPLIANT"],
            "n_model_uncertain":
                counts["UNCERTAIN"],
            "model_majority":
                majority,
            "majority_matches_runtime":
                (
                    "YES"
                    if majority
                    == key[blind_id]["runtime_label"]
                    else "NO"
                ),
            "unanimous_models":
                (
                    "YES"
                    if max(counts.values()) == 6
                    else "NO"
                ),
        }

        for model, data in models.items():
            row[f"{model}_label"] = (
                data[blind_id]["compliance"]
            )

            row[f"{model}_norms"] = (
                ";".join(
                    sorted(
                        data[blind_id]["norms"]
                    )
                )
            )

            row[f"{model}_matches_runtime"] = (
                "YES"
                if data[blind_id]["compliance"]
                == key[blind_id]["runtime_label"]
                else "NO"
            )

        case_rows.append(row)

    case_fields = list(case_rows[0].keys())

    write_csv(
        OUT / "case_unblind_summary.csv",
        case_fields,
        case_rows,
    )

    #
    # Majority-vs-runtime.
    #
    comparable_majority = [
        row
        for row in case_rows
        if row["model_majority"] != "TIE"
    ]

    majority_agree = sum(
        row["majority_matches_runtime"] == "YES"
        for row in comparable_majority
    )

    #
    # Group-level analyses.
    #
    group_rows = []

    for field in [
        "family",
        "scenario_kind",
        "alignment",
    ]:
        groups = defaultdict(list)

        for blind_id in ids:
            groups[
                key[blind_id][field]
            ].append(blind_id)

        for value, group_ids in sorted(
            groups.items()
        ):
            group_rows.extend(
                summarize_group(
                    field,
                    value,
                    group_ids,
                    key,
                    models,
                )
            )

    write_csv(
        OUT / "group_agreement.csv",
        [
            "group_type",
            "group_value",
            "model",
            "n",
            "runtime_compliant_n",
            "runtime_noncompliant_n",
            "model_compliant_n",
            "model_noncompliant_n",
            "model_uncertain_n",
            "agreement_n",
            "agreement_rate",
        ],
        group_rows,
    )

    #
    # Failed-predicate analysis.
    # Only runtime-NONCOMPLIANT proposals have a
    # failed predicate by definition.
    #
    predicate_to_ids = defaultdict(list)

    for blind_id in ids:
        if (
            key[blind_id]["runtime_label"]
            != "NONCOMPLIANT"
        ):
            continue

        predicates = key[blind_id][
            "failed_predicate_types"
        ]

        if not predicates:
            predicate_to_ids[
                "<NO_FAILED_PREDICATE_RECORDED>"
            ].append(blind_id)

        for predicate in predicates:
            predicate_to_ids[predicate].append(
                blind_id
            )

    predicate_rows = []

    for predicate, predicate_ids in sorted(
        predicate_to_ids.items()
    ):
        for model, data in models.items():
            n_noncompliant = sum(
                data[i]["compliance"]
                == "NONCOMPLIANT"
                for i in predicate_ids
            )

            n_compliant = sum(
                data[i]["compliance"]
                == "COMPLIANT"
                for i in predicate_ids
            )

            n_uncertain = sum(
                data[i]["compliance"]
                == "UNCERTAIN"
                for i in predicate_ids
            )

            jointly_n = [
                i
                for i in predicate_ids
                if data[i]["compliance"]
                == "NONCOMPLIANT"
            ]

            norm_exact = sum(
                data[i]["norms"]
                == key[i]["runtime_norms"]
                for i in jointly_n
            )

            predicate_rows.append(
                {
                    "predicate_type":
                        predicate,
                    "model":
                        model,
                    "n_runtime_noncompliant":
                        len(predicate_ids),
                    "model_noncompliant_n":
                        n_noncompliant,
                    "model_noncompliant_rate":
                        n_noncompliant
                        / len(predicate_ids),
                    "model_compliant_n":
                        n_compliant,
                    "model_uncertain_n":
                        n_uncertain,
                    "jointly_noncompliant_n":
                        len(jointly_n),
                    "norm_basis_exact_n":
                        norm_exact,
                    "norm_basis_exact_rate":
                        (
                            norm_exact
                            / len(jointly_n)
                            if jointly_n
                            else None
                        ),
                }
            )

    write_csv(
        OUT / "predicate_agreement.csv",
        [
            "predicate_type",
            "model",
            "n_runtime_noncompliant",
            "model_noncompliant_n",
            "model_noncompliant_rate",
            "model_compliant_n",
            "model_uncertain_n",
            "jointly_noncompliant_n",
            "norm_basis_exact_n",
            "norm_basis_exact_rate",
        ],
        predicate_rows,
    )

    #
    # Particularly useful disagreement cases.
    #
    disagreement_rows = [
        row
        for row in case_rows
        if (
            row["unanimous_models"] == "NO"
            or
            row["majority_matches_runtime"] == "NO"
        )
    ]

    write_csv(
        OUT / "unblind_disagreement_cases.csv",
        case_fields,
        disagreement_rows,
    )

    #
    # JSON summary.
    #
    summary = {
        "n_cases": len(ids),

        "runtime_reference_counts": {
            "COMPLIANT":
                runtime_counts["COMPLIANT"],
            "NONCOMPLIANT":
                runtime_counts["NONCOMPLIANT"],
        },

        "majority": {
            "comparable_cases":
                len(comparable_majority),
            "ties":
                len(ids)
                - len(comparable_majority),
            "agreement_n":
                majority_agree,
            "agreement_rate":
                (
                    majority_agree
                    / len(comparable_majority)
                    if comparable_majority
                    else None
                ),
        },

        "models": {
            row["model"]: row
            for row in overall_rows
        },
    }

    (OUT / "unblind_summary.json").write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    #
    # Console output.
    #
    print("=== Unblinded model-vs-runtime analysis ===")
    print(f"Cases: {len(ids)}")
    print(
        "Runtime reference: "
        f"C={runtime_counts['COMPLIANT']} "
        f"N={runtime_counts['NONCOMPLIANT']}"
    )
    print()

    print(
        f"{'Model':24s} "
        f"{'Agree':>10s} "
        f"{'Kappa':>6s} "
        f"{'N/N':>8s} "
        f"{'C/C':>8s} "
        f"{'Norm':>8s}"
    )
    print("-" * 72)

    for row in overall_rows:
        norm_rate = (
            row["runtime_norm_basis_exact_rate"]
        )

        norm_text = (
            f"{100 * norm_rate:.1f}%"
            if norm_rate is not None
            else "n/a"
        )

        print(
            f"{row['model']:24s} "
            f"{row['raw_agreement_n']:2d}/41 "
            f"({100*row['raw_agreement_rate']:5.1f}%) "
            f"{row['cohen_kappa']:6.3f} "
            f"{row['model_noncompliant_on_runtime_noncompliant_n']:2d}/"
            f"{row['runtime_noncompliant_n']:2d} "
            f"{row['model_compliant_on_runtime_compliant_n']:2d}/"
            f"{row['runtime_compliant_n']:2d} "
            f"{norm_text:>8s}"
        )

    print()
    print(
        "Six-model majority vs runtime: "
        f"{majority_agree}/"
        f"{len(comparable_majority)} "
        f"({100 * majority_agree / len(comparable_majority):.1f}%)"
    )

    print(
        "Majority ties: "
        f"{len(ids) - len(comparable_majority)}"
    )

    print()
    print(f"Outputs: {OUT}")


if __name__ == "__main__":
    main()

    
