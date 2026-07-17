from app.services.mapping import map_fields

ATTRIBUTES = [
    {
        "name": "color",
        "aliases": ["Colour"],
        "value_list": {"Red": ["rouge"], "Blue": ["bleu"]},
    }
]


def test_direct_values_outrank_parsed_and_vision_and_alias_map() -> None:
    result = map_fields(
        {"color": "rouge", "input_data": "Colour: bleu"},
        ATTRIBUTES,
        vision={"color": "Blue"},
    )
    assert result["values"] == {"color": "Red"}
    assert result["provenance"]["color"]["source"] == "direct"


def test_ambiguous_fuzzy_value_is_rejected() -> None:
    attributes = [{"name": "fit", "value_list": {"AB": [], "AC": []}, "fuzzy_threshold": 0.5}]
    result = map_fields({"fit": "A"}, attributes, fuzzy=True)
    assert result["values"]["fit"] is None
    assert result["issues"][0]["code"] == "ambiguous_value"


def test_published_value_list_item_shape_maps_alias() -> None:
    attributes = [
        {
            "name": "color",
            "value_list": [{"canonical_value": "Red", "aliases": ["rouge"]}],
        }
    ]
    assert map_fields({"color": "rouge"}, attributes)["values"]["color"] == "Red"
