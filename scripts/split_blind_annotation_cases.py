# scripts/split_blind_annotation_cases.py

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

SRC = (
    ROOT
    / "artifacts"
    / "e2-blind-validation"
    / "blind_annotation_cases.md"
)

OUT_DIR = SRC.parent

BATCHES = [
    (1, 10, "blind_cases_01_10.md"),
    (11, 20, "blind_cases_11_20.md"),
    (21, 30, "blind_cases_21_30.md"),
    (31, 41, "blind_cases_31_41.md"),
]


def main():
    text = SRC.read_text(encoding="utf-8")

    matches = list(
        re.finditer(
            r"(?m)^## Case (\d+):[^\n]*$",
            text,
        )
    )

    case_numbers = [int(m.group(1)) for m in matches]

    expected = list(range(1, 42))
    if case_numbers != expected:
        raise RuntimeError(
            "Expected exactly Cases 1..41 in order.\n"
            f"Found: {case_numbers}"
        )

    # Preserve the original preamble exactly.
    preamble = text[: matches[0].start()].rstrip() + "\n\n"

    cases = {}

    for i, match in enumerate(matches):
        case_no = int(match.group(1))

        start = match.start()
        end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(text)
        )

        cases[case_no] = text[start:end].strip() + "\n"

    for first, last, filename in BATCHES:
        selected = [
            cases[n]
            for n in range(first, last + 1)
        ]

        output = preamble + "\n".join(selected)
        path = OUT_DIR / filename
        path.write_text(output.rstrip() + "\n", encoding="utf-8")

        print(
            f"Wrote {path} "
            f"(Cases {first}-{last}, "
            f"{last-first+1} cases)"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
