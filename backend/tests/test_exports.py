from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import load_workbook
from sqlalchemy import select

import app.main as api_main
import app.storage as storage
import app.worker as worker
from app.models import CatalogItem, ExportJob, ImageAsset, Run, ValidationIssue
from app.schemas import ExportCreate
from app.services.exports import export_csv, export_images_zip, export_xlsx


def test_exports_protect_formulas_and_preserve_identifiers_as_text() -> None:
    rows = [{"sku": "00001", "ean": "001234", "title": "=2+2"}]
    csv_output = export_csv(rows)
    assert b"'=2+2" in csv_output and b"'00001" in csv_output
    sheet = load_workbook(BytesIO(export_xlsx(rows))).active
    assert sheet["A2"].value == "00001" and sheet["A2"].number_format == "@"
    assert sheet["C2"].value == "'=2+2"


def test_blockers_require_override_and_zip_names_are_safe() -> None:
    issues = [{"blocking": True}]
    with pytest.raises(ValueError, match="override"):
        export_csv([], issues=issues)
    assert export_csv([], issues=issues, override_blocking=True)
    archive = export_images_zip({"../../look.jpg": b"x"})
    assert archive.startswith(b"PK")


@pytest.mark.parametrize(
    ("run_status", "total", "completed", "failed", "item_status"),
    [
        ("running", 1, 0, 0, "pending"),
        ("failed", 1, 0, 1, "failed"),
        ("completed", 2, 1, 0, "pending"),
        ("completed", 1, 0, 1, "failed"),
    ],
)
def test_export_blocks_incomplete_or_failed_runs_and_items(
    db, run_status, total, completed, failed, item_status
) -> None:
    run = Run(
        name="Blocked",
        status=run_status,
        total_items=total,
        completed_items=completed,
        failed_items=failed,
    )
    db.add(run)
    db.flush()
    db.add(CatalogItem(run_id=run.id, row_number=1, status=item_status, data={}))
    db.commit()

    with pytest.raises(HTTPException) as caught:
        api_main.queue_export(run.id, ExportCreate(format="csv"), db)

    assert caught.value.status_code == 409
    assert caught.value.detail["error"]["code"] == "export_blocked"


def test_export_override_is_persisted_with_actor(db, monkeypatch) -> None:
    run = Run(name="Partial", status="failed", total_items=1, failed_items=1)
    db.add(run)
    db.flush()
    db.add(CatalogItem(run_id=run.id, row_number=1, status="failed", data={}))
    db.commit()

    monkeypatch.setattr(api_main, "enqueue", lambda *_: "test-job")
    response = api_main.queue_export(
        run.id,
        ExportCreate(format="csv", override_blocking=True, actor="reviewer@example.test"),
        db,
    )

    assert response["job_id"] == "test-job"
    job = db.scalar(select(ExportJob).where(ExportJob.run_id == run.id))
    assert job and job.override_blocking is True
    assert job.override_actor == "reviewer@example.test"


def test_image_export_streams_objects_into_a_disk_backed_zip(
    db, session_factory, monkeypatch
) -> None:
    run = Run(name="Images", status="completed", total_items=1, completed_items=1)
    db.add(run)
    db.flush()
    item = CatalogItem(run_id=run.id, row_number=1, status="ready", data={})
    db.add(item)
    db.flush()
    assets = [
        ImageAsset(
            run_id=run.id,
            item_id=item.id,
            object_key=f"runs/{run.id}/images/{name}",
            checksum=name,
        )
        for name in ("front.jpg", "back.jpg")
    ]
    job = ExportJob(run_id=run.id, format="image_zip", include_images=True)
    db.add_all([*assets, job])
    db.commit()
    uploaded = {}
    copied = []

    def copy_to_fileobj(key, destination):
        copied.append(key)
        destination.write(key.rsplit("/", 1)[-1].encode())

    def put_fileobj(key, source, content_type):
        uploaded.update(key=key, data=source.read(), content_type=content_type)

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr("app.storage.copy_to_fileobj", copy_to_fileobj)
    monkeypatch.setattr("app.storage.put_fileobj", put_fileobj)
    monkeypatch.setattr(
        "app.storage.get_bytes",
        lambda *_: pytest.fail("image exports must not load objects wholesale"),
    )

    assert worker._create_export(job.id)["ok"] is True
    assert set(copied) == {asset.object_key for asset in assets}
    assert uploaded["content_type"] == "application/zip"
    with ZipFile(BytesIO(uploaded["data"])) as archive:
        assert sorted(archive.read(name) for name in archive.namelist()) == [
            b"back.jpg",
            b"front.jpg",
        ]


def test_worker_rechecks_export_blockers_after_queue(db, session_factory, monkeypatch) -> None:
    run = Run(name="Changed", status="completed", total_items=1, completed_items=1)
    db.add(run)
    db.flush()
    item = CatalogItem(run_id=run.id, row_number=1, status="ready", data={})
    db.add(item)
    db.flush()
    job = ExportJob(run_id=run.id, format="csv")
    db.add(job)
    db.flush()
    db.add(
        ValidationIssue(
            item_id=item.id,
            code="late_error",
            message="added after queueing",
            severity="error",
        )
    )
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", session_factory)

    result = worker._create_export(job.id)

    assert result["ok"] is False
    assert result["error"] == "export_blocked"
    db.expire_all()
    assert db.get(ExportJob, job.id).status == "failed"


def test_storage_and_download_response_stream_in_chunks(db, monkeypatch) -> None:
    class Body:
        def __init__(self):
            self.data = bytearray(b"abcdefgh")
            self.closed = False

        def read(self, size):
            chunk = bytes(self.data[:size])
            del self.data[:size]
            return chunk

        def close(self):
            self.closed = True

    body = Body()

    class Client:
        def get_object(self, **_):
            return {"Body": body}

    monkeypatch.setattr(storage, "client", Client)
    assert list(storage.iter_bytes("key", 3)) == [b"abc", b"def", b"gh"]
    assert body.closed is True

    run = Run(name="Download", status="completed")
    db.add(run)
    db.flush()
    job = ExportJob(
        run_id=run.id,
        status="completed",
        format="xlsx",
        include_images=True,
        object_key=f"exports/{run.id}.zip",
    )
    db.add(job)
    db.commit()

    response = api_main.download_export(job.id, db)
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "application/zip"
