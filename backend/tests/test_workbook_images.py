from io import BytesIO

from openpyxl import Workbook

from app.services.workbook_images import parse_image_workbook


def workbook_bytes(headers: list[str], row: list[object]) -> bytes:
    workbook = Workbook()
    workbook.active.append(headers)
    workbook.active.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_parses_aliases_dynamic_images_and_raw_values() -> None:
    parsed = parse_image_workbook(
        workbook_bytes(["Stock Code", "Image 1", "image-2"], ["0007", "a", "b"]),
        {"sku": ["Stock Code"]},
    )
    assert parsed["headers"] == ["sku", "image_1", "image_2"]
    assert parsed["image_columns"] == ["image_1", "image_2"]
    assert parsed["rows"][0]["values"]["sku"] == "0007"
    assert parsed["rows"][0]["raw"]["Stock Code"] == "0007"


def test_accepts_one_alias_as_a_string() -> None:
    parsed = parse_image_workbook(workbook_bytes(["Stock Code"], ["1"]), {"sku": "Stock Code"})
    assert parsed["headers"] == ["sku"]
