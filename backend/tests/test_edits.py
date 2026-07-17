from app.models import CatalogItem, Run


def test_edit_is_audited_and_rejects_stale_version(client, db):
    run = Run(name="Edits")
    db.add(run)
    db.flush()
    item = CatalogItem(
        run_id=run.id,
        row_number=1,
        sku="SKU-1",
        data={"color": "blue"},
    )
    db.add(item)
    db.commit()

    edited = client.patch(
        f"/api/v1/review/items/{item.id}",
        json={"row_version": 1, "changes": {"color": "red"}},
    )
    assert edited.status_code == 200
    assert edited.json()["row_version"] == 2
    assert edited.json()["fields"]["color"] == "red"

    stale = client.patch(
        f"/api/v1/review/items/{item.id}",
        json={"row_version": 1, "changes": {"color": "green"}},
    )
    assert stale.status_code == 409
    audits = client.get(f"/api/v1/review/items/{item.id}/edits").json()
    assert audits[0]["old_value"] == "blue"
    assert audits[0]["new_value"] == "red"


def test_bulk_edit_checks_each_row_version(client, db):
    run = Run(name="Bulk")
    db.add(run)
    db.flush()
    items = [
        CatalogItem(run_id=run.id, row_number=index, data={"brand": "old"}) for index in (1, 2)
    ]
    db.add_all(items)
    db.commit()
    response = client.post(
        "/api/v1/review/bulk",
        json={
            "edits": [
                {
                    "item_id": str(item.id),
                    "row_version": item.row_version,
                    "changes": {"brand": "new"},
                }
                for item in items
            ]
        },
    )
    assert response.status_code == 200
    assert {row["fields"]["brand"] for row in response.json()} == {"new"}
