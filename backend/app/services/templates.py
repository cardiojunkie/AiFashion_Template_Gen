from __future__ import annotations

from collections.abc import Mapping, Sequence
from io import BytesIO

from openpyxl import Workbook


def generate_template(
    required_columns: Sequence[str | Mapping[str, object]], *, image_columns: int = 10
) -> bytes:
    """Return the exact configured columns followed by image_1..image_N."""
    if not 0 <= image_columns <= 50:
        raise ValueError("image_columns must be between 0 and 50")
    columns = [
        str(column.get("name") or column.get("key")) if isinstance(column, Mapping) else str(column)
        for column in required_columns
    ]
    images = [f"image_{position}" for position in range(1, image_columns + 1)]
    folded = [column.casefold() for column in [*columns, *images]]
    if not all(columns) or len(folded) != len(set(folded)):
        raise ValueError("template columns must be unique and non-empty")
    columns.extend(images)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Catalog"
    sheet.append(columns)
    sheet.freeze_panes = "A2"
    for cell in sheet[1]:
        cell.number_format = "@"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
