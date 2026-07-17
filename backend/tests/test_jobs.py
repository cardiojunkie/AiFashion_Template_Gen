import uuid
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import select

import app.worker as worker
from app.models import CatalogItem, Extraction, ImageJob, Run, RunTask
from app.services.providers import MockProvider


def test_dispatch_is_idempotent_per_base_code_group(client, db, session_factory, monkeypatch):
    created = client.post(
        "/api/v1/runs",
        json={
            "name": "Grouping",
            "items": [
                {"row_number": 2, "values": {"sku": "1", "base_code": " Shirt "}},
                {"row_number": 3, "values": {"sku": "2", "base_code": "shirt"}},
            ],
        },
    ).json()
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    queued: list[str] = []
    monkeypatch.setattr(worker.process_run_task, "delay", queued.append)

    first = worker.dispatch_run.run(created["id"])
    second = worker.dispatch_run.run(created["id"])
    assert first["queued"] == second["queued"] == 1
    tasks = db.scalars(select(RunTask)).all()
    assert len(tasks) == 1
    assert tasks[0].task_key == "group:shirt"
    assert len(tasks[0].payload["item_ids"]) == 2
    assert len(queued) == 2


def test_cancel_moves_run_to_terminal_state(client, db):
    created = client.post("/api/v1/runs", json={"name": "Cancel me", "items": []}).json()
    response = client.post(f"/api/v1/runs/{created['id']}/cancel")
    assert response.status_code == 202
    assert response.json()["status"] == "cancelled"
    assert db.get(Run, uuid.UUID(created["id"])).cancel_requested is True


def test_run_images_send_only_the_lowest_row_images_to_vision(db, monkeypatch):
    run = Run(name="Images", effective_config={})
    db.add(run)
    db.flush()
    later = CatalogItem(
        run_id=run.id,
        row_number=2,
        raw_data={"image_1": "https://example.test/later.jpg"},
        data={},
    )
    first = CatalogItem(
        run_id=run.id,
        row_number=1,
        raw_data={
            "image_2": "https://example.test/second.jpg",
            "image_1": "https://example.test/first.jpg",
        },
        data={},
    )
    db.add_all([later, first])
    db.flush()
    downloaded = []
    stored = {}

    def process(urls, *, store, downloader, max_pixels, retain_data):
        assert retain_data is False
        downloaded.extend(urls)
        return [
            {
                "url": url,
                "status": "ok",
                "storage_key": store(f"{position}.jpg", url.encode()),
                "checksum": f"checksum-{position}",
            }
            for position, url in enumerate(urls, 1)
        ]

    monkeypatch.setattr("app.services.image_downloader.process_image_urls", process)
    monkeypatch.setattr(
        "app.storage.put_bytes",
        lambda key, data, *_: stored.setdefault(key, data),
    )
    monkeypatch.setattr("app.storage.get_bytes", stored.__getitem__)

    images, checksums = worker._process_run_images(db, run, [later, first])

    assert set(downloaded) == {
        "https://example.test/later.jpg",
        "https://example.test/second.jpg",
        "https://example.test/first.jpg",
    }
    assert [image["reference"] for image in images] == [
        "https://example.test/first.jpg",
    ]
    assert len(checksums) == 1


def test_worker_reuses_persisted_vision_extraction(db, session_factory, monkeypatch):
    schema = {
        "type": "object",
        "properties": {"color": {"type": "string"}},
        "required": ["color"],
        "additionalProperties": False,
    }
    run = Run(
        name="Cached",
        status="running",
        effective_config={
            "attributes": [{"name": "color"}],
            "response_schema": schema,
            "model": "mock",
            "prompt_version": "1",
            "llm_profile": {"adapter": "mock"},
        },
        total_items=2,
    )
    db.add(run)
    db.flush()
    items = [CatalogItem(run_id=run.id, row_number=row, raw_data={}, data={}) for row in (1, 2)]
    db.add_all(items)
    db.flush()
    tasks = [
        RunTask(run_id=run.id, task_key=f"row:{item.id}", payload={"item_ids": [str(item.id)]})
        for item in items
    ]
    db.add_all(tasks)
    db.commit()

    provider = MockProvider({"color": "red"})
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "_provider_for_run", lambda *_: provider)
    monkeypatch.setattr(
        worker,
        "_process_run_images",
        lambda *_: ([{"url": "data:image/jpeg;base64,eA==", "reference": "image"}], ["sum"]),
    )

    assert worker._process_run_task(tasks[0].id)["ok"] is True
    assert worker._process_run_task(tasks[1].id)["ok"] is True
    db.expire_all()

    assert len(provider.calls) == 1
    assert len(db.scalars(select(Extraction)).all()) == 1
    assert db.get(CatalogItem, items[1].id).data["color"] == "red"


def test_standalone_image_job_uses_frozen_aliases_and_reports_cells(
    db, session_factory, monkeypatch
):
    workbook = Workbook()
    workbook.active.append(["Front image"])
    workbook.active.append(["https://example.test/front.jpg"])
    source = BytesIO()
    workbook.save(source)
    job = ImageJob(
        input_object_key="jobs/input.xlsx",
        result={"header_aliases": {"image_1": ["Front image"]}},
    )
    db.add(job)
    db.commit()
    stored = {}
    seen_urls = []

    def process(urls, *, store, downloader, max_pixels, retain_data):
        assert retain_data is False
        seen_urls.extend(urls)
        data = b"jpeg"
        return [
            {
                "position": 1,
                "url": urls[0],
                "status": "ok",
                "storage_key": store("front.jpg", data),
                "checksum": "checksum",
            }
        ]

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr("app.storage.get_bytes", lambda _: source.getvalue())
    monkeypatch.setattr(
        "app.storage.put_bytes",
        lambda key, data, content_type="application/octet-stream": stored.setdefault(key, data),
    )
    monkeypatch.setattr("app.services.image_downloader.process_image_urls", process)
    monkeypatch.setattr(
        "app.storage.copy_to_fileobj",
        lambda key, destination: destination.write(stored[key]),
    )
    monkeypatch.setattr(
        "app.storage.put_fileobj",
        lambda key, source, content_type: stored.setdefault(key, source.read()),
    )

    result = worker.process_image_job.run(str(job.id))

    assert result["ok"] is True
    assert result["total_rows"] == 1
    assert seen_urls == ["https://example.test/front.jpg"]
    report = stored[result["csv_key"]].decode("utf-8-sig")
    assert "row_number,column" in report
    assert "2,image_1" in report
