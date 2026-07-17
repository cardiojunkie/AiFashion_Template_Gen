import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from app.main import preflight
from app.services.preflight import preflight_workbook


def source(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    workbook.active.append(headers)
    for row in rows:
        workbook.active.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_preflight_reports_blockers_without_provider_calls() -> None:
    result = preflight_workbook(
        source(["sku", "base_code", "image_1", "image_3"], [[None, "A", "ftp://bad", "x"]]),
        required_headers=["sku", "base_code"],
    )
    assert result["valid"] is False
    assert {issue["code"] for issue in result["issues"]} == {
        "missing_required_value",
        "invalid_image_url",
        "non_contiguous_images",
    }


def test_preflight_allows_different_attribute_sets_across_groups() -> None:
    result = preflight_workbook(
        source(["sku", "base_code", "category"], [["1", "A", "top"], ["2", "B", "shoe"]]),
        required_headers=["sku", "base_code"],
        attribute_rules=[
            {"attribute_set": "tops", "when": {"category": "top"}},
            {"attribute_set": "shoes", "when": {"category": "shoe"}},
        ],
    )
    assert result["valid"] is True


def test_preflight_rejects_unassigned_attribute_set() -> None:
    result = preflight_workbook(
        source(["sku", "base_code", "category"], [["1", "A", "unknown"]]),
        required_headers=["sku", "base_code"],
        attribute_rules=[{"attribute_set": "tops", "when": {"category": "top"}}],
    )
    assert "unassigned_attribute_set" in {issue["code"] for issue in result["issues"]}


class Upload:
    filename = "catalog.xlsx"
    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def __init__(self, content: bytes):
        self.content = content

    async def read(self, size: int) -> bytes:
        return self.content


def test_api_preflight_rejects_an_unpublished_attribute_set(db, monkeypatch):
    monkeypatch.setattr("app.storage.put_bytes", lambda *_: None)
    result = asyncio.run(
        preflight(
            Upload(source(["category"], [["top"]])),
            required_headers="[]",
            aliases="{}",
            attribute_rules='[{"attribute_set":"missing","when":{"category":"top"}}]',
            db=db,
        )
    )

    assert result["valid"] is False
    assert "unknown_attribute_set" in {issue["code"] for issue in result["issues"]}


def test_api_preflight_sanitizes_a_corrupt_workbook(db):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            preflight(
                Upload(b"not an xlsx"),
                required_headers="[]",
                aliases="{}",
                attribute_rules="[]",
                db=db,
            )
        )

    assert caught.value.status_code == 422
