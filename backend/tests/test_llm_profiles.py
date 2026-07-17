import uuid

import pytest
from fastapi import HTTPException

import app.main as api_main
import app.worker as worker
from app.models import AppSetting, LLMProfile
from app.security import decrypt_secret, validate_llm_endpoint
from app.settings import Settings


def test_llm_secret_is_write_only_and_encrypted(client, db):
    response = client.post(
        "/api/v1/config/llm-profiles",
        json={
            "name": "Local mock",
            "adapter": "mock",
            "model_name": "mock",
            "api_key": "provider-key",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["has_api_key"] is True
    assert "api_key" not in body

    profile = db.get(LLMProfile, uuid.UUID(body["id"]))
    assert profile.encrypted_api_key != b"provider-key"
    assert decrypt_secret(profile.encrypted_api_key) == "provider-key"
    profiles = client.get("/api/v1/config/llm-profiles").json()
    assert all("api_key" not in item for item in profiles)


def test_llm_options_cannot_smuggle_secrets(client):
    response = client.post(
        "/api/v1/config/llm-profiles",
        json={
            "name": "Bad",
            "adapter": "mock",
            "model_name": "mock",
            "options": {"api_key": "leak"},
        },
    )
    assert response.status_code == 422
    assert "leak" not in response.text


def resolver(address: str):
    return lambda host, port, **kwargs: [(2, 1, 6, "", (address, port))]


def test_private_llm_endpoints_require_an_explicit_allowlist():
    with pytest.raises(ValueError, match="allowlisted"):
        validate_llm_endpoint("https://127.0.0.1/v1", Settings(), resolver("127.0.0.1"))

    allowed = Settings(
        allowed_private_llm_hosts=["llm.internal"], allow_insecure_llm_endpoints=True
    )
    validate_llm_endpoint("https://llm.internal/v1", allowed, resolver("10.0.0.8"))
    validate_llm_endpoint("http://llm.internal/v1", allowed, resolver("10.0.0.8"))


def test_connection_tests_and_runs_share_database_private_host_settings(db):
    db.add(AppSetting(key="private_llm_hosts", value=["llm.internal"]))
    db.commit()

    runtime = worker._llm_runtime_settings(db)

    assert runtime.allowed_private_llm_hosts == ["llm.internal"]
    assert runtime.allow_insecure_llm_endpoints is True


def test_pinned_null_endpoint_does_not_fall_through_to_live_draft(db, monkeypatch):
    profile = LLMProfile(
        name="Pinned",
        adapter="litellm",
        model_name="model",
        endpoint_url="https://live.example.test/v1",
        options={},
        encrypted_api_key=None,
    )
    db.add(profile)
    db.commit()
    captured = {}

    class Provider:
        def __init__(self, *, api_key=None, api_base=None):
            captured.update(api_key=api_key, api_base=api_base)

    monkeypatch.setattr("app.services.providers.LiteLLMProvider", Provider)

    worker._provider_for_run(
        db,
        {
            "llm_profile_id": str(profile.id),
            "llm_profile": {
                "adapter": "litellm",
                "endpoint_url": None,
                "options": {},
            },
        },
    )

    assert captured["api_base"] is None


def test_openai_compatible_endpoint_cannot_be_cleared(db):
    profile = LLMProfile(
        name="Compatible",
        adapter="openai-compatible",
        model_name="model",
        endpoint_url="https://provider.example.test/v1",
        options={},
        encrypted_api_key=None,
    )
    db.add(profile)
    db.commit()

    with pytest.raises(HTTPException) as caught:
        api_main.update_config("llm-profiles", profile.id, {"endpoint_url": None}, db)

    assert caught.value.status_code == 422
    assert db.get(LLMProfile, profile.id).endpoint_url == "https://provider.example.test/v1"
