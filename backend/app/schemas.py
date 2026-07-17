import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HeaderCreate(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    aliases: list[str] = []
    required: bool = False
    generated: bool = False


class HeaderUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    aliases: list[str] | None = None
    required: bool | None = None
    generated: bool | None = None
    archived: bool | None = None


class HeaderRead(ORMModel):
    id: uuid.UUID
    key: str
    label: str
    aliases: list[str]
    required: bool
    generated: bool
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ValueItemCreate(BaseModel):
    canonical_value: str = Field(min_length=1, max_length=500)
    aliases: list[str] = []


class ValueItemRead(ORMModel):
    id: uuid.UUID
    canonical_value: str
    aliases: list[str]
    archived_at: datetime | None


class ValueListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    items: list[ValueItemCreate] = []


class ValueListUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    archived: bool | None = None


class ValueListRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[ValueItemRead] = []


class AttributeSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    attributes: list[dict] = []
    assignment_rules: list[dict] = []


class AttributeSetRead(ORMModel):
    id: uuid.UUID
    name: str
    attributes: list[dict]
    assignment_rules: list[dict]
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PromptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    response_schema: dict = {}


class PromptRead(ORMModel):
    id: uuid.UUID
    name: str
    text: str
    response_schema: dict
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MappingProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mapping: dict = {}
    fuzzy_matching: bool = False
    multiselect_delimiter: str = Field("|", min_length=1, max_length=10)


class MappingProfileRead(ORMModel):
    id: uuid.UUID
    name: str
    mapping: dict
    fuzzy_matching: bool
    multiselect_delimiter: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AppSettingCreate(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    value: object


class AppSettingRead(ORMModel):
    id: uuid.UUID
    key: str
    value: object
    created_at: datetime
    updated_at: datetime


class AppSettingsValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retention_days: StrictInt = Field(ge=1, le=365)
    max_image_bytes: StrictInt = Field(ge=1_000_000)
    max_image_pixels: StrictInt = Field(ge=1_000_000)
    multiselect_delimiter: StrictStr = Field(min_length=1, max_length=5)
    fuzzy_matching: StrictBool
    private_llm_hosts: list[StrictStr]


class LLMProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    adapter: Literal["mock", "litellm", "openai-compatible"]
    model_name: str = Field(min_length=1, max_length=200)
    endpoint_url: HttpUrl | None = None
    options: dict = {}
    api_key: str | None = Field(default=None, min_length=1)


class LLMProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    endpoint_url: HttpUrl | None = None
    options: dict | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    archived: bool | None = None


class LLMProfileRead(ORMModel):
    id: uuid.UUID
    name: str
    adapter: str
    model_name: str
    endpoint_url: str | None
    options: dict
    has_api_key: bool
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SnapshotRead(ORMModel):
    id: uuid.UUID
    resource_type: str
    source_id: uuid.UUID
    version: int
    payload: dict
    created_at: datetime


class PreflightRequest(BaseModel):
    rows: list[dict]
    required_headers: list[str] = []
    header_aliases: dict[str, list[str]] = {}
    assignment_rules: list[dict] = []


class RunCreate(BaseModel):
    name: str = Field("Catalog run", min_length=1, max_length=250)
    preflight_id: uuid.UUID | None = None
    input_object_key: str | None = None
    preflight: dict = {}
    snapshot_ids: dict[str, uuid.UUID] = {}
    effective_config: dict = {}
    items: list[dict] = []


class RunRead(ORMModel):
    id: uuid.UUID
    name: str
    status: str
    input_object_key: str | None
    preflight: dict
    snapshot_ids: dict
    total_items: int
    completed_items: int
    failed_items: int
    cancel_requested: bool
    source_run_id: uuid.UUID | None
    deleted_at: datetime | None
    purge_after: datetime | None
    created_at: datetime
    updated_at: datetime


class ReviewItemRead(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID
    row_number: int
    sku: str | None
    ean: str | None
    base_code: str | None
    status: str
    confidence: float | None
    raw_data: dict
    data: dict
    provenance: dict
    validation_summary: dict
    row_version: int
    created_at: datetime
    updated_at: datetime


class ReviewEdit(BaseModel):
    row_version: int = Field(ge=1)
    changes: dict[str, object | None]
    actor: str = Field("internal", min_length=1, max_length=320)

    @model_validator(mode="after")
    def require_changes(self) -> "ReviewEdit":
        if not self.changes:
            raise ValueError("at least one field change is required")
        return self


class BulkEditEntry(BaseModel):
    item_id: uuid.UUID
    row_version: int = Field(ge=1)
    changes: dict[str, object | None]


class BulkEdit(BaseModel):
    edits: list[BulkEditEntry] = Field(min_length=1)
    actor: str = Field("internal", min_length=1, max_length=320)


class ExportCreate(BaseModel):
    format: Literal["csv", "xlsx", "image_zip"]
    include_images: bool = False
    override_blocking: bool = False
    actor: str | None = Field(default=None, max_length=320)

    @model_validator(mode="after")
    def require_actor_for_override(self) -> "ExportCreate":
        if self.override_blocking and not self.actor:
            raise ValueError("actor is required for a validation override")
        return self


class ExportRead(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID
    status: str
    format: str
    include_images: bool
    override_blocking: bool
    override_actor: str | None
    object_key: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class JobAccepted(BaseModel):
    id: str
    status: str


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str]
