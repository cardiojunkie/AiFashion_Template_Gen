from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from io import BytesIO, StringIO
from pathlib import PurePath
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook


def safe_cell(value: object) -> object:
    if isinstance(value, str) and value.lstrip()[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


def image_report_csv(rows: Sequence[Mapping[str, object]]) -> bytes:
    columns = list(dict.fromkeys(key for row in rows for key in row if key != "data"))
    output = StringIO(newline="")
    writer = csv.DictWriter(output, columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({key: safe_cell(value) for key, value in row.items()} for row in rows)
    return output.getvalue().encode("utf-8-sig")


def image_report_xlsx(rows: Sequence[Mapping[str, object]]) -> bytes:
    columns = list(dict.fromkeys(key for row in rows for key in row if key != "data"))
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Image report"
    sheet.append(columns)
    for row in rows:
        sheet.append([safe_cell(row.get(column)) for column in columns])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def normalized_image_zip(images: Mapping[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in images.items():
            safe_name = PurePath(str(name).replace("\\", "/")).name or "image.jpg"
            archive.writestr(safe_name, data)
    return output.getvalue()


def image_validation_summary(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    ok = sum(row.get("status") == "ok" for row in rows)
    return {"total": len(rows), "ok": ok, "failed": len(rows) - ok}
