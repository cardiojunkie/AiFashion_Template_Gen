from __future__ import annotations

import json
from io import BytesIO

from openpyxl import load_workbook
from PIL import Image
from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    AttributeSet,
    HeaderDefinition,
    LLMProfile,
    MappingProfile,
    Prompt,
    PublishedSnapshot,
    ValueList,
    ValueListItem,
)
from app.services.templates import generate_template
from app.storage import ensure_bucket, put_bytes


def build_demo_assets() -> dict[str, bytes]:
    assets: dict[str, bytes] = {}
    for name, color in {"red-top.jpg": "#b91c1c", "blue-top.jpg": "#1d4ed8"}.items():
        output = BytesIO()
        Image.new("RGB", (240, 320), color).save(output, "JPEG", quality=90)
        assets[f"demo/images/{name}"] = output.getvalue()

    workbook = load_workbook(
        BytesIO(generate_template(["sku", "ean", "base_code", "category", "color"]))
    )
    workbook.active.append(["0001", "0000000000001", "DEMO-TOP", "tops", "rouge"])
    workbook.active.append(["0002", "0000000000002", "DEMO-TOP", "tops", "bleu"])
    output = BytesIO()
    workbook.save(output)
    assets["demo/catalog.xlsx"] = output.getvalue()
    return assets


def seed() -> dict[str, object]:
    assets = build_demo_assets()
    ensure_bucket()
    for key, data in assets.items():
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if key.endswith(".xlsx")
            else "image/jpeg"
        )
        put_bytes(key, data, content_type)

    created: list[str] = []
    with SessionLocal.begin() as session:
        headers = []
        for key, label, required in [
            ("sku", "SKU", True),
            ("ean", "EAN", False),
            ("base_code", "Base code", True),
            ("category", "Category", True),
            ("color", "Color", False),
        ]:
            header = session.scalar(select(HeaderDefinition).where(HeaderDefinition.key == key))
            if header is None:
                header = HeaderDefinition(
                    key=key,
                    label=label,
                    aliases=[],
                    required=required,
                    generated=False,
                )
                session.add(header)
                created.append(f"header:{key}")
            headers.append(header)

        value_list = session.scalar(select(ValueList).where(ValueList.name == "Demo Colors"))
        if value_list is None:
            value_list = ValueList(name="Demo Colors", description="Colors used by the local demo")
            session.add(value_list)
            session.flush()
            for canonical, aliases in {"Red": ["rouge"], "Blue": ["bleu"]}.items():
                session.add(
                    ValueListItem(
                        value_list_id=value_list.id,
                        canonical_value=canonical,
                        canonical_normalized=canonical.casefold(),
                        aliases=aliases,
                    )
                )
            created.append("value_list")

        attribute_set = session.scalar(select(AttributeSet).where(AttributeSet.name == "Demo Tops"))
        if attribute_set is None:
            attribute_set = AttributeSet(
                name="Demo Tops",
                attributes=[
                    {
                        "name": "color",
                        "required": True,
                        "value_list": "Demo Colors",
                    },
                    {
                        "name": "description",
                        "required": True,
                        "default": "Demo cotton top",
                    },
                ],
                assignment_rules=[{"attribute_set": "Demo Tops", "when": {"category": "tops"}}],
            )
            session.add(attribute_set)
            created.append("attribute_set")

        mapping_profile = session.scalar(
            select(MappingProfile).where(MappingProfile.name == "Demo Mapping")
        )
        if mapping_profile is None:
            mapping_profile = MappingProfile(
                name="Demo Mapping",
                mapping={"color": {"direct_columns": ["color"]}},
                fuzzy_matching=False,
                multiselect_delimiter="|",
            )
            session.add(mapping_profile)
            created.append("mapping_profile")

        prompt = session.scalar(select(Prompt).where(Prompt.name == "Demo Vision Prompt"))
        if prompt is None:
            prompt = Prompt(
                name="Demo Vision Prompt",
                text="Describe the garment and identify its color.",
                response_schema={
                    "type": "object",
                    "required": ["description"],
                    "properties": {"description": {"type": "string"}},
                },
            )
            session.add(prompt)
            created.append("prompt")

        llm_profile = session.scalar(
            select(LLMProfile).where(LLMProfile.name == "Demo Mock Provider")
        )
        if llm_profile is None:
            llm_profile = LLMProfile(
                name="Demo Mock Provider",
                adapter="mock",
                model_name="mock",
                endpoint_url=None,
                options={"response": {"description": "Demo cotton top"}},
                encrypted_api_key=None,
            )
            session.add(llm_profile)
            created.append("llm_profile")

        session.flush()
        value_items = session.scalars(
            select(ValueListItem).where(ValueListItem.value_list_id == value_list.id)
        ).all()
        resources = [
            *(
                (
                    "headers",
                    header,
                    {
                        "key": header.key,
                        "label": header.label,
                        "aliases": header.aliases,
                        "required": header.required,
                        "generated": header.generated,
                    },
                )
                for header in headers
            ),
            (
                "value-lists",
                value_list,
                {
                    "name": value_list.name,
                    "items": [
                        {
                            "canonical_value": item.canonical_value,
                            "aliases": item.aliases,
                        }
                        for item in value_items
                    ],
                },
            ),
            (
                "attribute-sets",
                attribute_set,
                {
                    "name": attribute_set.name,
                    "attributes": attribute_set.attributes,
                    "assignment_rules": attribute_set.assignment_rules,
                },
            ),
            (
                "mapping-profiles",
                mapping_profile,
                {
                    "name": mapping_profile.name,
                    "mapping": mapping_profile.mapping,
                    "fuzzy_matching": mapping_profile.fuzzy_matching,
                    "multiselect_delimiter": mapping_profile.multiselect_delimiter,
                },
            ),
            (
                "prompts",
                prompt,
                {
                    "name": prompt.name,
                    "text": prompt.text,
                    "response_schema": prompt.response_schema,
                },
            ),
            (
                "llm-profiles",
                llm_profile,
                {
                    "name": llm_profile.name,
                    "adapter": llm_profile.adapter,
                    "model_name": llm_profile.model_name,
                    "endpoint_url": llm_profile.endpoint_url,
                    "options": llm_profile.options,
                },
            ),
        ]
        published = 0
        for resource_type, resource, payload in resources:
            if (
                session.scalar(
                    select(PublishedSnapshot).where(
                        PublishedSnapshot.resource_type == resource_type,
                        PublishedSnapshot.source_id == resource.id,
                    )
                )
                is None
            ):
                session.add(
                    PublishedSnapshot(
                        resource_type=resource_type,
                        source_id=resource.id,
                        version=1,
                        payload=payload,
                    )
                )
                published += 1
        if published:
            created.append(f"snapshots:{published}")

    return {"ok": True, "created": created, "objects": sorted(assets)}


if __name__ == "__main__":
    print(json.dumps(seed(), sort_keys=True))
