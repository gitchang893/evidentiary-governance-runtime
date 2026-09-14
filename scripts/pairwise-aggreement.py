import csv
from pathlib import Path
from collections import Counter

base = Path("artifacts/e2-blind-validation")
v2 = base / "clarified-spec-v2"

files = {
    "Qwen 3.5 9B":
        v2 / "qwen35_v2_batch_all_41.csv",
    "Gemma 4 12B":
        v2 / "gemma4_v2_batch_all_41.csv",
    "Llama 3.1 8B":
        v2 / "llama31_v2_batch_all_41.csv",
    "Phi-4 14B":
        v2 / "phi4_v2_batch_all_41.csv",
    "Microsoft 365 Copilot":
        base / "copilot_batch_v2_all_41.csv",
    "Gemini 3.6 Flash":
        v2 / "gemini_v2_batch_all_41.csv",
}

md_file = v2 / "pairwise_agreement_upper_raw_lower_kappa_v2.md"
raw_file = v2 / "pairwise_raw_agreement_matrix_v2.csv"
kappa_file = v2 / "pairwise_cohen_kappa_matrix_v2.csv"

def load(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    result = {}
    for row in rows:
        blind_id = row["blind_id"].strip()
        compliance = row["compliance"].strip().upper()

        if blind_id in result:
            raise ValueError(
                f"Duplicate blind_id in {path}: {blind_id}"
            )

        if compliance not in {
            "COMPLIANT",
            "NONCOMPLIANT",
            "UNCERTAIN",
        }:
            raise ValueError(
                f"Unexpected label in {path}: "
                f"{blind_id} = {compliance}"
            )

        result[blind_id] = compliance

    if len(result) != 41:
        raise ValueError(
            f"Expected 41 cases in {path}, found {len(result)}"
        )

    return result

def agreement_and_kappa(a, b):
    ids = sorted(a)

    if set(a) != set(b):
        raise ValueError("The two annotators have different IDs")

    labels_a = [a[blind_id] for blind_id in ids]
    labels_b = [b[blind_id] for blind_id in ids]
    n = len(ids)

    observed = sum(
        x == y for x, y in zip(labels_a, labels_b)
    ) / n

    counts_a = Counter(labels_a)
    counts_b = Counter(labels_b)

    categories = {
        "COMPLIANT",
        "NONCOMPLIANT",
        "UNCERTAIN",
    }

    expected = sum(
        (counts_a[category] / n)
        * (counts_b[category] / n)
        for category in categories
    )

    if abs(1.0 - expected) < 1e-12:
        kappa = float("nan")
    else:
        kappa = (observed - expected) / (1.0 - expected)

    return observed, kappa

annotations = {
    name: load(path)
    for name, path in files.items()
}

names = list(files)

reference_ids = set(annotations[names[0]])

for name in names[1:]:
    if set(annotations[name]) != reference_ids:
        print("ID mismatch:", name)
        print(
            "Missing:",
            sorted(reference_ids - set(annotations[name])),
        )
        print(
            "Unexpected:",
            sorted(set(annotations[name]) - reference_ids),
        )
        raise SystemExit(1)

raw = {}
kappa = {}

for name_a in names:
    raw[name_a] = {}
    kappa[name_a] = {}

    for name_b in names:
        if name_a == name_b:
            raw[name_a][name_b] = 1.0
            kappa[name_a][name_b] = 1.0
        else:
            observed, coefficient = agreement_and_kappa(
                annotations[name_a],
                annotations[name_b],
            )
            raw[name_a][name_b] = observed
            kappa[name_a][name_b] = coefficient

header = ["LLM annotator", *names]
separator = ["---", *(["---:"] * len(names))]

lines = [
    "**Pairwise agreement among the six "
    "LLM annotators under v2**",
    "",
    "| " + " | ".join(header) + " |",
    "|" + "|".join(separator) + "|",
]

for i, row_name in enumerate(names):
    cells = [f"**{row_name}**"]

    for j, column_name in enumerate(names):
        if i == j:
            value = "?"
        elif j > i:
            value = (
                f"**{100 * raw[row_name][column_name]:.1f}%**"
            )
        else:
            coefficient = kappa[row_name][column_name]
            value = (
                "NA"
                if coefficient != coefficient
                else f"{coefficient:.3f}"
            )

        cells.append(value)

    lines.append("| " + " | ".join(cells) + " |")

lines.extend([
    "",
    "*Note: The upper triangle reports raw agreement; "
    "the lower triangle reports Cohenfs È. "
    "Values are based on the v2 annotator judgments.*",
])

markdown = "\n".join(lines)

print(markdown)

md_file.write_text(markdown + "\n", encoding="utf-8")

with raw_file.open("w", encoding="utf-8", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["LLM annotator", *names])

    for row_name in names:
        writer.writerow([
            row_name,
            *[
                f"{raw[row_name][column_name]:.6f}"
                for column_name in names
            ],
        ])

with kappa_file.open("w", encoding="utf-8", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["LLM annotator", *names])

    for row_name in names:
        writer.writerow([
            row_name,
            *[
                f"{kappa[row_name][column_name]:.6f}"
                for column_name in names
            ],
        ])

print()
print("Output:", md_file)
print("Output:", raw_file)
print("Output:", kappa_file)
