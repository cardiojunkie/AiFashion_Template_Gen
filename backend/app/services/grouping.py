from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


def row_values(row: Mapping[str, Any]) -> Mapping[str, Any]:
    values = row.get("values")
    return values if isinstance(values, Mapping) else row


def assign_attribute_set(row: Mapping[str, Any], rules: Sequence[Mapping[str, Any]]) -> str | None:
    values = row_values(row)
    for rule in rules:
        conditions = rule.get("when", {})
        if not isinstance(conditions, Mapping):
            continue
        matches = True
        for field, expected in conditions.items():
            options = expected if isinstance(expected, (list, tuple, set)) else [expected]
            matches = matches and str(values.get(field, "")).casefold() in {
                str(option).casefold() for option in options
            }
        if matches:
            return str(rule.get("attribute_set") or rule.get("name") or "") or None
    return None


def group_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    base_code_column: str = "base_code",
    attribute_set_column: str = "_attribute_set",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    issues: list[dict[str, Any]] = []
    for row in rows:
        base_code = row_values(row).get(base_code_column)
        if base_code is None or not str(base_code).strip():
            issues.append(
                {
                    "code": "missing_base_code",
                    "row": row.get("row_number"),
                    "field": base_code_column,
                    "blocking": True,
                    "message": "base code is required for grouping",
                }
            )
            continue
        grouped[str(base_code).strip().casefold()].append(row)

    groups: list[dict[str, Any]] = []
    for _, members in grouped.items():
        base_code = str(row_values(members[0])[base_code_column]).strip()
        attribute_sets = {
            str(row_values(member).get(attribute_set_column))
            for member in members
            if row_values(member).get(attribute_set_column)
        }
        valid = len(attribute_sets) <= 1
        if not valid:
            issues.append(
                {
                    "code": "mixed_attribute_sets",
                    "base_code": base_code,
                    "blocking": True,
                    "message": "one base-code group resolves to multiple attribute sets",
                }
            )
        groups.append(
            {
                "base_code": base_code,
                "rows": list(members),
                "attribute_set": next(iter(attribute_sets), None) if valid else None,
                "valid": valid,
                "representative_image": select_representative_image(members),
            }
        )
    return groups, issues


def select_representative_image(rows: Sequence[Mapping[str, Any]]) -> str | None:
    ordered = sorted(rows, key=lambda row: int(row.get("row_number") or 0))
    for row in ordered:
        images = row.get("images")
        if not isinstance(images, Sequence) or isinstance(images, (str, bytes)):
            images = [
                value
                for key, value in row_values(row).items()
                if str(key).casefold().startswith("image_")
            ]
        for image in images:
            if image is not None and str(image).strip():
                return str(image).strip()
    return None
