import pytest

from app.services.providers import MockProvider
from app.services.vision import ProviderSchemaError, extract_vision

SCHEMA = {
    "type": "object",
    "required": ["color"],
    "additionalProperties": False,
    "properties": {"color": {"type": "string"}},
}


def test_schema_repair_once_then_cache() -> None:
    provider = MockProvider(["not json", {"color": "Red"}])
    cache = {}
    kwargs = {
        "prompt": "Extract",
        "prompt_version": "1",
        "images": ["https://example.com/a.jpg"],
        "image_checksums": ["abc"],
        "schema": SCHEMA,
        "model": "mock",
        "cache": cache,
    }
    assert extract_vision(provider, **kwargs)["data"] == {"color": "Red"}
    assert extract_vision(provider, **kwargs)["cached"] is True
    assert len(provider.calls) == 2


def test_failed_repair_is_clear() -> None:
    provider = MockProvider(["bad", "still bad"])
    with pytest.raises(ProviderSchemaError, match="repair failed"):
        extract_vision(
            provider,
            prompt="Extract",
            prompt_version="1",
            images=["x"],
            image_checksums=["x"],
            schema=SCHEMA,
            model="mock",
        )


def test_provenance_uses_safe_image_reference() -> None:
    result = extract_vision(
        MockProvider({"color": "Red"}),
        prompt="Extract",
        prompt_version="1",
        images=[{"url": "data:image/jpeg;base64,abc", "reference": "source.jpg"}],
        image_checksums=["abc"],
        schema=SCHEMA,
        model="mock",
    )
    assert result["provenance"]["images"] == ["source.jpg"]
