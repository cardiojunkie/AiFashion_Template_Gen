from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any


def parse_input_data(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    parsed: dict[str, object] = {}
    parts = re.split(r"[\n;]+", str(value or ""))
    parts = [item for part in parts for item in re.split(r"\|(?=\s*[^|:=]+\s*[:=])", part)]
    for part in parts:
        key, separator, item = part.partition(":")
        if not separator:
            key, separator, item = part.partition("=")
        if separator and key.strip():
            parsed[key.strip()] = item.strip()
    return parsed


def _lookup(data: Mapping[str, Any], names: Sequence[str]) -> Any:
    folded = {str(key).casefold(): value for key, value in data.items()}
    for name in names:
        value = folded.get(name.casefold())
        if value is not None and str(value).strip():
            return value
    return None


def _options(attribute: Mapping[str, Any]) -> dict[str, set[str]]:
    specification = attribute.get("value_list") or attribute.get("options") or []
    if isinstance(specification, Mapping) and "values" in specification:
        specification = specification["values"]
    options: dict[str, set[str]] = {}
    if isinstance(specification, Mapping):
        for canonical, aliases in specification.items():
            values = aliases if isinstance(aliases, (list, tuple, set)) else [aliases]
            options[str(canonical)] = {str(canonical), *(str(value) for value in values)}
    else:
        for item in specification:
            if isinstance(item, Mapping):
                canonical = str(
                    item.get("canonical_value")
                    or item.get("value")
                    or item.get("canonical")
                    or item.get("name")
                )
                aliases = item.get("aliases") or []
            else:
                canonical, aliases = str(item), []
            options[canonical] = {canonical, *(str(alias) for alias in aliases)}
    return options


def _canonical_value(
    value: object,
    attribute: Mapping[str, Any],
    *,
    fuzzy: bool,
) -> tuple[object, dict[str, object] | None]:
    options = _options(attribute)
    if not options:
        return value, None
    text = str(value).strip()
    exact = [
        canonical
        for canonical, terms in options.items()
        if text.casefold() in {term.casefold() for term in terms}
    ]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, {"code": "ambiguous_value", "value": text, "blocking": True}
    if fuzzy:
        scores = sorted(
            (
                max(
                    SequenceMatcher(None, text.casefold(), term.casefold()).ratio()
                    for term in terms
                ),
                canonical,
            )
            for canonical, terms in options.items()
        )
        scores.reverse()
        threshold = float(attribute.get("fuzzy_threshold", 0.85))
        if scores and scores[0][0] >= threshold:
            if len(scores) > 1 and scores[0][0] - scores[1][0] < 0.05:
                return None, {"code": "ambiguous_value", "value": text, "blocking": True}
            return scores[0][1], None
    return value, {"code": "invalid_value", "value": text, "blocking": True}


def _map_value(
    value: object,
    attribute: Mapping[str, Any],
    *,
    fuzzy: bool,
    delimiter: str,
) -> tuple[object, list[dict[str, object]]]:
    if not attribute.get("multiselect"):
        mapped, issue = _canonical_value(value, attribute, fuzzy=fuzzy)
        return mapped, [issue] if issue else []
    parts = value if isinstance(value, (list, tuple, set)) else str(value).split(delimiter)
    mapped_values: list[object] = []
    issues: list[dict[str, object]] = []
    for part in parts:
        if str(part).strip():
            mapped, issue = _canonical_value(part, attribute, fuzzy=fuzzy)
            if mapped is not None:
                mapped_values.append(mapped)
            if issue:
                issues.append(issue)
    return delimiter.join(map(str, mapped_values)), issues


def map_fields(
    row: Mapping[str, Any],
    attributes: Sequence[Mapping[str, Any]],
    *,
    parsed_input: Mapping[str, Any] | None = None,
    vision: Mapping[str, Any] | None = None,
    vision_provenance: Mapping[str, Any] | None = None,
    fuzzy: bool = False,
    multiselect_delimiter: str = "|",
) -> dict[str, Any]:
    """Apply the fixed direct > parsed > vision > default > blank priority."""
    parsed = dict(parsed_input or parse_input_data(row.get("input_data")))
    vision = vision or {}
    values: dict[str, object] = {}
    provenance: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, object]] = []

    for attribute in attributes:
        name = str(attribute.get("name") or attribute.get("key") or "")
        if not name:
            continue
        columns = [name, *(str(item) for item in attribute.get("direct_columns", []))]
        raw = _lookup(row, columns)
        source, confidence = "direct", 1.0
        if raw is None:
            raw = _lookup(parsed, [name, *(str(item) for item in attribute.get("aliases", []))])
            source, confidence = "input_data", 0.9
        if raw is None:
            raw = _lookup(vision, [name])
            source, confidence = "vision", 0.7
            if isinstance(raw, Mapping) and "value" in raw:
                confidence = float(raw.get("confidence", confidence))
                raw = raw["value"]
        if raw is None and attribute.get("default") is not None:
            raw = attribute["default"]
            source, confidence = "default", 1.0
        if raw is None:
            values[name] = None
            provenance[name] = {"source": "blank", "confidence": None}
            continue

        delimiter = str(attribute.get("delimiter") or multiselect_delimiter)
        mapped, field_issues = _map_value(
            raw,
            attribute,
            fuzzy=bool(attribute.get("fuzzy", fuzzy)),
            delimiter=delimiter,
        )
        values[name] = mapped
        provenance[name] = {"source": source, "confidence": confidence}
        if source == "vision":
            provenance[name].update(vision_provenance or {})
            provenance[name].update({"source": "vision", "confidence": confidence})
        issues.extend({**issue, "field": name} for issue in field_issues)
    return {"values": values, "provenance": provenance, "issues": issues}
