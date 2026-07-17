from io import BytesIO

from openpyxl import load_workbook

from app.services.templates import generate_template


def test_template_has_exact_required_and_image_columns() -> None:
    sheet = load_workbook(BytesIO(generate_template(["sku", "base_code"]))).active
    assert [cell.value for cell in sheet[1]] == [
        "sku",
        "base_code",
        *(f"image_{position}" for position in range(1, 11)),
    ]
