import csv
import io
import json
import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from zipfile import BadZipFile

import redis
from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import String, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .database import get_db
from .models import (
    AppSetting,
    AttributeSet,
    CatalogItem,
    EditAudit,
    ExportJob,
    HeaderDefinition,
    ImageJob,
    LLMProfile,
    MappingProfile,
    PreflightRecord,
    Prompt,
    PublishedSnapshot,
    Run,
    RunTask,
    ValidationIssue,
    ValueList,
    ValueListItem,
)
from .schemas import (
    AppSettingCreate,
    AppSettingsValues,
    AttributeSetCreate,
    BulkEdit,
    ExportCreate,
    HeaderCreate,
    LLMProfileCreate,
    LLMProfileUpdate,
    MappingProfileCreate,
    PromptCreate,
    ReviewEdit,
    RunCreate,
    ValueItemCreate,
    ValueListCreate,
)
from .security import (
    JSONLogFormatter,
    SecretRedactionFilter,
    encrypt_secret,
    normalize_name,
    sanitize_snapshot,
    validate_llm_endpoint,
)
from .settings import settings

app = FastAPI(title=settings.app_name, version="1.0.0", openapi_url="/api/v1/openapi.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

root_logger = logging.getLogger()
if not root_logger.handlers:
    root_logger.addHandler(logging.StreamHandler())
for logger in (root_logger, logging.getLogger("uvicorn"), logging.getLogger("uvicorn.error")):
    for handler in logger.handlers:
        handler.addFilter(SecretRedactionFilter())
        handler.setFormatter(JSONLogFormatter())


def api_error(code: str, message: str, details: object | None = None) -> dict:
    body = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        content = detail
    else:
        content = api_error("http_error", str(detail))
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(content))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(api_error("validation_error", "invalid request", exc.errors())),
    )


def fail(status_code: int, code: str, message: str, details: object | None = None) -> None:
    raise HTTPException(status_code, api_error(code, message, details))


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        fail(409, "conflict", "a record with the same unique value already exists")
        raise exc  # pragma: no cover


def get_or_404(db: Session, model, object_id: uuid.UUID):
    value = db.get(model, object_id)
    if value is None:
        fail(404, "not_found", "record not found")
    return value


def enqueue(task, *args: object) -> str:
    return str(task.delay(*args).id)


@app.get("/api/v1/health/live")
def liveness() -> dict:
    return {"status": "ok"}


@app.get("/api/v1/health")
def health(db: Session = Depends(get_db)) -> dict:
    checks: dict[str, str] = {}
    try:
        db.execute(select(1))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = type(exc).__name__
    try:
        connection = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        connection.ping()
        connection.close()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = type(exc).__name__
    try:
        from .storage import client

        client().head_bucket(Bucket=settings.s3_bucket)
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = type(exc).__name__
    if any(value != "ok" for value in checks.values()):
        fail(503, "unhealthy", "one or more dependencies are unavailable", checks)
    return {"status": "ok", "checks": checks}


RESOURCE_MODELS = {
    "headers": HeaderDefinition,
    "value-lists": ValueList,
    "attribute-sets": AttributeSet,
    "prompts": Prompt,
    "mapping-profiles": MappingProfile,
    "llm-profiles": LLMProfile,
    "settings": AppSetting,
}


def resource_model(resource: str):
    model = RESOURCE_MODELS.get(resource)
    if model is None:
        fail(404, "not_found", "unknown configuration resource")
    return model


def config_payload(db: Session, resource: str, value) -> dict:
    common = {
        "id": value.id,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }
    if resource == "headers":
        return common | {
            "key": value.key,
            "label": value.label,
            "aliases": value.aliases,
            "required": value.required,
            "generated": value.generated,
            "archived_at": value.archived_at,
        }
    if resource == "value-lists":
        items = db.scalars(
            select(ValueListItem)
            .where(ValueListItem.value_list_id == value.id)
            .order_by(ValueListItem.canonical_value)
        ).all()
        return common | {
            "name": value.name,
            "description": value.description,
            "archived_at": value.archived_at,
            "items": [
                {
                    "id": item.id,
                    "canonical_value": item.canonical_value,
                    "aliases": item.aliases,
                    "archived_at": item.archived_at,
                }
                for item in items
            ],
        }
    if resource == "attribute-sets":
        return common | {
            "name": value.name,
            "attributes": value.attributes,
            "assignment_rules": value.assignment_rules,
            "archived_at": value.archived_at,
        }
    if resource == "prompts":
        return common | {
            "name": value.name,
            "text": value.text,
            "response_schema": value.response_schema,
            "archived_at": value.archived_at,
        }
    if resource == "mapping-profiles":
        return common | {
            "name": value.name,
            "mapping": value.mapping,
            "fuzzy_matching": value.fuzzy_matching,
            "multiselect_delimiter": value.multiselect_delimiter,
            "archived_at": value.archived_at,
        }
    if resource == "llm-profiles":
        return common | {
            "name": value.name,
            "adapter": value.adapter,
            "model_name": value.model_name,
            "endpoint_url": value.endpoint_url,
            "options": value.options,
            "has_api_key": value.encrypted_api_key is not None,
            "archived_at": value.archived_at,
        }
    return common | {"key": value.key, "value": value.value}


def validate_header_names(
    db: Session,
    key: str,
    aliases: list[str],
    exclude_id: uuid.UUID | None = None,
) -> None:
    names = [key, *aliases]
    normalized = [normalize_name(name) for name in names]
    if any(not name for name in normalized) or len(normalized) != len(set(normalized)):
        fail(409, "duplicate_header_alias", "header keys and aliases must be unique")
    for header in db.scalars(
        select(HeaderDefinition).where(HeaderDefinition.archived_at.is_(None))
    ):
        if header.id == exclude_id:
            continue
        existing = {normalize_name(header.key), *(normalize_name(x) for x in header.aliases)}
        if existing.intersection(normalized):
            fail(409, "duplicate_header_alias", "header alias is already configured")


def validate_value_items(
    items: list[ValueItemCreate], existing: list[ValueListItem] | None = None
) -> None:
    used = {
        normalize_name(name)
        for item in existing or []
        if item.archived_at is None
        for name in [item.canonical_value, *item.aliases]
    }
    for item in items:
        names = [item.canonical_value, *item.aliases]
        normalized = [normalize_name(name) for name in names]
        if any(not name for name in normalized) or len(normalized) != len(set(normalized)):
            fail(409, "duplicate_value", "canonical values and aliases must be unique")
        if used.intersection(normalized):
            fail(409, "duplicate_value", "value or alias is already configured")
        used.update(normalized)


