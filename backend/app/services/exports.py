from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from io import BytesIO, StringIO
from pathlib import PurePath
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CatalogItem, Run, ValidationIssue

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def export_blockers(db: Session, run: Run) -> dict[str, object]:
    validation_errors = (
        db.scalar(
            select(func.count())
            .select_from(ValidationIssue)
            .join(CatalogItem, CatalogItem.id == ValidationIssue.item_id)
            .where(
                CatalogItem.run_id == run.id,
                ValidationIssue.resolved_at.is_(None),
                ValidationIssue.severity == "error",
            )
        )
        or 0
    )
    incomplete_items = (
        db.scalar(
            select(func.count())
            .select_from(CatalogItem)
            .where(
                CatalogItem.run_id == run.id,
                ~CatalogItem.status.in_(["ready", "needs_review", "edited"]),
            )
        )
        or 0
    )
    run_incomplete = (
        run.status != "completed" or run.failed_items > 0 or run.completed_items != run.total_items
    )
    return {
        "blocked": bool(validation_errors or incomplete_items or run_incomplete),
        "run_status": run.status,
        "validation_errors": validation_errors,
        "incomplete_items": incomplete_items,
        "completed_items": run.completed_items,
        "failed_items": run.failed_items,
        "total_items": run.total_items,
    }


def spreadsheet_safe(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_filename(name: str, fallback: str = "export") -> str:
    base = PurePath(name.replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return cleaned or fallback


def assert_export_allowed(
    issues: Sequence[Mapping[str, object]], *, override_blocking: bool = False
) -> None:
    if not override_blocking and any(issue.get("blocking") for issue in issues):
        raise ValueError("blocking validation issues require an audited export override")


def _columns(rows: Sequence[Mapping[str, object]], columns: Sequence[str] | None) -> list[str]:
    return list(columns or dict.fromkeys(key for row in rows for key in row))


def export_csv(
    rows: Sequence[Mapping[str, object]],
    *,
    columns: Sequence[str] | None = None,
    text_columns: Sequence[str] = ("sku", "ean"),
    issues: Sequence[Mapping[str, object]] = (),
    override_blocking: bool = False,
) -> bytes:
    assert_export_allowed(issues, override_blocking=override_blocking)
    headers = _columns(rows, columns)
    text = {column.casefold() for column in text_columns}
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(spreadsheet_safe(header) for header in headers)
    for row in rows:
        values: list[object] = []
        for header in headers:
            value = spreadsheet_safe(row.get(header))
            if header.casefold() in text and value is not None and not str(value).startswith("'"):
                value = "'" + str(value)
            values.append(value)
        writer.writerow(values)
    return output.getvalue().encode("utf-8-sig")


def export_xlsx(
    rows: Sequence[Mapping[str, object]],
    *,
    columns: Sequence[str] | None = None,
    text_columns: Sequence[str] = ("sku", "ean"),
    issues: Sequence[Mapping[str, object]] = (),
    override_blocking: bool = False,
) -> bytes:
    assert_export_allowed(issues, override_blocking=override_blocking)
    headers = _columns(rows, columns)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Catalog"
    sheet.append([spreadsheet_safe(header) for header in headers])
    text = {column.casefold() for column in text_columns}
    for row_index, row in enumerate(rows, start=2):
        for column_index, header in enumerate(headers, start=1):
            value = row.get(header)
            if header.casefold() in text and value is not None:
                value = str(value)
            cell = sheet.cell(row_index, column_index, spreadsheet_safe(value))
            if header.casefold() in text:
                cell.number_format = "@"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def export_images_zip(images: Mapping[str, bytes] | Sequence[Mapping[str, object]]) -> bytes:
    items = (
        images.items()
        if isinstance(images, Mapping)
        else (
            (str(item.get("filename") or item.get("name") or "image.jpg"), item["data"])
            for item in images
        )
    )
    output = BytesIO()
    used: set[str] = set()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for original_name, data in items:
            name = safe_filename(original_name, "image.jpg")
            stem, dot, suffix = name.rpartition(".")
            stem, suffix = (stem, "." + suffix) if dot else (name, "")
            candidate, counter = name, 2
            while candidate.casefold() in used:
                candidate = f"{stem}_{counter}{suffix}"
                counter += 1
            used.add(candidate.casefold())
            archive.writestr(candidate, data)
    return output.getvalue()
