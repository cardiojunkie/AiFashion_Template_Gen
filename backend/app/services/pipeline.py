from __future__ import annotations

import hashlib
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from app.services.grouping import row_values
from app.services.mapping import map_fields
from app.services.providers import Provider
from app.services.validation import validate_fields
from app.services.vision import ProviderSchemaError, extract_vision


def enrich_group(
    group: Mapping[str, Any],
    attributes: Sequence[Mapping[str, Any]],
    *,
    provider: Provider | None = None,
    prompt: str = "Extract catalog attributes from the supplied images.",
    prompt_version: str = "1",
    schema: Mapping[str, Any] | None = None,
    model: str = "mock",
    model_settings: Mapping[str, Any] | None = None,
    profile_snapshot: Mapping[str, Any] | None = None,
    images: Sequence[str | Mapping[str, Any]] | None = None,
    image_checksums: Sequence[str] | None = None,
    cache: MutableMapping[str, dict[str, Any]] | None = None,
    fuzzy: bool = False,
) -> dict[str, Any]:
    rows = list(group.get("rows") or [])
    merged: dict[str, Any] = {}
    for row in sorted(rows, key=lambda item: int(item.get("row_number") or 0)):
        for key, value in row_values(row).items():
            if key not in merged and value is not None and str(value).strip():
                merged[key] = value

    if images is None:
        representative = group.get("representative_image")
        images = [str(representative)] if representative else []
    checksums = list(image_checksums or [])
    if not checksums:
        checksums = [hashlib.sha256(str(image).encode()).hexdigest() for image in images]

    vision_data: Mapping[str, Any] = {}
    vision_provenance: Mapping[str, Any] = {}
    provider_issues: list[dict[str, Any]] = []
    if provider and images and schema:
        try:
            extraction = extract_vision(
                provider,
                prompt=prompt,
                prompt_version=prompt_version,
                images=images,
                image_checksums=checksums,
                schema=schema,
                model=model,
                model_settings=model_settings,
                profile_snapshot=profile_snapshot,
                cache=cache,
            )
            vision_data = extraction["data"]
            vision_provenance = extraction["provenance"]
        except ProviderSchemaError as exc:
            provider_issues.append(
                {
                    "code": "provider_schema_error",
                    "field": None,
                    "message": str(exc),
                    "blocking": True,
                }
            )
        except Exception as exc:  # Keep a provider outage isolated to its group.
            provider_issues.append(
                {
                    "code": "provider_error",
                    "field": None,
                    "message": type(exc).__name__,
                    "blocking": True,
                }
            )

    mapped = map_fields(
        merged,
        attributes,
        vision=vision_data,
        vision_provenance=vision_provenance,
        fuzzy=fuzzy,
    )
    issues = validate_fields(
        mapped["values"],
        attributes,
        provenance=mapped["provenance"],
        existing_issues=[*provider_issues, *mapped["issues"]],
    )
    return {
        "base_code": group.get("base_code"),
        "values": mapped["values"],
        "provenance": mapped["provenance"],
        "issues": issues,
        "valid": not any(issue.get("blocking") for issue in issues),
    }
