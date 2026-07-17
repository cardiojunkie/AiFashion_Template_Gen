from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.mapping import _options


def _issue(code: str, field: str | None, message: str, *, blocking: bool = True) -> dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "message": message,
        "severity": "error" if blocking else "warning",
        "blocking": blocking,
    }


def validate_fields(
    values: Mapping[str, Any],
    attributes: Sequence[Mapping[str, Any]],
    *,
    provenance: Mapping[str, Mapping[str, Any]] | None = None,
    existing_issues: Sequence[Mapping[str, Any]] = (),
    confidence_threshold: float | None = None,
) -> list[dict[str, Any]]:
    issues = [dict(issue) for issue in existing_issues]
    provenance = provenance or {}
    for attribute in attributes:
        name = str(attribute.get("name") or attribute.get("key") or "")
        value = values.get(name)
        blank = value is None or (isinstance(value, str) and not value.strip())
        if attribute.get("required") and blank:
            issues.append(_issue("missing_required", name, "required value is blank"))
            continue
        options = _options(attribute)
        if options and not blank:
            delimiter = str(attribute.get("delimiter") or "|")
            items = str(value).split(delimiter) if attribute.get("multiselect") else [value]
            if any(str(item) not in options for item in items):
                issues.append(_issue("invalid_select", name, "value is not in the configured list"))
        threshold = attribute.get("confidence_threshold", confidence_threshold)
        confidence = provenance.get(name, {}).get("confidence")
        if (
            threshold is not None
            and confidence is not None
            and float(confidence) < float(threshold)
        ):
            issues.append(
                _issue(
                    "low_confidence",
                    name,
                    f"confidence {float(confidence):.2f} is below {float(threshold):.2f}",
                    blocking=bool(attribute.get("low_confidence_blocks", False)),
                )
            )
    return issues
