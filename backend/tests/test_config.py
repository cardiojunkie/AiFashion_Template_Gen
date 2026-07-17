import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import import_value_lists, update_config, update_settings_api
from app.models import LLMProfile, Prompt
from app.settings import Settings


def test_header_aliases_are_unique(client):
    first = client.post(
        "/api/v1/config/headers",
        json={"key": "sku", "label": "SKU", "aliases": ["Stock code"]},
    )
    assert first.status_code == 201
    duplicate = client.post(
        "/api/v1/config/headers",
        json={"key": "product", "label": "Product", "aliases": [" stock CODE "]},
    )
    assert duplicate.status_code == 409


def test_production_rejects_example_secrets():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            database_url="postgresql://change-me@db/catalog",
            s3_access_key="minioadmin",
            s3_secret_key="change-me",
            secret_key="change-me",
            encryption_key="change-me",
        )


def test_production_rejects_sqlite_even_with_real_secrets():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            database_url="sqlite:///production.db",
            s3_access_key="catalog-prod",
            s3_secret_key="strong-storage-secret",
            secret_key="strong-application-secret",
            encryption_key="strong-encryption-secret",
        )


def test_public_settings_snapshot_omits_credentials():
    snapshot = Settings().public_snapshot()
    assert not {
        "database_url",
        "redis_url",
        "s3_access_key",
        "s3_secret_key",
        "secret_key",
        "encryption_key",
    }.intersection(snapshot)


def test_config_patch_rejects_invalid_nested_shapes(db):
    prompt = Prompt(name="Prompt", text="Extract", response_schema={})
    db.add(prompt)
    db.commit()

    with pytest.raises(HTTPException) as caught:
        update_config("prompts", prompt.id, {"response_schema": []}, db)

    assert caught.value.status_code == 422
    db.refresh(prompt)
    assert prompt.response_schema == {}


def test_llm_patch_rejects_null_options(db):
    profile = LLMProfile(
        name="Mock",
        adapter="mock",
        model_name="mock",
        endpoint_url=None,
        options={},
        encrypted_api_key=None,
    )
    db.add(profile)
    db.commit()

    with pytest.raises(HTTPException) as caught:
        update_config("llm-profiles", profile.id, {"options": None}, db)

    assert caught.value.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [{"retention_days": "abc"}, {"fuzzy_matching": "yes"}],
)
def test_settings_patch_rejects_wrong_json_types(db, payload):
    with pytest.raises(HTTPException) as caught:
        update_settings_api(payload, db)

    assert caught.value.status_code == 422


def test_value_list_import_rejects_blank_canonical_values(db):
    class Upload:
        filename = "values.csv"

        async def read(self, size):
            return b"canonical_value,aliases\n,blank\n"

    with pytest.raises(HTTPException) as caught:
        asyncio.run(import_value_lists(Upload(), db))

    assert caught.value.status_code == 422
