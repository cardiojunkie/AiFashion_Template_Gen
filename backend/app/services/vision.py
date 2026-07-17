from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from app.services.providers import Provider, ProviderResult


class ProviderSchemaError(ValueError):
    pass


def vision_cache_key(
    *,
    model_settings: Mapping[str, Any],
    prompt_version: str,
    image_checksums: Sequence[str],
    schema: Mapping[str, Any],
) -> str:
    payload = {
        "model_settings": model_settings,
        "prompt_version": prompt_version,
        "image_checksums": list(image_checksums),
        "schema": schema,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def build_vision_messages(
    prompt: str,
    images: Sequence[str | Mapping[str, Any]],
    schema: Mapping[str, Any],
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"{prompt}\nReturn only JSON matching this schema: "
                f"{json.dumps(schema, sort_keys=True)}"
            ),
        }
    ]
    for position, image in enumerate(images, start=1):
        image_url = image.get("url") if isinstance(image, Mapping) else image
        content.extend(
            [
                {"type": "text", "text": f"IMAGE_POSITION_{position}"},
                {"type": "image_url", "image_url": {"url": str(image_url)}},
            ]
        )
    return [{"role": "user", "content": content}]


def _validate(value: Any, schema: Mapping[str, Any], path: str = "response") -> None:
    expected = schema.get("type")
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected in checks and not checks[expected](value):
        raise ProviderSchemaError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ProviderSchemaError(f"{path} is not an allowed value")
    if expected == "object":
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ProviderSchemaError(f"{path}.{key} is required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ProviderSchemaError(f"{path} has unexpected fields: {sorted(extras)}")
        for key, child_schema in properties.items():
            if key in value:
                _validate(value[key], child_schema, f"{path}.{key}")
    if expected == "array" and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]")


def _decode(result: ProviderResult, schema: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(result.content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProviderSchemaError("provider response is not valid JSON") from exc
    _validate(value, schema)
    if not isinstance(value, dict):
        raise ProviderSchemaError("provider response must be a JSON object")
    return value


def _image_reference(image: str | Mapping[str, Any], position: int) -> object:
    if isinstance(image, Mapping):
        return image.get("reference") or image.get("url") or f"image_{position}"
    return f"image_{position}" if image.startswith("data:") else image


def extract_vision(
    provider: Provider,
    *,
    prompt: str,
    prompt_version: str,
    images: Sequence[str | Mapping[str, Any]],
    image_checksums: Sequence[str],
    schema: Mapping[str, Any],
    model: str,
    model_settings: Mapping[str, Any] | None = None,
    profile_snapshot: Mapping[str, Any] | None = None,
    cache: MutableMapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = {"model": model, **(model_settings or {})}
    key = vision_cache_key(
        model_settings=settings,
        prompt_version=prompt_version,
        image_checksums=image_checksums,
        schema=schema,
    )
    if cache is not None and key in cache:
        return {**cache[key], "cached": True}

    messages = build_vision_messages(prompt, images, schema)
    first = provider.complete(
        messages=messages,
        model=model,
        temperature=float(settings.get("temperature", 0)),
        response_format={"type": "json_object"},
    )
    try:
        data = _decode(first, schema)
        result = first
    except ProviderSchemaError as first_error:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": first.content},
            {
                "role": "user",
                "content": f"Repair the JSON to satisfy the schema. Error: {first_error}",
            },
        ]
        result = provider.complete(
            messages=repair_messages,
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
        )
        try:
            data = _decode(result, schema)
        except ProviderSchemaError as exc:
            raise ProviderSchemaError(f"provider schema repair failed: {exc}") from exc

    output = {
        "data": data,
        "provenance": {
            "source": "vision",
            "model": result.model or model,
            "profile_snapshot": dict(profile_snapshot or {}),
            "prompt_version": prompt_version,
            "images": [
                _image_reference(image, position) for position, image in enumerate(images, start=1)
            ],
            "usage": result.usage,
            "cost": result.cost,
        },
        "cache_key": key,
        "cached": False,
    }
    if cache is not None:
        cache[key] = output
    return output
