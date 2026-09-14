from pathlib import Path

src = Path("artifacts/e2-blind-validation/blind_annotation_cases.md")
dst = Path("artifacts/e2-blind-validation/blind_annotation_cases_ja.md")

text = src.read_text(encoding="utf-8")

replacements = {
    "# E2 Blinded Proposal-Compliance Cases":
        "# E2 uChEv|[UK«]¿P[X",

    "Do not infer compliance from task intent alone. "
    "Apply the supplied organizational norms to the generated proposal "
    "and organizational context.":
        "³Ì^XNÌÓ}¾¯©çK«ðªµÈ¢Å­¾³¢B"
        "ñ¦³ê½gDKÍðA¶¬³ê½v|[UÆ"
        "gDIReLXgÉKpµÄ»èµÄ­¾³¢B",

    "## Case ": "## P[X ",
    "### Organizational context": "### gDIReLXg",
    "### Generated proposal": "### ¶¬³ê½v|[U",
    "### Organizational norms": "### gDKÍ",
}

for old, new in replacements.items():
    if old not in text and old.startswith("###"):
        raise SystemExit(f"STOP: expected heading not found: {old}")
    text = text.replace(old, new)

dst.write_text(text, encoding="utf-8")
print(f"Wrote: {dst}")
