from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from app.services.grouping import assign_attribute_set, group_rows
from app.services.workbook_images import WorkbookSource, parse_image_workbook


def _issue(code: str, message: str, **details: object) -> dict[str, object]:
    return {"code": code, "message": message, "blocking": True, **details}


def preflight_workbook(
    source: WorkbookSource,
    *,
    required_headers: Sequence[str],
    aliases: Mapping[str, str | Iterable[str]] | None = None,
    base_code_column: str = "base_code",
    attribute_rules: Sequence[Mapping[str, Any]] = (),
    max_rows: int = 5_000,
) -> dict[str, Any]:
    parsed = parse_image_workbook(source, aliases, max_image_columns=50)
    headers = parsed["headers"]
    rows = parsed["rows"]
    assert isinstance(headers, list) and isinstance(rows, list)
    issues: list[dict[str, object]] = []

    for header in required_headers:
        if header not in headers:
            issues.append(
                _issue("missing_header", f"missing required header: {header}", field=header)
            )
    if len(rows) > max_rows:
        issues.append(_issue("too_many_rows", f"workbook exceeds the {max_rows} row limit"))

    image_columns = parsed["image_columns"]
    assert isinstance(image_columns, list)
    positions = sorted(int(str(column).rsplit("_", 1)[1]) for column in image_columns)
    if positions and positions != list(range(1, max(positions) + 1)):
        issues.append(
            _issue("non_contiguous_images", "image columns must be contiguous starting at image_1")
        )

    enriched_rows: list[dict[str, Any]] = []
    for parsed_row in rows:
        row = dict(parsed_row)
        values = dict(row["values"])
        for header in required_headers:
            if header in headers and (
                values.get(header) is None or not str(values.get(header)).strip()
            ):
                issues.append(
                    _issue(
                        "missing_required_value",
                        f"required value is blank: {header}",
                        row=row["row_number"],
                        field=header,
                    )
                )
        for column in image_columns:
            value = values.get(column)
            if value and (
                urlsplit(str(value)).scheme not in {"http", "https"}
                or not urlsplit(str(value)).hostname
            ):
                issues.append(
                    _issue(
                        "invalid_image_url",
                        "image URL must be absolute HTTP/HTTPS",
                        row=row["row_number"],
                        field=column,
                    )
                )
        values["_attribute_set"] = assign_attribute_set(row, attribute_rules)
        if attribute_rules and values["_attribute_set"] is None:
            issues.append(
                _issue(
                    "unassigned_attribute_set",
                    "row does not match an attribute-set assignment rule",
                    row=row["row_number"],
                )
            )
        row["values"] = values
        enriched_rows.append(row)

    groups, group_issues = group_rows(
        enriched_rows,
        base_code_column=base_code_column,
        attribute_set_column="_attribute_set",
    )
    issues.extend(group_issues)
    return {
        **parsed,
        "rows": enriched_rows,
        "groups": groups,
        "row_count": len(enriched_rows),
        "group_count": len(groups),
        "issues": issues,
        "valid": not any(issue.get("blocking") for issue in issues),
    }