def llm_profile_from_input(data: LLMProfileCreate, db: Session) -> LLMProfile:
    endpoint = str(data.endpoint_url) if data.endpoint_url else None
    if data.adapter == "openai-compatible" and not endpoint:
        fail(422, "validation_error", "openai-compatible profiles require endpoint_url")
    try:
        private_hosts = db.scalar(select(AppSetting).where(AppSetting.key == "private_llm_hosts"))
        database_hosts = private_hosts.value if private_hosts else []
        validation_settings = settings.model_copy(
            update={
                "allowed_private_llm_hosts": list(
                    dict.fromkeys([*settings.allowed_private_llm_hosts, *database_hosts])
                ),
                "allow_insecure_llm_endpoints": settings.allow_insecure_llm_endpoints
                or bool(database_hosts),
            }
        )
        validate_llm_endpoint(endpoint, validation_settings)
    except ValueError as exc:
        fail(422, "invalid_llm_endpoint", str(exc))
    if sanitize_snapshot(data.options) != data.options:
        fail(422, "secret_in_options", "send credentials using api_key, not options")
    return LLMProfile(
        name=data.name,
        adapter=data.adapter,
        model_name=data.model_name,
        endpoint_url=endpoint,
        options=data.options,
        encrypted_api_key=encrypt_secret(data.api_key) if data.api_key else None,
    )


@app.get("/api/v1/config/{resource}")
def list_config(
    resource: str,
    include_archived: bool = False,
    db: Session = Depends(get_db),
) -> list[dict]:
    model = resource_model(resource)
    query = select(model)
    if hasattr(model, "archived_at") and not include_archived:
        query = query.where(model.archived_at.is_(None))
    values = db.scalars(query.order_by(model.created_at.desc())).all()
    return [config_payload(db, resource, value) for value in values]


