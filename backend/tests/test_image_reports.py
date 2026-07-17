from io import BytesIO
from zipfile import ZipFile

from openpyxl import load_workbook

from app.services.reports import (
    image_report_csv,
    image_report_xlsx,
    image_validation_summary,
    normalized_image_zip,
)


def test_reports_protect_formulas_and_summarize() -> None:
    rows = [{"status": "ok", "url": "=HYPERLINK(1)"}, {"status": "error", "error": "bad"}]
    assert b"'=HYPERLINK" in image_report_csv(rows)
    workbook = load_workbook(BytesIO(image_report_xlsx(rows)))
    assert workbook.active["B2"].value.startswith("'=")
    assert image_validation_summary(rows) == {"total": 2, "ok": 1, "failed": 1}


def test_image_zip_strips_paths() -> None:
    archive = ZipFile(BytesIO(normalized_image_zip({"../../a.jpg": b"a"})))
    assert archive.namelist() == ["a.jpg"]
