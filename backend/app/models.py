import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ArchiveMixin:
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))


class SystemMetadata(TimestampMixin, Base):
    __tablename__ = "system_metadata"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class HeaderDefinition(UUIDMixin, TimestampMixin, ArchiveMixin, Base):
    __tablename__ = "header_definitions"

    key: Mapped[str] = mapped_column(String(100), unique=True)
    label: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    generated: Mapped[bool] = mapped_column(Boolean, default=False)


class ValueList(UUIDMixin, TimestampMixin, ArchiveMixin, Base):
    __tablename__ = "value_lists"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class ValueListItem(UUIDMixin, TimestampMixin, ArchiveMixin, Base):
    __tablename__ = "value_list_items"
    __table_args__ = (UniqueConstraint("value_list_id", "canonical_normalized"),)

    value_list_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("value_lists.id", ondelete="CASCADE"), index=True
    )
    canonical_value: Mapped[str] = mapped_column(String(500))
    canonical_normalized: Mapped[str] = mapped_column(String(500))
    aliases: Mapped[list] = mapped_column(JSON, default=list)


class AttributeSet(UUIDMixin, TimestampMixin, ArchiveMixin, Base):
    __tablename__ = "attribute_sets"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    attributes: Mapped[list] = mapped_column(JSON, default=list)
    assignment_rules: Mapped[list] = mapped_column(JSON, default=list)


class Prompt(UUIDMixin, TimestampMixin, ArchiveMixin, Base):
    __tablename__ = "prompts"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    text: Mapped[str] = mapped_column(Text)
    response_schema: Mapped[dict] = mapped_column(JSON, default=dict)


class MappingProfile(UUIDMixin, TimestampMixin, ArchiveMixin, Base):
    __tablename__ = "mapping_profiles"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    fuzzy_matching: Mapped[bool] = mapped_column(Boolean, default=False)
    multiselect_delimiter: Mapped[str] = mapped_column(String(10), default="|")


class AppSetting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class LLMProfile(UUIDMixin, TimestampMixin, ArchiveMixin, Base):
    __tablename__ = "llm_profiles"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    adapter: Mapped[str] = mapped_column(String(40))
    model_name: Mapped[str] = mapped_column(String(200))
    endpoint_url: Mapped[str | None] = mapped_column(String(1000))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary)


class PublishedSnapshot(UUIDMixin, Base):
    __tablename__ = "published_snapshots"
    __table_args__ = (
        UniqueConstraint("resource_type", "source_id", "version"),
        Index("ix_snapshot_resource", "resource_type", "source_id"),
    )

    resource_type: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PreflightRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "preflight_records"

    filename: Mapped[str] = mapped_column(String(500))
    input_object_key: Mapped[str | None] = mapped_column(String(1000))
    result: Mapped[dict] = mapped_column(JSON)
    items: Mapped[list] = mapped_column(JSON, default=list)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImageJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "image_jobs"

    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    input_object_key: Mapped[str | None] = mapped_column(String(1000))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Run(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "runs"

    name: Mapped[str] = mapped_column(String(250))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    input_object_key: Mapped[str | None] = mapped_column(String(1000))
    preflight: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    effective_config: Mapped[dict] = mapped_column(JSON, default=dict)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "run_tasks"
    __table_args__ = (UniqueConstraint("run_id", "task_key"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    task_key: Mapped[str] = mapped_column(String(250))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "catalog_items"
    __table_args__ = (
        UniqueConstraint("run_id", "row_number"),
        Index("ix_catalog_items_run_sku", "run_id", "sku"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str | None] = mapped_column(String(500))
    ean: Mapped[str | None] = mapped_column(String(500))
    base_code: Mapped[str | None] = mapped_column(String(500), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __mapper_args__ = {"version_id_col": row_version}


class ImageAsset(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "image_assets"

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True
    )
    source_url: Mapped[str | None] = mapped_column(String(2000))
    object_key: Mapped[str] = mapped_column(String(1000))
    checksum: Mapped[str] = mapped_column(String(128), index=True)
    position: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Extraction(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "extractions"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True
    )
    request_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(100))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class MappingResult(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "mapping_results"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True
    )
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)


class ValidationIssue(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "validation_issues"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str | None] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="error", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EditAudit(UUIDMixin, Base):
    __tablename__ = "edit_audits"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(200))
    old_value: Mapped[object | None] = mapped_column(JSON)
    new_value: Mapped[object | None] = mapped_column(JSON)
    old_version: Mapped[int] = mapped_column(Integer)
    new_version: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(320), default="internal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExportJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "export_jobs"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    format: Mapped[str] = mapped_column(String(20))
    include_images: Mapped[bool] = mapped_column(Boolean, default=False)
    override_blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    override_actor: Mapped[str | None] = mapped_column(String(320))
    object_key: Mapped[str | None] = mapped_column(String(1000))
    error: Mapped[str | None] = mapped_column(Text)