@app.post("/api/v1/config/{resource}", status_code=status.HTTP_201_CREATED)
def create_config(resource: str, payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    try:
        if resource == "headers":
            data = HeaderCreate.model_validate(payload)
            validate_header_names(db, data.key, data.aliases)
            value = HeaderDefinition(**data.model_dump())
        elif resource == "value-lists":
            data = ValueListCreate.model_validate(payload)
            validate_value_items(data.items)
            value = ValueList(name=data.name, description=data.description)
            db.add(value)
            db.flush()
            db.add_all(
                ValueListItem(
                    value_list_id=value.id,
                    canonical_value=item.canonical_value,
                    canonical_normalized=normalize_name(item.canonical_value),
                    aliases=item.aliases,
                )
                for item in data.items
            )
        elif resource == "attribute-sets":
            value = AttributeSet(**AttributeSetCreate.model_validate(payload).model_dump())
        elif resource == "prompts":
            value = Prompt(**PromptCreate.model_validate(payload).model_dump())
        elif resource == "mapping-profiles":
            value = MappingProfile(**MappingProfileCreate.model_validate(payload).model_dump())
        elif resource == "llm-profiles":
            value = llm_profile_from_input(LLMProfileCreate.model_validate(payload), db)
        elif resource == "settings":
            value = AppSetting(**AppSettingCreate.model_validate(payload).model_dump())
        else:
            resource_model(resource)
            raise AssertionError("unreachable")
    except HTTPException:
        raise
    except ValueError as exc:
        fail(422, "validation_error", "invalid configuration", str(exc))
    db.add(value)
    commit(db)
    db.refresh(value)
    return config_payload(db, resource, value)


@app.get("/api/v1/config/{resource}/{resource_id:uuid}")
def get_config(resource: str, resource_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    return config_payload(db, resource, get_or_404(db, resource_model(resource), resource_id))


@app.patch("/api/v1/config/{resource}/{resource_id:uuid}")
def update_config(
    resource: str,
    resource_id: uuid.UUID,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    value = get_or_404(db, resource_model(resource), resource_id)
    allowed = {
        "headers": {"label", "aliases", "required", "generated", "archived"},
        "value-lists": {"name", "description", "archived"},
        "attribute-sets": {"name", "attributes", "assignment_rules", "archived"},
        "prompts": {"name", "text", "response_schema", "archived"},
        "mapping-profiles": {
            "name",
            "mapping",
            "fuzzy_matching",
            "multiselect_delimiter",
            "archived",
        },
        "llm-profiles": {
            "name",
            "model_name",
            "endpoint_url",
            "options",
            "api_key",
            "clear_api_key",
            "archived",
        },
        "settings": {"value"},
    }[resource]
    unknown = set(payload) - allowed
    if unknown:
        fail(422, "validation_error", f"unknown fields: {', '.join(sorted(unknown))}")
    if "archived" in payload and not isinstance(payload["archived"], bool):
        fail(422, "validation_error", "archived must be a boolean")
    try:
        if resource == "llm-profiles":
            payload = LLMProfileUpdate.model_validate(payload).model_dump(exclude_unset=True)
            if any(
                payload.get(key) is None
                for key in ("name", "model_name", "options")
                if key in payload
            ):
                raise ValueError("name, model_name, and options cannot be null")
            endpoint = payload.get("endpoint_url", value.endpoint_url)
            if value.adapter == "openai-compatible" and not endpoint:
                raise ValueError("openai-compatible profiles require endpoint_url")
        else:
            if resource == "headers":
                schema, current = (
                    HeaderCreate,
                    {
                        "key": value.key,
                        "label": value.label,
                        "aliases": value.aliases,
                        "required": value.required,
                        "generated": value.generated,
                    },
                )
            elif resource == "value-lists":
                schema, current = (
                    ValueListCreate,
                    {
                        "name": value.name,
                        "description": value.description,
                        "items": [],
                    },
                )
            elif resource == "attribute-sets":
                schema, current = (
                    AttributeSetCreate,
                    {
                        "name": value.name,
                        "attributes": value.attributes,
                        "assignment_rules": value.assignment_rules,
                    },
                )
            elif resource == "prompts":
                schema, current = (
                    PromptCreate,
                    {
                        "name": value.name,
                        "text": value.text,
                        "response_schema": value.response_schema,
                    },
                )
            elif resource == "mapping-profiles":
                schema, current = (
                    MappingProfileCreate,
                    {
                        "name": value.name,
                        "mapping": value.mapping,
                        "fuzzy_matching": value.fuzzy_matching,
                        "multiselect_delimiter": value.multiselect_delimiter,
                    },
                )
            else:
                schema, current = (
                    AppSettingCreate,
                    {
                        "key": value.key,
                        "value": value.value,
                    },
                )
            updates = {key: item for key, item in payload.items() if key != "archived"}
            validated = schema.model_validate(current | updates).model_dump()
            payload = payload | {key: validated[key] for key in updates}
    except ValueError as exc:
        fail(422, "validation_error", "invalid configuration", str(exc))
    if resource == "headers" and "aliases" in payload:
        validate_header_names(db, value.key, payload["aliases"], value.id)
    if resource == "llm-profiles":
        if "endpoint_url" in payload:
            endpoint = str(payload["endpoint_url"]) if payload["endpoint_url"] else None
            try:
                private_hosts = db.scalar(
                    select(AppSetting).where(AppSetting.key == "private_llm_hosts")
                )
                database_hosts = private_hosts.value if private_hosts else []
                validation_settings = settings.model_copy(
                    update={
                        "allowed_private_llm_hosts": list(
                            dict.fromkeys([*settings.allowed_private_llm_hosts, *database_hosts])
                        ),
                        "allow_insecure_llm_endpoints": settings.allow_insecure_llm_endpoints
                        or bool(database_hosts),
                    }
                )
                validate_llm_endpoint(endpoint, validation_settings)
            except ValueError as exc:
                fail(422, "invalid_llm_endpoint", str(exc))
            value.endpoint_url = endpoint
        if "options" in payload:
            if sanitize_snapshot(payload["options"]) != payload["options"]:
                fail(422, "secret_in_options", "send credentials using api_key, not options")
            value.options = payload["options"]
        if payload.get("api_key"):
            value.encrypted_api_key = encrypt_secret(payload["api_key"])
        if payload.get("clear_api_key"):
            value.encrypted_api_key = None
    for key, item in payload.items():
        if key in {"endpoint_url", "options", "api_key", "clear_api_key", "archived"}:
            continue
        setattr(value, key, item)
    if "archived" in payload and hasattr(value, "archived_at"):
        value.archived_at = datetime.now(UTC) if payload["archived"] else None
    commit(db)
    db.refresh(value)
    return config_payload(db, resource, value)


@app.delete("/api/v1/config/{resource}/{resource_id:uuid}", status_code=204)
def archive_config(
    resource: str, resource_id: uuid.UUID, db: Session = Depends(get_db)
) -> Response:
    value = get_or_404(db, resource_model(resource), resource_id)
    if not hasattr(value, "archived_at"):
        fail(405, "not_allowed", "this resource cannot be archived")
    value.archived_at = datetime.now(UTC)
    commit(db)
    return Response(status_code=204)


@app.post(
    "/api/v1/config/value-lists/{value_list_id}/items",
    status_code=status.HTTP_201_CREATED,
)
def add_value_list_items(
    value_list_id: uuid.UUID,
    items: list[ValueItemCreate],
    db: Session = Depends(get_db),
) -> list[dict]:
    get_or_404(db, ValueList, value_list_id)
    existing = db.scalars(
        select(ValueListItem).where(ValueListItem.value_list_id == value_list_id)
    ).all()
    validate_value_items(items, existing)
    values = [
        ValueListItem(
            value_list_id=value_list_id,
            canonical_value=item.canonical_value,
            canonical_normalized=normalize_name(item.canonical_value),
            aliases=item.aliases,
        )
        for item in items
    ]
    db.add_all(values)
    commit(db)
    return [
        {"id": item.id, "canonical_value": item.canonical_value, "aliases": item.aliases}
        for item in values
    ]


@app.get("/api/v1/config/value-lists/{value_list_id}/export")
def export_value_list(value_list_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    value_list = get_or_404(db, ValueList, value_list_id)
    items = db.scalars(
        select(ValueListItem)
        .where(
            ValueListItem.value_list_id == value_list_id,
            ValueListItem.archived_at.is_(None),
        )
        .order_by(ValueListItem.canonical_value)
    ).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["canonical_value", "aliases"])
    writer.writerows((item.canonical_value, "|".join(item.aliases)) for item in items)
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{value_list.name}.csv"'},
    )


@app.post("/api/v1/config/{resource}/{resource_id:uuid}/publish", status_code=201)
def publish_config(resource: str, resource_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    value = get_or_404(db, resource_model(resource), resource_id)
    previous = db.scalar(
        select(func.max(PublishedSnapshot.version)).where(
            PublishedSnapshot.resource_type == resource,
            PublishedSnapshot.source_id == resource_id,
        )
    )
    payload = sanitize_snapshot(config_payload(db, resource, value))
    snapshot = PublishedSnapshot(
        resource_type=resource,
        source_id=resource_id,
        version=(previous or 0) + 1,
        payload=jsonable_encoder(payload),
    )
    db.add(snapshot)
    commit(db)
    db.refresh(snapshot)
    return {
        "id": snapshot.id,
        "resource_type": snapshot.resource_type,
        "source_id": snapshot.source_id,
        "version": snapshot.version,
        "payload": snapshot.payload,
        "created_at": snapshot.created_at,
    }


@app.get("/api/v1/config/{resource}/{resource_id:uuid}/snapshots")
def list_snapshots(
    resource: str, resource_id: uuid.UUID, db: Session = Depends(get_db)
) -> list[dict]:
    resource_model(resource)
    snapshots = db.scalars(
        select(PublishedSnapshot)
        .where(
            PublishedSnapshot.resource_type == resource,
            PublishedSnapshot.source_id == resource_id,
        )
        .order_by(PublishedSnapshot.version.desc())
    ).all()
    return [
        {
            "id": item.id,
            "resource_type": item.resource_type,
            "source_id": item.source_id,
            "version": item.version,
            "payload": item.payload,
            "created_at": item.created_at,
        }
        for item in snapshots
    ]


@app.post("/api/v1/config/llm-profiles/{profile_id}/test", status_code=202)
def queue_llm_test(profile_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    profile = get_or_404(db, LLMProfile, profile_id)
    if profile.archived_at:
        fail(409, "archived", "archived profiles cannot be tested")
    from .worker import test_llm_profile

    return {"id": enqueue(test_llm_profile, str(profile_id)), "status": "queued"}


@app.post("/api/v1/templates")
def create_template(payload: dict = Body(...)) -> Response:
    from .services.templates import generate_template

    try:
        content = generate_template(
            payload.get("required_columns", []), image_columns=payload.get("image_columns", 10)
        )
    except (TypeError, ValueError) as exc:
        fail(422, "invalid_template", str(exc))
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="catalog-template.xlsx"'},
    )


@app.get("/api/v1/templates/workbook")
def download_template(db: Session = Depends(get_db)) -> Response:
    headers = db.scalars(
        select(HeaderDefinition)
        .where(HeaderDefinition.archived_at.is_(None), HeaderDefinition.required.is_(True))
        .order_by(HeaderDefinition.created_at)
    ).all()
    return create_template(
        {"required_columns": [header.key for header in headers], "image_columns": 10}
    )


@app.get("/api/v1/demo/catalog.xlsx")
def download_demo_catalog() -> Response:
    from botocore.exceptions import ClientError

    from .storage import get_bytes

    try:
        workbook = get_bytes("demo/catalog.xlsx")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {
            "404",
            "NoSuchKey",
            "NoSuchObject",
        }:
            raise
        fail(404, "not_found", "demo seed workbook is not available")
    return Response(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="demo-catalog.xlsx"'},
    )


@app.post("/api/v1/uploads", status_code=201)
async def upload_workbook(file: UploadFile = File(...)) -> dict:
    from .services.exports import safe_filename
    from .storage import put_bytes

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        fail(413, "upload_too_large", "upload exceeds configured byte limit")
    name = safe_filename(file.filename or "catalog.xlsx")
    key = f"uploads/{uuid.uuid4()}/{name}"
    put_bytes(key, content, file.content_type or "application/octet-stream")
    return {"object_key": key, "filename": name, "bytes": len(content)}


@app.post("/api/v1/preflight")
@app.post("/api/v1/runs/preflight")
async def preflight(
    file: UploadFile = File(...),
    required_headers: str = Form("[]"),
    aliases: str = Form("{}"),
    attribute_rules: str = Form("[]"),
    db: Session = Depends(get_db),
) -> dict:
    from .services.exports import safe_filename
    from .services.preflight import preflight_workbook
    from .storage import put_bytes

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        fail(413, "upload_too_large", "upload exceeds configured byte limit")
    try:
        snapshot_ids, frozen_config = resolve_run_configuration(db, {}, {})
        configured_headers = frozen_config.get("headers", [])
        requested_headers = json.loads(required_headers)
        alias_map = json.loads(aliases)
        rules = json.loads(attribute_rules)
        if not requested_headers:
            requested_headers = [
                header["key"] for header in configured_headers if header.get("required")
            ]
        if not alias_map:
            alias_map = {header["key"]: header.get("aliases", []) for header in configured_headers}
        if not rules:
            rules = frozen_config.get("attribute_assignment_rules", [])
        result = preflight_workbook(
            content,
            required_headers=requested_headers,
            aliases=alias_map,
            attribute_rules=rules,
        )
        known_sets = set(frozen_config.get("attribute_sets", {}))
        unknown_sets = sorted(
            {
                row["values"].get("_attribute_set")
                for row in result["rows"]
                if row["values"].get("_attribute_set") not in known_sets
                and row["values"].get("_attribute_set") is not None
            }
        )
        if unknown_sets:
            result["issues"].append(
                {
                    "code": "unknown_attribute_set",
                    "message": f"assignment references unpublished attribute sets: {unknown_sets}",
                    "blocking": True,
                }
            )
            result["valid"] = False
    except (BadZipFile, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        fail(422, "invalid_workbook", str(exc))
    name = safe_filename(file.filename or "catalog.xlsx")
    key = f"preflights/{uuid.uuid4()}/{name}"
    put_bytes(key, content, file.content_type or "application/octet-stream")
    frozen_result = result | {
        "snapshot_ids": snapshot_ids,
        "effective_config": frozen_config,
    }
    record = PreflightRecord(
        filename=name,
        input_object_key=key,
        result=jsonable_encoder(frozen_result),
        items=jsonable_encoder(result["rows"]),
    )
    db.add(record)
    commit(db)
    issues = [
        issue | {"severity": "error" if issue.get("blocking") else "warning"}
        for issue in result["issues"]
    ]
    return {
        "id": record.id,
        "rows": result["row_count"],
        "groups": result["group_count"],
        "image_urls": sum(
            bool(row["values"].get(column))
            for row in result["rows"]
            for column in result["image_columns"]
        ),
        "issues": issues,
        "valid": result["valid"],
    }


def run_payload(run: Run) -> dict:
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "input_object_key": run.input_object_key,
        "preflight": run.preflight,
        "snapshot_ids": run.snapshot_ids,
        "total_items": run.total_items,
        "completed_items": run.completed_items,
        "failed_items": run.failed_items,
        "cancel_requested": run.cancel_requested,
        "source_run_id": run.source_run_id,
        "deleted_at": run.deleted_at,
        "purge_after": run.purge_after,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def resolve_run_configuration(
    db: Session,
    requested_ids: dict[str, uuid.UUID],
    requested_config: dict,
) -> tuple[dict[str, str], dict]:
    if requested_ids:
        snapshots = []
        for key, snapshot_id in requested_ids.items():
            snapshot = db.get(PublishedSnapshot, snapshot_id)
            if snapshot is None:
                fail(422, "invalid_snapshot", f"unknown snapshot for {key}")
            snapshots.append(snapshot)
        snapshot_ids = {key: str(value) for key, value in requested_ids.items()}
    else:
        active_sources: dict[str, set[uuid.UUID]] = {}
        for resource, model in RESOURCE_MODELS.items():
            query = select(model.id)
            if hasattr(model, "archived_at"):
                query = query.where(model.archived_at.is_(None))
            active_sources[resource] = set(db.scalars(query))
        latest: dict[tuple[str, uuid.UUID], PublishedSnapshot] = {}
        for snapshot in db.scalars(select(PublishedSnapshot).order_by(PublishedSnapshot.version)):
            if snapshot.source_id in active_sources.get(snapshot.resource_type, set()):
                latest[(snapshot.resource_type, snapshot.source_id)] = snapshot
        snapshots = list(latest.values())
        snapshot_ids = {
            f"{snapshot.resource_type}:{snapshot.source_id}": str(snapshot.id)
            for snapshot in snapshots
        }

    by_type: dict[str, list[PublishedSnapshot]] = {}
    for snapshot in snapshots:
        by_type.setdefault(snapshot.resource_type, []).append(snapshot)
    attribute_sets = {
        snapshot.payload["name"]: snapshot.payload["attributes"]
        for snapshot in by_type.get("attribute-sets", [])
    }
    derived: dict = {
        "attribute_sets": attribute_sets,
        "attribute_assignment_rules": [
            rule
            for snapshot in by_type.get("attribute-sets", [])
            for rule in snapshot.payload.get("assignment_rules", [])
        ],
        "headers": [snapshot.payload for snapshot in by_type.get("headers", [])],
        "value_lists": {
            snapshot.payload["name"]: snapshot.payload.get("items", [])
            for snapshot in by_type.get("value-lists", [])
        },
    }
    if len(attribute_sets) == 1:
        derived["attributes"] = next(iter(attribute_sets.values()))

    def newest(resource: str) -> PublishedSnapshot | None:
        values = by_type.get(resource, [])
        return max(values, key=lambda item: item.created_at) if values else None

    if prompt := newest("prompts"):
        derived.update(
            {
                "prompt": prompt.payload["text"],
                "prompt_version": f"{prompt.source_id}:{prompt.version}",
                "response_schema": prompt.payload.get("response_schema", {}),
            }
        )
    if mapping := newest("mapping-profiles"):
        derived.update(
            {
                "mapping": mapping.payload.get("mapping", {}),
                "fuzzy_matching": mapping.payload.get("fuzzy_matching", False),
                "multiselect_delimiter": mapping.payload.get("multiselect_delimiter", "|"),
            }
        )
    if profile := newest("llm-profiles"):
        derived.update(
            {
                "llm_profile_id": str(profile.source_id),
                "llm_profile": profile.payload,
                "model": profile.payload.get("model_name", "mock"),
            }
        )
    live_settings = {setting.key: setting.value for setting in db.scalars(select(AppSetting)).all()}
    published_settings = {
        snapshot.payload["key"]: snapshot.payload.get("value")
        for snapshot in by_type.get("settings", [])
    }
    derived["app_settings"] = live_settings | published_settings
    effective = derived | requested_config
    effective["app_settings"] = derived["app_settings"] | requested_config.get("app_settings", {})
    effective.setdefault(
        "fuzzy_matching", bool(effective["app_settings"].get("fuzzy_matching", False))
    )
    effective.setdefault(
        "multiselect_delimiter",
        str(effective["app_settings"].get("multiselect_delimiter") or "|"),
    )
    return snapshot_ids, sanitize_snapshot(effective)


@app.post("/api/v1/runs", status_code=202)
def create_run(data: RunCreate, db: Session = Depends(get_db)) -> dict:
    preflight_record = None
    frozen_snapshot_ids = None
    frozen_effective_config = None
    if data.preflight_id:
        preflight_record = db.scalar(
            select(PreflightRecord).where(PreflightRecord.id == data.preflight_id).with_for_update()
        )
        if preflight_record is None:
            fail(404, "not_found", "PreflightRecord not found")
        if preflight_record.consumed_at:
            fail(409, "preflight_consumed", "preflight was already used to create a run")
        frozen_ids = {
            key: uuid.UUID(value)
            for key, value in preflight_record.result.get("snapshot_ids", {}).items()
        }
        frozen_config = preflight_record.result.get("effective_config", {})
        if "effective_config" in preflight_record.result:
            frozen_snapshot_ids = {key: str(value) for key, value in frozen_ids.items()}
            frozen_effective_config = sanitize_snapshot(frozen_config)
        data = data.model_copy(
            update={
                "name": data.name or preflight_record.filename,
                "input_object_key": preflight_record.input_object_key,
                "preflight": preflight_record.result,
                "items": preflight_record.items,
                "snapshot_ids": frozen_ids or data.snapshot_ids,
                "effective_config": frozen_config or data.effective_config,
            }
        )
    if data.preflight.get("valid") is False:
        fail(422, "preflight_failed", "blocking preflight issues must be fixed")
    if frozen_effective_config is not None:
        snapshot_ids = frozen_snapshot_ids or {}
        effective_config = frozen_effective_config
    else:
        snapshot_ids, effective_config = resolve_run_configuration(
            db, data.snapshot_ids, data.effective_config
        )
    run = Run(
        name=data.name,
        input_object_key=data.input_object_key,
        preflight=data.preflight,
        snapshot_ids=snapshot_ids,
        effective_config=effective_config,
        total_items=len(data.items),
    )
    db.add(run)
    db.flush()
    for index, row in enumerate(data.items, 1):
        raw = dict(row.get("values") or row.get("raw_data") or row)
        db.add(
            CatalogItem(
                run_id=run.id,
                row_number=int(row.get("row_number") or index),
                sku=str(raw["sku"]) if raw.get("sku") is not None else None,
                ean=str(raw["ean"]) if raw.get("ean") is not None else None,
                base_code=str(raw["base_code"]) if raw.get("base_code") is not None else None,
                raw_data=raw,
                data=dict(row.get("data") or raw),
            )
        )
    if preflight_record:
        preflight_record.consumed_at = datetime.now(UTC)
    commit(db)
    db.refresh(run)
    from .worker import dispatch_run

    job_id = enqueue(dispatch_run, str(run.id))
    return run_payload(run) | {"job_id": job_id}


@app.get("/api/v1/runs")
def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_deleted: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    conditions = [] if include_deleted else [Run.deleted_at.is_(None)]
    total = db.scalar(select(func.count()).select_from(Run).where(*conditions)) or 0
    runs = db.scalars(
        select(Run)
        .where(*conditions)
        .order_by(Run.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [run_payload(run) for run in runs],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size),
    }


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    return run_payload(get_or_404(db, Run, run_id))


def requeue_run(db: Session, run: Run) -> None:
    run.cancel_requested = False
    run.status = "queued"
    for task in db.scalars(select(RunTask).where(RunTask.run_id == run.id)):
        if task.status != "completed":
            task.status = "queued"
            task.error = None


@app.post("/api/v1/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, Run, run_id)
    if run.deleted_at or run.status in {"completed", "cancelled", "failed"}:
        fail(409, "invalid_transition", f"cannot cancel a {run.status} run")
    run.cancel_requested = True
    run.status = "cancelled"
    commit(db)
    return run_payload(run)


@app.post("/api/v1/runs/{run_id}/retry", status_code=202)
@app.post("/api/v1/runs/{run_id}/resume", status_code=202)
def retry_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, Run, run_id)
    if run.deleted_at or run.status not in {"failed", "cancelled"}:
        fail(409, "invalid_transition", f"cannot retry a {run.status} run")
    requeue_run(db, run)
    commit(db)
    from .worker import dispatch_run

    return run_payload(run) | {"job_id": enqueue(dispatch_run, str(run.id))}


@app.post("/api/v1/runs/{run_id}/duplicate", status_code=202)
def duplicate_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    source = get_or_404(db, Run, run_id)
    if source.deleted_at:
        fail(409, "deleted", "restore the run before duplicating it")
    duplicate = Run(
        name=f"{source.name} (copy)",
        status="queued",
        input_object_key=source.input_object_key,
        preflight=source.preflight,
        snapshot_ids=source.snapshot_ids,
        effective_config=source.effective_config,
        total_items=source.total_items,
        source_run_id=source.id,
    )
    db.add(duplicate)
    db.flush()
    items = db.scalars(
        select(CatalogItem).where(CatalogItem.run_id == source.id).order_by(CatalogItem.row_number)
    ).all()
    db.add_all(
        CatalogItem(
            run_id=duplicate.id,
            row_number=item.row_number,
            sku=item.sku,
            ean=item.ean,
            base_code=item.base_code,
            raw_data=item.raw_data,
            data=item.raw_data,
        )
        for item in items
    )
    commit(db)
    from .worker import dispatch_run

    return run_payload(duplicate) | {"job_id": enqueue(dispatch_run, str(duplicate.id))}


@app.delete("/api/v1/runs/{run_id}", status_code=204)
def delete_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    run = get_or_404(db, Run, run_id)
    if run.deleted_at is None:
        run.deleted_at = datetime.now(UTC)
        retention_days = db.scalar(
            select(AppSetting.value).where(AppSetting.key == "retention_days")
        )
        run.purge_after = run.deleted_at + timedelta(
            days=int(retention_days or settings.soft_delete_days)
        )
        run.cancel_requested = True
        commit(db)
    return Response(status_code=204)


@app.post("/api/v1/runs/{run_id}/restore")
def restore_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, Run, run_id)
    now = datetime.now(UTC)
    if run.deleted_at is None:
        fail(409, "not_deleted", "run is not deleted")
    purge_after = run.purge_after
    if purge_after and purge_after.tzinfo is None:
        purge_after = purge_after.replace(tzinfo=UTC)
    if purge_after and purge_after <= now:
        fail(410, "purge_due", "the restore window has expired")
    run.deleted_at = None
    run.purge_after = None
    run.status = "cancelled"
    run.cancel_requested = False
    commit(db)
    return run_payload(run)


def review_payload(item: CatalogItem) -> dict:
    return {
        "id": item.id,
        "run_id": item.run_id,
        "row_number": item.row_number,
        "sku": item.sku,
        "ean": item.ean,
        "base_code": item.base_code,
        "status": item.status,
        "confidence": item.confidence,
        "raw_data": item.raw_data,
        "data": item.data,
        "fields": item.data,
        "provenance": item.provenance,
        "validation_summary": item.validation_summary,
        "row_version": item.row_version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def resolve_item_attributes(config: dict, item: CatalogItem) -> list[dict]:
    attributes = config.get("attribute_sets", {}).get(
        item.raw_data.get("_attribute_set"), config.get("attributes", [])
    )
    mapping = config.get("mapping", {})
    value_lists = config.get("value_lists", {})
    resolved = []
    for attribute in attributes:
        name = str(attribute.get("name") or attribute.get("key") or "")
        rule = dict(attribute) | dict(mapping.get(name, {}))
        if isinstance(rule.get("value_list"), str):
            rule["value_list"] = value_lists.get(rule["value_list"], [])
        rule.setdefault("delimiter", config.get("multiselect_delimiter", "|"))
        resolved.append(rule)
    return resolved


@app.get("/api/v1/runs/{run_id}/review")
def list_review(
    run_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    search: str | None = None,
    item_status: str | None = Query(None, alias="status"),
    min_confidence: float | None = Query(None, ge=0, le=1),
    max_confidence: float | None = Query(None, ge=0, le=1),
    source: str | None = None,
    sort: str = "row_number",
    order: str = Query("asc", pattern="^(asc|desc)$"),
    direction: str | None = Query(None, pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> dict:
    run = get_or_404(db, Run, run_id)
    if run.deleted_at:
        fail(404, "not_found", "run not found")
    conditions = [CatalogItem.run_id == run_id]
    if search:
        pattern = f"%{search}%"
        conditions.append(
            or_(
                CatalogItem.sku.ilike(pattern),
                CatalogItem.ean.ilike(pattern),
                CatalogItem.base_code.ilike(pattern),
            )
        )
    if item_status:
        conditions.append(CatalogItem.status == item_status)
    if min_confidence is not None:
        conditions.append(CatalogItem.confidence >= min_confidence)
    if max_confidence is not None:
        conditions.append(CatalogItem.confidence <= max_confidence)
    if source:
        conditions.append(
            func.cast(CatalogItem.provenance, String).ilike(f'%"source": "{source}"%')
        )
    total = db.scalar(select(func.count()).select_from(CatalogItem).where(*conditions)) or 0
    column = getattr(CatalogItem, sort, CatalogItem.row_number)
    ordering = column.desc() if (direction or order) == "desc" else column.asc()
    items = db.scalars(
        select(CatalogItem)
        .where(*conditions)
        .order_by(ordering, CatalogItem.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [review_payload(item) for item in items],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size),
    }


def apply_review_edit(
    db: Session,
    item: CatalogItem,
    expected_version: int,
    changes: dict[str, object | None],
    actor: str,
) -> None:
    if item.row_version != expected_version:
        fail(
            409,
            "version_conflict",
            "row changed; reload before saving",
            {"current_version": item.row_version},
        )
    old_version = item.row_version
    values = dict(item.data)
    provenance = dict(item.provenance)
    changed = {key: value for key, value in changes.items() if values.get(key) != value}
    if not changed:
        return
    with db.no_autoflush:
        run = db.get(Run, item.run_id)
    config = run.effective_config if run else {}
    attributes = resolve_item_attributes(config, item)
    for field, new_value in changed.items():
        db.add(
            EditAudit(
                item_id=item.id,
                field=field,
                old_value=values.get(field),
                new_value=new_value,
                old_version=old_version,
                new_version=old_version + 1,
                actor=actor,
            )
        )
        values[field] = new_value
        provenance[field] = {"source": "manual", "confidence": 1.0, "actor": actor}
    item.data = values
    item.provenance = provenance
    scores = [
        detail.get("confidence")
        for detail in provenance.values()
        if isinstance(detail, dict) and detail.get("confidence") is not None
    ]
    item.confidence = min(scores) if scores else None
    if attributes:
        from .services.validation import validate_fields

        issues = validate_fields(values, attributes, provenance=item.provenance)
        item.validation_summary = {"issues": issues}
        with db.no_autoflush:
            image_blocking = db.scalar(
                select(func.count())
                .select_from(ValidationIssue)
                .where(
                    ValidationIssue.item_id == item.id,
                    ValidationIssue.code == "image_download_failed",
                    ValidationIssue.severity == "error",
                    ValidationIssue.resolved_at.is_(None),
                )
            )
        item.status = (
            "needs_review"
            if image_blocking or any(issue.get("blocking") for issue in issues)
            else "ready"
        )
        db.execute(
            delete(ValidationIssue).where(
                ValidationIssue.item_id == item.id,
                ValidationIssue.code != "image_download_failed",
            )
        )
        db.add_all(
            ValidationIssue(
                item_id=item.id,
                field=issue.get("field"),
                code=str(issue.get("code", "validation_error")),
                message=str(issue.get("message", "validation failed")),
                severity="error" if issue.get("blocking") else "warning",
            )
            for issue in issues
        )
    else:
        item.status = "edited"


@app.patch("/api/v1/review/items/{item_id}")
def edit_review_item(item_id: uuid.UUID, edit: ReviewEdit, db: Session = Depends(get_db)) -> dict:
    item = get_or_404(db, CatalogItem, item_id)
    apply_review_edit(db, item, edit.row_version, edit.changes, edit.actor)
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        fail(409, "version_conflict", "row changed; reload before saving")
    db.refresh(item)
    return review_payload(item)


@app.post("/api/v1/review/bulk")
def bulk_edit_review(payload: dict = Body(...), db: Session = Depends(get_db)) -> list[dict]:
    if "edits" in payload:
        try:
            data = BulkEdit.model_validate(payload)
        except ValueError as exc:
            fail(422, "validation_error", "invalid bulk edit", str(exc))
    else:
        try:
            row_ids = [uuid.UUID(str(item)) for item in payload["row_ids"]]
            field = str(payload["field"])
        except (KeyError, TypeError, ValueError) as exc:
            fail(422, "validation_error", "row_ids and field are required", str(exc))
        selected = db.scalars(select(CatalogItem).where(CatalogItem.id.in_(row_ids))).all()
        if len(selected) != len(set(row_ids)):
            fail(404, "not_found", "one or more review rows were not found")
        if payload.get("run_id") and any(
            str(item.run_id) != str(payload["run_id"]) for item in selected
        ):
            fail(409, "run_mismatch", "selected rows do not belong to the requested run")
        data = BulkEdit.model_validate(
            {
                "edits": [
                    {
                        "item_id": item.id,
                        "row_version": item.row_version,
                        "changes": {field: payload.get("value")},
                    }
                    for item in selected
                ],
                "actor": payload.get("actor") or "internal",
            }
        )
    items = {
        item.id: item
        for item in db.scalars(
            select(CatalogItem).where(CatalogItem.id.in_([entry.item_id for entry in data.edits]))
        )
    }
    if len(items) != len({entry.item_id for entry in data.edits}):
        fail(404, "not_found", "one or more review rows were not found")
    for entry in data.edits:
        if items[entry.item_id].row_version != entry.row_version:
            fail(
                409,
                "version_conflict",
                "one or more rows changed; reload before saving",
                {"item_id": entry.item_id, "current_version": items[entry.item_id].row_version},
            )
    for entry in data.edits:
        apply_review_edit(db, items[entry.item_id], entry.row_version, entry.changes, data.actor)
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        fail(409, "version_conflict", "one or more rows changed; reload before saving")
    return [review_payload(items[entry.item_id]) for entry in data.edits]


@app.get("/api/v1/review/items/{item_id}/provenance")
def item_provenance(item_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    item = get_or_404(db, CatalogItem, item_id)
    return {"item_id": item.id, "provenance": item.provenance}


@app.get("/api/v1/review/items/{item_id}/edits")
def item_edits(item_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    get_or_404(db, CatalogItem, item_id)
    edits = db.scalars(
        select(EditAudit).where(EditAudit.item_id == item_id).order_by(EditAudit.created_at.desc())
    ).all()
    return [
        {
            "id": edit.id,
            "field": edit.field,
            "old_value": edit.old_value,
            "new_value": edit.new_value,
            "old_version": edit.old_version,
            "new_version": edit.new_version,
            "actor": edit.actor,
            "created_at": edit.created_at,
        }
        for edit in edits
    ]


@app.post("/api/v1/runs/{run_id}/exports", status_code=202)
def queue_export(run_id: uuid.UUID, data: ExportCreate, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, Run, run_id)
    if run.deleted_at:
        fail(404, "not_found", "run not found")
    from .services.exports import export_blockers

    blockers = export_blockers(db, run)
    if blockers["blocked"] and not data.override_blocking:
        fail(
            409,
            "export_blocked",
            "confirm an audited override to export an incomplete or invalid run",
            blockers,
        )
    job = ExportJob(
        run_id=run_id,
        format=data.format,
        include_images=data.include_images,
        override_blocking=data.override_blocking,
        override_actor=data.actor if data.override_blocking else None,
    )
    db.add(job)
    commit(db)
    from .worker import create_export

    return {
        "id": job.id,
        "status": job.status,
        "job_id": enqueue(create_export, str(job.id)),
    }


@app.get("/api/v1/exports/{export_id}")
def get_export(export_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    job = get_or_404(db, ExportJob, export_id)
    result = {
        "id": job.id,
        "run_id": job.run_id,
        "status": job.status,
        "format": job.format,
        "include_images": job.include_images,
        "object_key": job.object_key,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
    if job.status == "completed" and job.object_key:
        result["download_url"] = f"/api/v1/exports/{job.id}/download"
    return result


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    from .worker import celery

    result = celery.AsyncResult(job_id)
    payload = result.result if result.ready() and not result.failed() else None
    job_status = {"SUCCESS": "succeeded", "FAILURE": "failed"}.get(
        result.status, result.status.casefold()
    )
    if job_status == "succeeded" and isinstance(payload, dict) and payload.get("ok") is False:
        job_status = "failed"
    message = None
    if isinstance(payload, dict):
        message = str(payload.get("error") or payload.get("model") or "") or None
    return {"id": job_id, "status": job_status, "result": payload, "message": message}


@app.post("/api/v1/image-downloads", status_code=202)
async def create_image_download(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> dict:
    from .services.exports import safe_filename
    from .storage import put_bytes

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        fail(413, "upload_too_large", "upload exceeds configured byte limit")
    header_aliases = {
        header.key: header.aliases
        for header in db.scalars(
            select(HeaderDefinition).where(HeaderDefinition.archived_at.is_(None))
        )
    }
    job = ImageJob(status="queued", result={"header_aliases": header_aliases})
    db.add(job)
    db.flush()
    name = safe_filename(file.filename or "images.xlsx")
    job.input_object_key = f"image-jobs/{job.id}/input/{name}"
    put_bytes(job.input_object_key, content, file.content_type or "application/octet-stream")
    commit(db)
    from .worker import process_image_job

    return {
        "id": job.id,
        "status": job.status,
        "job_id": enqueue(process_image_job, str(job.id)),
    }


@app.get("/api/v1/image-downloads/{job_id}")
def get_image_download(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    job = get_or_404(db, ImageJob, job_id)
    return {
        "id": job.id,
        "status": job.status,
        "progress": 0 if job.status == "queued" else job.result.get("progress", 10),
        "total_rows": job.result.get("total_rows", 0),
        "completed_rows": job.result.get("completed_rows", 0),
        "failed_rows": job.result.get("failed_rows", 0),
        "message": job.error or job.result.get("message"),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


@app.get("/api/v1/image-downloads/{job_id}/{artifact}")
def download_image_artifact(
    job_id: uuid.UUID, artifact: str, db: Session = Depends(get_db)
) -> Response:
    job = get_or_404(db, ImageJob, job_id)
    choices = {
        "images.zip": ("zip_key", "application/zip"),
        "report.csv": ("csv_key", "text/csv"),
        "report.xlsx": (
            "xlsx_key",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    }
    if artifact not in choices:
        fail(404, "not_found", "artifact not found")
    key_name, content_type = choices[artifact]
    key = job.result.get(key_name)
    if job.status != "completed" or not key:
        fail(409, "not_ready", "artifact is not ready")
    from .storage import iter_bytes

    return StreamingResponse(
        iter_bytes(key),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact}"'},
    )


@app.get("/api/v1/exports")
def list_exports(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    total = db.scalar(select(func.count()).select_from(ExportJob)) or 0
    jobs = db.scalars(
        select(ExportJob)
        .order_by(ExportJob.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": job.id,
                "run_id": job.run_id,
                "format": job.format,
                "status": job.status,
                "progress": 100 if job.status == "completed" else 0,
                "filename": job.object_key.rsplit("/", 1)[-1] if job.object_key else None,
                "created_at": job.created_at,
            }
            for job in jobs
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size),
    }


@app.get("/api/v1/exports/{export_id}/download")
def download_export(export_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    job = get_or_404(db, ExportJob, export_id)
    if job.status != "completed" or not job.object_key:
        fail(409, "not_ready", "export is not ready")
    from .storage import iter_bytes

    media_types = {
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image_zip": "application/zip",
    }
    filename = job.object_key.rsplit("/", 1)[-1]
    return StreamingResponse(
        iter_bytes(job.object_key),
        media_type=(
            "application/zip"
            if filename.endswith(".zip")
            else media_types.get(job.format, "application/octet-stream")
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


APP_SETTING_DEFAULTS = {
    "retention_days": 30,
    "max_image_bytes": 20_000_000,
    "max_image_pixels": 40_000_000,
    "multiselect_delimiter": "|",
    "fuzzy_matching": False,
    "private_llm_hosts": [],
}


@app.get("/api/v1/settings")
def get_settings_api(db: Session = Depends(get_db)) -> dict:
    values = dict(APP_SETTING_DEFAULTS)
    values.update(
        {
            setting.key: setting.value
            for setting in db.scalars(
                select(AppSetting).where(AppSetting.key.in_(APP_SETTING_DEFAULTS))
            )
        }
    )
    return values


@app.patch("/api/v1/settings")
def update_settings_api(payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    unknown = set(payload) - set(APP_SETTING_DEFAULTS)
    if unknown:
        fail(422, "validation_error", f"unknown settings: {', '.join(sorted(unknown))}")
    try:
        merged = AppSettingsValues.model_validate(get_settings_api(db) | payload).model_dump()
    except ValueError as exc:
        fail(422, "validation_error", "invalid settings", str(exc))
    if any(not host.strip() or "/" in host or ":" in host for host in merged["private_llm_hosts"]):
        fail(422, "validation_error", "private_llm_hosts must contain exact hostnames")
    for key in payload:
        value = merged[key]
        setting = db.scalar(select(AppSetting).where(AppSetting.key == key))
        if setting:
            setting.value = value
        else:
            db.add(AppSetting(key=key, value=value))
    commit(db)
    return get_settings_api(db)


@app.post("/api/v1/config/value-lists/import", status_code=201)
async def import_value_lists(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        fail(413, "upload_too_large", "upload exceeds configured byte limit")
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        fail(422, "invalid_csv", str(exc))
    if not rows or "canonical_value" not in rows[0]:
        fail(422, "invalid_csv", "CSV requires a canonical_value column")
    fallback_name = (file.filename or "Imported values").rsplit(".", 1)[0]
    grouped: dict[str, list[ValueItemCreate]] = {}
    try:
        for row in rows:
            name = str(row.get("list_name") or row.get("value_list") or fallback_name).strip()
            if not name:
                raise ValueError("value-list name cannot be blank")
            aliases = [
                alias.strip() for alias in str(row.get("aliases") or "").split("|") if alias.strip()
            ]
            grouped.setdefault(name, []).append(
                ValueItemCreate(
                    canonical_value=str(row["canonical_value"]).strip(), aliases=aliases
                )
            )
    except ValueError as exc:
        fail(422, "invalid_csv", "invalid value-list row", str(exc))
    imported = 0
    for name, items in grouped.items():
        value_list = db.scalar(select(ValueList).where(ValueList.name == name))
        if not value_list:
            value_list = ValueList(name=name)
            db.add(value_list)
            db.flush()
        existing = db.scalars(
            select(ValueListItem).where(ValueListItem.value_list_id == value_list.id)
        ).all()
        validate_value_items(items, existing)
        db.add_all(
            ValueListItem(
                value_list_id=value_list.id,
                canonical_value=item.canonical_value,
                canonical_normalized=normalize_name(item.canonical_value),
                aliases=item.aliases,
            )
            for item in items
        )
        imported += len(items)
    commit(db)
    return {"lists": len(grouped), "items": imported}
