import uuid
from datetime import UTC, datetime

import app.main as api_main
from app.main import create_run, resolve_run_configuration
from app.models import (
    AppSetting,
    LLMProfile,
    PreflightRecord,
    Prompt,
    PublishedSnapshot,
    Run,
)
from app.schemas import RunCreate


def test_published_snapshot_does_not_change_with_draft(client):
    prompt = client.post(
        "/api/v1/config/prompts",
        json={"name": "Vision", "text": "Original", "response_schema": {}},
    ).json()
    snapshot = client.post(f"/api/v1/config/prompts/{prompt['id']}/publish").json()
    assert snapshot["version"] == 1

    assert (
        client.patch(f"/api/v1/config/prompts/{prompt['id']}", json={"text": "Changed"}).status_code
        == 200
    )
    snapshots = client.get(f"/api/v1/config/prompts/{prompt['id']}/snapshots").json()
    assert snapshots[0]["payload"]["text"] == "Original"


def test_llm_snapshot_contains_no_secret(client):
    profile = client.post(
        "/api/v1/config/llm-profiles",
        json={
            "name": "Mock",
            "adapter": "mock",
            "model_name": "mock",
            "api_key": "super-secret",
        },
    ).json()
    snapshot = client.post(f"/api/v1/config/llm-profiles/{profile['id']}/publish").json()
    serialized = str(snapshot).casefold()
    assert "super-secret" not in serialized
    assert "encrypted_api_key" not in serialized


def test_default_run_config_merges_live_published_and_requested_settings(db):
    source_id = uuid.uuid4()
    older = PublishedSnapshot(
        resource_type="settings",
        source_id=source_id,
        version=1,
        payload={"key": "max_image_pixels", "value": 20},
    )
    latest = PublishedSnapshot(
        resource_type="settings",
        source_id=source_id,
        version=2,
        payload={"key": "max_image_pixels", "value": 30},
    )
    db.add_all(
        [
            AppSetting(id=source_id, key="max_image_pixels", value=10),
            AppSetting(key="fuzzy_matching", value=True),
            AppSetting(key="multiselect_delimiter", value=";"),
            older,
            latest,
        ]
    )
    db.commit()

    snapshot_ids, config = resolve_run_configuration(
        db, {}, {"app_settings": {"max_image_pixels": 40, "custom": True}}
    )

    assert snapshot_ids == {f"settings:{source_id}": str(latest.id)}
    assert config["app_settings"] == {
        "max_image_pixels": 40,
        "fuzzy_matching": True,
        "multiselect_delimiter": ";",
        "custom": True,
    }
    assert config["fuzzy_matching"] is True
    assert config["multiselect_delimiter"] == ";"


def test_prompt_version_is_unique_across_prompt_records(db):
    source_id = uuid.uuid4()
    prompt = Prompt(
        id=source_id,
        name="Extract",
        text="Extract",
        response_schema={},
    )
    snapshot = PublishedSnapshot(
        resource_type="prompts",
        source_id=source_id,
        version=1,
        payload={"text": "Extract", "response_schema": {}},
    )
    db.add_all([prompt, snapshot])
    db.commit()

    _, config = resolve_run_configuration(db, {}, {})

    assert config["prompt_version"] == f"{source_id}:1"


def test_default_resolution_skips_archived_llm_profiles(db):
    profile = LLMProfile(
        name="Retired",
        adapter="mock",
        model_name="mock",
        endpoint_url=None,
        options={},
        encrypted_api_key=None,
        archived_at=datetime.now(UTC),
    )
    db.add(profile)
    db.flush()
    snapshot = PublishedSnapshot(
        resource_type="llm-profiles",
        source_id=profile.id,
        version=1,
        payload={"id": str(profile.id), "adapter": "mock", "model_name": "mock"},
    )
    db.add(snapshot)
    db.commit()

    default_ids, default_config = resolve_run_configuration(db, {}, {})
    explicit_ids, explicit_config = resolve_run_configuration(db, {"profile": snapshot.id}, {})

    assert default_ids == {}
    assert "llm_profile" not in default_config
    assert explicit_ids == {"profile": str(snapshot.id)}
    assert explicit_config["llm_profile"]["adapter"] == "mock"


def test_preflight_pin_survives_a_later_publish(db, monkeypatch):
    prompt = Prompt(name="Catalog", text="Draft", response_schema={})
    db.add(prompt)
    db.flush()
    first = PublishedSnapshot(
        resource_type="prompts",
        source_id=prompt.id,
        version=1,
        payload={"text": "Original", "response_schema": {}},
    )
    db.add(first)
    db.commit()
    snapshot_ids, config = resolve_run_configuration(db, {}, {})
    record = PreflightRecord(
        filename="catalog.xlsx",
        input_object_key="preflights/catalog.xlsx",
        result={
            "valid": True,
            "snapshot_ids": snapshot_ids,
            "effective_config": config,
        },
        items=[],
    )
    db.add(record)
    db.flush()
    db.add(
        PublishedSnapshot(
            resource_type="prompts",
            source_id=prompt.id,
            version=2,
            payload={"text": "Changed", "response_schema": {}},
        )
    )
    db.commit()
    monkeypatch.setattr(api_main, "enqueue", lambda *_: "job")

    created = create_run(RunCreate(preflight_id=record.id), db)
    run = db.get(Run, created["id"])

    assert run.snapshot_ids == snapshot_ids
    assert run.effective_config["prompt"] == "Original"
