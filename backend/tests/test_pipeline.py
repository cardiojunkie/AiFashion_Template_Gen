from app.services.pipeline import enrich_group
from app.services.providers import MockProvider


def test_pipeline_records_vision_provenance_and_validates() -> None:
    group = {
        "base_code": "A",
        "rows": [{"row_number": 2, "values": {"base_code": "A", "sku": "1"}}],
        "representative_image": "https://example.com/a.jpg",
    }
    provider = MockProvider({"description": {"value": "A red top", "confidence": 0.9}})
    result = enrich_group(
        group,
        [{"name": "description", "required": True}],
        provider=provider,
        model="mock",
        prompt_version="v1",
        schema={
            "type": "object",
            "required": ["description"],
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
    )
    assert result["valid"] is True
    assert result["provenance"]["description"]["prompt_version"] == "v1"
