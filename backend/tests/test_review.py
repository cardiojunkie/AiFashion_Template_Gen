from app.models import CatalogItem, Run


def test_review_is_server_paginated(client, db):
    run = Run(name="Review", status="completed", total_items=5)
    db.add(run)
    db.flush()
    db.add_all(
        CatalogItem(
            run_id=run.id,
            row_number=index,
            sku=f"SKU-{index}",
            base_code=f"B-{index}",
            data={"color": "blue"},
        )
        for index in range(1, 6)
    )
    db.commit()

    page = client.get(f"/api/v1/runs/{run.id}/review?page=2&page_size=2").json()
    assert page["total"] == 5
    assert page["pages"] == 3
    assert [item["row_number"] for item in page["items"]] == [3, 4]
    assert page["items"][0]["fields"] == {"color": "blue"}


def test_review_search_does_not_load_unmatched_rows(client, db):
    run = Run(name="Search")
    db.add(run)
    db.flush()
    db.add_all(
        [
            CatalogItem(run_id=run.id, row_number=1, sku="MATCH", data={}),
            CatalogItem(run_id=run.id, row_number=2, sku="OTHER", data={}),
        ]
    )
    db.commit()
    result = client.get(f"/api/v1/runs/{run.id}/review?search=MATCH").json()
    assert result["total"] == 1
    assert result["items"][0]["sku"] == "MATCH"
