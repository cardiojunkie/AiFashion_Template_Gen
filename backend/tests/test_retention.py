from datetime import UTC, datetime, timedelta

import app.worker as worker
from app.main import delete_run
from app.models import AppSetting, ExportJob, ImageAsset, PreflightRecord, Run


def test_soft_deleted_run_can_be_restored(client):
    run = client.post("/api/v1/runs", json={"name": "Restore", "items": []}).json()
    assert client.delete(f"/api/v1/runs/{run['id']}").status_code == 204
    restored = client.post(f"/api/v1/runs/{run['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None


def test_expired_soft_delete_is_purged(db, session_factory, monkeypatch):
    run = Run(
        name="Expired",
        deleted_at=datetime.now(UTC) - timedelta(days=31),
        purge_after=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(run)
    db.commit()
    run_id = run.id
    monkeypatch.setattr(worker, "SessionLocal", session_factory)

    assert worker.purge_deleted_runs.run() == {"purged": 1}
    db.expire_all()
    assert db.get(Run, run_id) is None


def test_soft_delete_uses_live_retention_setting(db):
    run = Run(name="Retention")
    db.add_all([run, AppSetting(key="retention_days", value=7)])
    db.commit()

    delete_run(run.id, db)

    assert 6.99 < (run.purge_after - run.deleted_at).total_seconds() / 86_400 < 7.01


def test_purge_deletes_owned_objects_but_keeps_shared_inputs(db, session_factory, monkeypatch):
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    shared = Run(
        name="Expired shared",
        input_object_key="inputs/shared.xlsx",
        deleted_at=datetime.now(UTC),
        purge_after=expired_at,
    )
    unique = Run(
        name="Expired unique",
        input_object_key="inputs/unique.xlsx",
        deleted_at=datetime.now(UTC),
        purge_after=expired_at,
    )
    active = Run(name="Active", input_object_key="inputs/shared.xlsx")
    db.add_all([shared, unique, active])
    db.flush()
    record = PreflightRecord(
        filename="unique.xlsx",
        input_object_key="inputs/unique.xlsx",
        result={},
        items=[],
        consumed_at=datetime.now(UTC),
    )
    db.add_all(
        [
            ImageAsset(
                run_id=shared.id,
                object_key="runs/shared/image.jpg",
                checksum="sum",
            ),
            ExportJob(
                run_id=shared.id,
                format="csv",
                object_key="exports/shared.csv",
            ),
            record,
        ]
    )
    db.commit()
    active_id = active.id
    record_id = record.id
    deleted = []
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr("app.storage.delete_keys", deleted.extend)

    assert worker.purge_deleted_runs.run() == {"purged": 2}

    assert set(deleted) == {
        "runs/shared/image.jpg",
        "exports/shared.csv",
        "inputs/unique.xlsx",
    }
    assert "inputs/shared.xlsx" not in deleted
    db.expire_all()
    assert db.get(Run, active_id) is not None
    assert db.get(PreflightRecord, record_id) is None


def test_purge_never_deletes_an_unproven_run_input_key(db, session_factory, monkeypatch):
    run = Run(
        name="Untrusted key",
        input_object_key="image-jobs/victim/input.xlsx",
        deleted_at=datetime.now(UTC),
        purge_after=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(run)
    db.commit()
    deleted = []
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr("app.storage.delete_keys", deleted.extend)

    assert worker.purge_deleted_runs.run() == {"purged": 1}
    assert deleted == []
