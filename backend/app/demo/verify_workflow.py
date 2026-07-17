from __future__ import annotations

import json
from io import BytesIO

from openpyxl import load_workbook

from app.services.exports import export_csv, export_xlsx
from app.services.pipeline import enrich_group
from app.services.preflight import preflight_workbook
from app.services.providers import MockProvider
from app.services.templates import generate_template


def verify_workflow() -> dict[str, object]:
    template = generate_template(["sku", "base_code", "category", "color"])
    workbook = load_workbook(BytesIO(template))
    workbook.active.append(["0001", "STYLE-1", "tops", "rouge", "https://example.com/1.jpg"])
    source = BytesIO()
    workbook.save(source)

    preflight = preflight_workbook(
        source.getvalue(),
        required_headers=["sku", "base_code", "category"],
        attribute_rules=[{"attribute_set": "tops", "when": {"category": "tops"}}],
    )
    assert preflight["valid"], preflight["issues"]
    attributes = [
        {
            "name": "color",
            "required": True,
            "value_list": {"Red": ["rouge"], "Blue": ["bleu"]},
        },
        {"name": "description", "required": True},
    ]
    provider = MockProvider({"description": {"value": "Red cotton top", "confidence": 0.95}})
    result = enrich_group(
        preflight["groups"][0],
        attributes,
        provider=provider,
        schema={
            "type": "object",
            "required": ["description"],
            "additionalProperties": False,
            "properties": {
                "description": {
                    "type": "object",
                    "required": ["value", "confidence"],
                    "properties": {
                        "value": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                }
            },
        },
        images=["https://example.com/1.jpg"],
        image_checksums=["demo-image"],
    )
    assert result["valid"], result["issues"]
    assert result["values"] == {"color": "Red", "description": "Red cotton top"}
    rows = [{"sku": "0001", "base_code": "STYLE-1", **result["values"]}]
    csv_output, xlsx_output = export_csv(rows), export_xlsx(rows)
    assert csv_output and xlsx_output
    return {
        "rows": preflight["row_count"],
        "groups": preflight["group_count"],
        "provider_calls": len(provider.calls),
        "csv_bytes": len(csv_output),
        "xlsx_bytes": len(xlsx_output),
        "ok": True,
    }


if __name__ == "__main__":
    print(json.dumps(verify_workflow(), sort_keys=True))
