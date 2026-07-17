from io import BytesIO

from openpyxl import load_workbook
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.demo.seed as seed_module
from app.database import Base
from app.demo.seed import build_demo_assets
from app.demo.verify_workflow import verify_workflow


def test_demo_assets_and_workflow_are_provider_free() -> None:
    assets = build_demo_assets()
    assert len(load_workbook(BytesIO(assets["demo/catalog.xlsx"])).active["A"]) == 3
    with Image.open(BytesIO(assets["demo/images/red-top.jpg"])) as image:
        assert image.size == (240, 320)
    assert verify_workflow()["ok"] is True


def test_seed_is_opt_in_and_idempotent(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'demo.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(seed_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(seed_module, "ensure_bucket", lambda: None)
    monkeypatch.setattr(seed_module, "put_bytes", lambda *args: None)

    created = set(seed_module.seed()["created"])
    assert {
        "value_list",
        "attribute_set",
        "mapping_profile",
        "prompt",
        "llm_profile",
    } <= created
    assert {f"header:{key}" for key in ["sku", "ean", "base_code", "category", "color"]} <= created
    assert any(value.startswith("snapshots:") for value in created)
    assert seed_module.seed()["created"] == []
