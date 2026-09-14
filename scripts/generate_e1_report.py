from __future__ import annotations

from eg_runtime.e1_report import (
    CONDITION_LABELS,
    METRICS,
    ReportGenerationError,
    build_report,
    main,
    validate_fences,
    validate_report,
)


__all__ = [
    "CONDITION_LABELS",
    "METRICS",
    "ReportGenerationError",
    "build_report",
    "main",
    "validate_fences",
    "validate_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
