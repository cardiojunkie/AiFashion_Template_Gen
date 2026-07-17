from app.services.validation import validate_fields


def test_required_select_and_confidence_validation() -> None:
    attributes = [
        {"name": "color", "required": True, "value_list": {"Red": []}},
        {"name": "description", "required": True, "confidence_threshold": 0.8},
    ]
    issues = validate_fields(
        {"color": "Green", "description": "Short"},
        attributes,
        provenance={"description": {"confidence": 0.5}},
    )
    assert {issue["code"] for issue in issues} == {"invalid_select", "low_confidence"}
    assert next(issue for issue in issues if issue["code"] == "low_confidence")["blocking"] is False
