from app.services.grouping import group_rows, select_representative_image


def test_grouping_rejects_mixed_attribute_sets_within_base_code() -> None:
    rows = [
        {
            "row_number": 3,
            "values": {"base_code": "A", "_attribute_set": "tops"},
            "images": ["b"],
        },
        {
            "row_number": 2,
            "values": {"base_code": "A", "_attribute_set": "shoes"},
            "images": ["a"],
        },
    ]
    groups, issues = group_rows(rows)
    assert groups[0]["valid"] is False
    assert issues[0]["code"] == "mixed_attribute_sets"
    assert select_representative_image(rows) == "a"


def test_grouping_normalizes_base_code_case() -> None:
    rows = [
        {"values": {"base_code": "Style", "_attribute_set": "tops"}},
        {"values": {"base_code": "STYLE", "_attribute_set": "shoes"}},
    ]
    groups, issues = group_rows(rows)
    assert len(groups) == 1
    assert issues[0]["code"] == "mixed_attribute_sets"
