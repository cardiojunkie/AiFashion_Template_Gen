import base64
import uuid
from datetime import UTC, datetime
from functools import partial
from tempfile import TemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from celery import Celery
from sqlalchemy import delete, func, select, text

from .database import SessionLocal
from .models import (
    AppSetting,
    CatalogItem,
    ExportJob,
    Extraction,
    HeaderDefinition,
    ImageAsset,
    ImageJob,
    LLMProfile,
    PreflightRecord,
    Run,
    RunTask,
    ValidationIssue,
)
from .security import decrypt_secret, validate_llm_endpoint
from .settings import settings

celery = Celery(
    "catalog_enrichment",
    broker=settings.broker_url,
    backend=settings.result_backend,
)
celery.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_serializer="json",
    task_always_eager=settings.task_always_eager,
    task_serializer="json",
    timezone="UTC",
    beat_schedule={
        "purge-soft-deleted-runs": {
            "task": "app.purge_deleted_runs",
            "schedule": 3600.0,
        }
    },
)


def _llm_runtime_settings(db):
    configured = db.scalar(select(AppSetting.value).where(AppSetting.key == "private_llm_hosts"))
    database_hosts = configured if isinstance(configured, list) else []
    hosts = list(dict.fromkeys([*settings.allowed_private_llm_hosts, *database_hosts]))
    return settings.model_copy(
        update={
            "allowed_private_llm_hosts": hosts,
            "allow_insecure_llm_endpoints": settings.allow_insecure_llm_endpoints
            or bool(database_hosts),
        }
    )


@celery.task(name="app.test_llm_profile")
def test_llm_profile(profile_id: str) -> dict:
    from .services.providers import test_profile

    with SessionLocal() as db:
        profile = db.get(LLMProfile, uuid.UUID(profile_id))
        if not profile or profile.archived_at:
            return {"ok": False, "error": "profile not found"}
        runtime_settings = _llm_runtime_settings(db)
        try:
            validate_llm_endpoint(profile.endpoint_url, runtime_settings)
        except ValueError as exc:
            return {"ok": False, "error": type(exc).__name__}
        payload = {
            "provider": profile.adapter,
            "model": profile.model_name,
            "base_url": profile.endpoint_url,
            "allow_insecure": runtime_settings.allow_insecure_llm_endpoints,
            "options": profile.options,
        }
        return test_profile(payload, decrypt_secret(profile.encrypted_api_key))


@celery.task(name="app.process_image_job")
def process_image_job(job_id: str) -> dict:
    job_uuid = uuid.UUID(job_id)
    with SessionLocal.begin() as db:
        job = db.scalar(select(ImageJob).where(ImageJob.id == job_uuid).with_for_update())
        if not job:
            return {"ok": False, "error": "job not found"}
        if job.status == "completed":
            return {"ok": True, **job.result}
        job.status = "running"
        input_key = job.input_object_key
        header_aliases = job.result.get("header_aliases") or {
            header.key: header.aliases
            for header in db.scalars(
                select(HeaderDefinition).where(HeaderDefinition.archived_at.is_(None))
            )
        }
        app_settings = {
            setting.key: setting.value
            for setting in db.scalars(
                select(AppSetting).where(
                    AppSetting.key.in_(["max_image_bytes", "max_image_pixels"])
                )
            )
        }
    try:
        from .services.image_downloader import download_image, process_image_urls
        from .services.reports import image_report_csv, image_report_xlsx
        from .services.workbook_images import parse_image_workbook
        from .storage import copy_to_fileobj, get_bytes, put_bytes, put_fileobj

        parsed = parse_image_workbook(get_bytes(str(input_key)), aliases=header_aliases)
        rows = parsed["rows"]
        image_columns = parsed["image_columns"]
        occurrences = [
            {
                "row_number": row["row_number"],
                "column": column,
                "url": str(row["values"][column]),
            }
            for row in rows
            for column in image_columns
            if row["values"].get(column)
        ]

        def store(name: str, data: bytes) -> str:
            key = f"image-jobs/{job_uuid}/normalized/{name}"
            put_bytes(key, data, "image/jpeg")
            return key

        results = process_image_urls(
            [occurrence["url"] for occurrence in occurrences],
            store=store,
            downloader=partial(
                download_image,
                max_bytes=int(app_settings.get("max_image_bytes", 20_000_000)),
            ),
            max_pixels=int(app_settings.get("max_image_pixels", 40_000_000)),
            retain_data=False,
        )
        report = [
            occurrence | result for occurrence, result in zip(occurrences, results, strict=True)
        ]
        prefix = f"image-jobs/{job_uuid}"
        keys = {
            "zip_key": f"{prefix}/images.zip",
            "csv_key": f"{prefix}/report.csv",
            "xlsx_key": f"{prefix}/report.xlsx",
        }
        with TemporaryFile(mode="w+b") as output:
            with ZipFile(output, "w", ZIP_DEFLATED) as archive:
                for occurrence, result in zip(occurrences, results, strict=True):
                    if result["status"] != "ok":
                        continue
                    name = (
                        f"row_{occurrence['row_number']}_{occurrence['column']}_"
                        f"{str(result['checksum'])[:12]}.jpg"
                    )
                    with archive.open(name, "w", force_zip64=True) as member:
                        copy_to_fileobj(str(result["storage_key"]), member)
            output.seek(0)
            put_fileobj(keys["zip_key"], output, "application/zip")
        put_bytes(keys["csv_key"], image_report_csv(report), "text/csv")
        put_bytes(
            keys["xlsx_key"],
            image_report_xlsx(report),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        failed_rows = {
            occurrence["row_number"]
            for occurrence, result in zip(occurrences, results, strict=True)
            if result["status"] != "ok"
        }
        failed_images = sum(result["status"] != "ok" for result in results)
        payload = {
            **keys,
            "header_aliases": header_aliases,
            "total_rows": len(rows),
            "completed_rows": len(rows),
            "failed_rows": len(failed_rows),
            "progress": 100,
            "message": (
                f"Processed {len(results) - failed_images} images; "
                f"{failed_images} failed across {len(failed_rows)} rows"
            ),
        }
        with SessionLocal.begin() as db:
            job = db.get(ImageJob, job_uuid)
            job.status = "completed"
            job.result = payload
        return {"ok": True, **payload}
    except Exception as exc:
        with SessionLocal.begin() as db:
            job = db.get(ImageJob, job_uuid)
            if job:
                job.status = "failed"
                job.error = type(exc).__name__
        return {"ok": False, "error": type(exc).__name__}


@celery.task(name="app.dispatch_run")
def dispatch_run(run_id: str) -> dict:
    run_uuid = uuid.UUID(run_id)
    queued_task_ids: list[str] = []
    with SessionLocal.begin() as db:
        run = db.scalar(select(Run).where(Run.id == run_uuid).with_for_update())
        if not run or run.deleted_at:
            return {"ok": False, "error": "run not found"}
        if run.cancel_requested:
            run.status = "cancelled"
            return {"ok": True, "status": run.status}

        items = db.scalars(select(CatalogItem).where(CatalogItem.run_id == run_uuid)).all()
        existing = {
            task.task_key: task
            for task in db.scalars(select(RunTask).where(RunTask.run_id == run_uuid)).all()
        }
        groups: dict[str, list[CatalogItem]] = {}
        for item in items:
            base_code = " ".join((item.base_code or "").casefold().split())
            key = f"group:{base_code}" if base_code else f"row:{item.id}"
            groups.setdefault(key, []).append(item)
        for key, members in groups.items():
            task = existing.get(key)
            if task is None:
                task = RunTask(
                    run_id=run.id,
                    task_key=key,
                    payload={"item_ids": [str(item.id) for item in members]},
                )
                db.add(task)
                db.flush()
            if task.status != "completed":
                task.status = "queued"
                task.error = None
                queued_task_ids.append(str(task.id))
        run.status = "running" if queued_task_ids else "completed"
        run_status = run.status

    for task_id in queued_task_ids:
        process_run_task.delay(task_id)
    return {"ok": True, "status": run_status, "queued": len(queued_task_ids)}


def _provider_for_run(db, config: dict):
    from .services.providers import (
        LiteLLMProvider,
        MockProvider,
        OpenAICompatibleProvider,
    )

    profile_id = config.get("llm_profile_id")
    profile = db.get(LLMProfile, uuid.UUID(profile_id)) if profile_id else None
    profile_snapshot = config.get("llm_profile", {})
    if profile or profile_snapshot:
        api_key = decrypt_secret(profile.encrypted_api_key) if profile else None
        adapter = profile_snapshot.get("adapter", profile.adapter if profile else None)
        endpoint = profile_snapshot.get("endpoint_url", profile.endpoint_url if profile else None)
        options = profile_snapshot.get("options", {})
        if endpoint:
            validate_llm_endpoint(str(endpoint), _llm_runtime_settings(db))
        if adapter == "mock":
            return MockProvider(options.get("response", {}))
        if adapter == "openai-compatible":
            return OpenAICompatibleProvider(
                str(endpoint),
                api_key=api_key,
                allow_insecure=str(endpoint).startswith("http://"),
            )
        return LiteLLMProvider(api_key=api_key, api_base=endpoint)
    provider_config = config.get("provider", {})
    if str(provider_config.get("type", "")).casefold() == "mock":
        return MockProvider(provider_config.get("response", {}))
    return None


def _update_run_progress(db, run: Run) -> None:
    run.completed_items = (
        db.scalar(
            select(func.count())
            .select_from(CatalogItem)
            .where(
                CatalogItem.run_id == run.id,
                CatalogItem.status.in_(["ready", "needs_review", "edited"]),
            )
        )
        or 0
    )
    run.failed_items = (
        db.scalar(
            select(func.count())
            .select_from(CatalogItem)
            .where(CatalogItem.run_id == run.id, CatalogItem.status == "failed")
        )
        or 0
    )


def _process_run_images(
    db, run: Run, items: list[CatalogItem]
) -> tuple[list[dict[str, str]], list[str]]:
    occurrences = [
        (item, int(str(key).rsplit("_", 1)[-1]), str(value))
        for item in items
        for key, value in item.raw_data.items()
        if str(key).casefold().startswith("image_")
        and str(key).rsplit("_", 1)[-1].isdigit()
        and value
    ]
    urls = list(dict.fromkeys(url for _, _, url in occurrences))
    if not urls:
        return [], []
    db.execute(
        delete(ValidationIssue).where(
            ValidationIssue.item_id.in_([item.id for item in items]),
            ValidationIssue.code == "image_download_failed",
        )
    )
    existing = {
        asset.source_url: asset
        for asset in db.scalars(
            select(ImageAsset).where(ImageAsset.run_id == run.id, ImageAsset.source_url.in_(urls))
        )
    }
    pending = [url for url in urls if url not in existing]

    from .services.image_downloader import download_image, process_image_urls
    from .storage import get_bytes, put_bytes

    def store(name: str, data: bytes) -> str:
        key = f"runs/{run.id}/images/{name}"
        put_bytes(key, data, "image/jpeg")
        return key

    max_pixels = int(
        run.effective_config.get("app_settings", {}).get("max_image_pixels", 40_000_000)
    )
    max_bytes = int(run.effective_config.get("app_settings", {}).get("max_image_bytes", 20_000_000))
    results = process_image_urls(
        pending,
        store=store,
        downloader=partial(download_image, max_bytes=max_bytes),
        max_pixels=max_pixels,
        retain_data=False,
    )
    normalized = {url: (asset.object_key, asset.checksum) for url, asset in existing.items()}
    required = bool(run.effective_config.get("images_required", False))
    for result in results:
        matching = [entry for entry in occurrences if entry[2] == result["url"]]
        if result["status"] == "ok":
            item, position, _ = matching[0]
            db.add(
                ImageAsset(
                    run_id=run.id,
                    item_id=item.id,
                    source_url=str(result["url"]),
                    object_key=str(result["storage_key"]),
                    checksum=str(result["checksum"]),
                    position=position,
                    metadata_json={
                        key: value
                        for key, value in result.items()
                        if key not in {"data", "storage_key"}
                    },
                )
            )
            normalized[str(result["url"])] = (str(result["storage_key"]), str(result["checksum"]))
            continue
        for item, _, _ in matching:
            db.add(
                ValidationIssue(
                    item_id=item.id,
                    field="images",
                    code="image_download_failed",
                    message=str(result.get("error") or "image download failed"),
                    severity="error" if required else "warning",
                )
            )
            if required:
                item.status = "needs_review"
    successful = sorted(
        (
            (item.row_number, position, url)
            for item, position, url in occurrences
            if url in normalized
        ),
        key=lambda entry: (entry[0], entry[1], entry[2]),
    )
    if not successful:
        return [], []
    representative_url = successful[0][2]
    return (
        [
            {
                "url": "data:image/jpeg;base64,"
                + base64.b64encode(get_bytes(normalized[representative_url][0])).decode("ascii"),
                "reference": representative_url,
            }
        ],
        [normalized[representative_url][1]],
    )


def _process_run_task(task_uuid: uuid.UUID) -> dict:
    now = datetime.now(UTC)
    with SessionLocal.begin() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        task = db.scalar(select(RunTask).where(RunTask.id == task_uuid).with_for_update())
        if not task:
            return {"ok": False, "error": "task not found"}
        if task.status == "completed":
            return {"ok": True, "status": "completed"}
        run = db.get(Run, task.run_id)
        if not run or run.cancel_requested or run.deleted_at:
            task.status = "cancelled"
            task.finished_at = now
            for item_id in task.payload.get("item_ids", []):
                if item := db.get(CatalogItem, uuid.UUID(item_id)):
                    item.status = "cancelled"
            return {"ok": True, "status": "cancelled"}
        task.status = "running"
        task.attempts += 1
        task.started_at = now

        items = [
            item
            for item_id in task.payload.get("item_ids", [])
            if (item := db.get(CatalogItem, uuid.UUID(item_id))) is not None
        ]
        if items:
            from .services.pipeline import enrich_group

            config = run.effective_config
            provider = _provider_for_run(db, config)
            images, image_checksums = _process_run_images(db, run, items)
            image_issues: dict[uuid.UUID, list[dict]] = {}
            for issue in db.scalars(
                select(ValidationIssue).where(
                    ValidationIssue.item_id.in_([item.id for item in items]),
                    ValidationIssue.code == "image_download_failed",
                    ValidationIssue.resolved_at.is_(None),
                )
            ):
                image_issues.setdefault(issue.item_id, []).append(
                    {
                        "field": issue.field,
                        "code": issue.code,
                        "message": issue.message,
                        "severity": issue.severity,
                        "blocking": issue.severity == "error",
                    }
                )
            cache: dict[str, dict] = {}
            schema = config.get("response_schema") or {}
            model = str(config.get("model", "mock"))
            model_settings = dict(config.get("model_settings") or {})
            profile = config.get("llm_profile") or {}
            provider_identity = {
                "source_id": config.get("llm_profile_id") or profile.get("id"),
                "adapter": profile.get("adapter"),
                "endpoint_url": profile.get("endpoint_url"),
                "options": profile.get("options", {}),
            }
            if any(value for value in provider_identity.values()):
                model_settings["provider_profile"] = provider_identity
            elif config.get("provider"):
                model_settings["provider_profile"] = config["provider"]
            extraction = None
            cache_claim = None
            cache_key = None
            if provider and images and schema:
                from .services.vision import vision_cache_key

                cache_key = vision_cache_key(
                    model_settings={"model": model, **model_settings},
                    prompt_version=str(config.get("prompt_version", "1")),
                    image_checksums=image_checksums,
                    schema=schema,
                )
                if db.bind.dialect.name == "postgresql":
                    lock_id = int(cache_key[:16], 16)
                    if lock_id >= 2**63:
                        lock_id -= 2**64
                    db.execute(select(func.pg_advisory_xact_lock(lock_id)))
                extraction = db.scalar(
                    select(Extraction).where(
                        Extraction.request_hash == cache_key,
                        Extraction.error.is_(None),
                    )
                )
                if extraction and extraction.result:
                    cache[cache_key] = extraction.result
                elif extraction is None:
                    cache_claim = Extraction(
                        item_id=items[0].id,
                        request_hash=cache_key,
                        provider=str(
                            config.get("llm_profile", {}).get("adapter")
                            or config.get("provider", {}).get("type")
                            or "unknown"
                        ),
                        result={},
                        usage={},
                    )
                    db.add(cache_claim)
                    db.flush()
                else:
                    cache_claim = extraction
            results: dict[str, dict] = {}
            attribute_sets = config.get("attribute_sets", {})
            for item in items:
                image_blocked = item.status == "needs_review"
                set_name = item.raw_data.get("_attribute_set")
                attributes = task.payload.get("attributes") or attribute_sets.get(
                    set_name, config.get("attributes", [])
                )
                if not attributes:
                    issues = image_issues.get(item.id, [])
                    item.status = "needs_review" if image_blocked else "ready"
                    item.validation_summary = {"issues": issues}
                    results[str(item.id)] = {
                        "valid": not image_blocked,
                        "issues": issues,
                    }
                    continue
                mapping_rules = config.get("mapping", {})
                value_lists = config.get("value_lists", {})
                resolved_attributes = []
                for attribute in attributes:
                    name = str(attribute.get("name") or attribute.get("key") or "")
                    resolved = dict(attribute) | dict(mapping_rules.get(name, {}))
                    if isinstance(resolved.get("value_list"), str):
                        resolved["value_list"] = value_lists.get(resolved["value_list"], [])
                    resolved.setdefault("delimiter", config.get("multiselect_delimiter", "|"))
                    resolved_attributes.append(resolved)
                result = enrich_group(
                    {
                        "base_code": item.base_code,
                        "rows": [{"row_number": item.row_number, "values": item.raw_data}],
                    },
                    resolved_attributes,
                    provider=provider,
                    prompt=str(config.get("prompt", "Extract attributes")),
                    prompt_version=str(config.get("prompt_version", "1")),
                    schema=schema,
                    model=model,
                    model_settings=model_settings,
                    profile_snapshot=config.get("llm_profile"),
                    images=images,
                    image_checksums=image_checksums,
                    cache=cache,
                    fuzzy=bool(config.get("fuzzy_matching", False)),
                )
                if cache_key and cache_claim is not None and cache_key in cache:
                    cached = cache[cache_key]
                    provenance = cached.get("provenance", {})
                    cache_claim.result = cached
                    cache_claim.usage = provenance.get("usage", {})
                item.data = dict(item.data) | result["values"]
                item.provenance = result["provenance"]
                scores = [
                    detail.get("confidence")
                    for detail in result["provenance"].values()
                    if detail.get("confidence") is not None
                ]
                item.confidence = min(scores) if scores else None
                combined_issues = [*image_issues.get(item.id, []), *result["issues"]]
                item.validation_summary = {"issues": combined_issues}
                item.status = "ready" if result["valid"] and not image_blocked else "needs_review"
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
                    for issue in result["issues"]
                )
                results[str(item.id)] = {
                    "valid": result["valid"] and not image_blocked,
                    "issues": combined_issues,
                }
            if cache_claim is not None and not cache_claim.result:
                db.delete(cache_claim)
            task.result = {"items": results}
        task.status = "completed"
        if not task.result:
            task.result = {"item_ids": task.payload.get("item_ids", [])}
        task.finished_at = now

        run = db.scalar(
            select(Run)
            .where(Run.id == run.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        completed_tasks, failed_tasks, total_tasks = db.execute(
            select(
                func.count().filter(RunTask.status == "completed"),
                func.count().filter(RunTask.status == "failed"),
                func.count(),
            ).where(RunTask.run_id == run.id)
        ).one()
        _update_run_progress(db, run)
        if run.cancel_requested:
            run.status = "cancelled"
        elif total_tasks and completed_tasks + failed_tasks == total_tasks:
            run.status = "failed" if failed_tasks else "completed"
    return {"ok": True, "status": "completed"}


@celery.task(name="app.process_run_task")
def process_run_task(task_id: str) -> dict:
    task_uuid = uuid.UUID(task_id)
    try:
        return _process_run_task(task_uuid)
    except Exception as exc:
        error = type(exc).__name__
        with SessionLocal.begin() as db:
            task = db.scalar(select(RunTask).where(RunTask.id == task_uuid).with_for_update())
            if task:
                task.status = "failed"
                task.error = error
                task.attempts += 1
                task.finished_at = datetime.now(UTC)
                run = db.get(Run, task.run_id)
                for item_id in task.payload.get("item_ids", []):
                    if item := db.get(CatalogItem, uuid.UUID(item_id)):
                        item.status = "failed"
                if run:
                    run.status = "failed"
                    _update_run_progress(db, run)
        raise RuntimeError(error) from None


def _create_export(export_uuid: uuid.UUID) -> dict:
    with SessionLocal.begin() as db:
        job = db.scalar(select(ExportJob).where(ExportJob.id == export_uuid).with_for_update())
        if not job:
            return {"ok": False, "error": "export not found"}
        if job.status == "completed":
            return {"ok": True, "object_key": job.object_key}
        job.status = "running"
        from .services.exports import export_blockers, export_csv, export_xlsx

        run = db.get(Run, job.run_id)
        blockers = export_blockers(db, run)
        if blockers["blocked"] and not job.override_blocking:
            job.status = "failed"
            job.error = "export_blocked"
            return {"ok": False, "error": "export_blocked", "details": blockers}
        items = db.scalars(
            select(CatalogItem)
            .where(CatalogItem.run_id == job.run_id)
            .order_by(CatalogItem.row_number)
        ).all()
        rows = [item.data | {"sku": item.sku, "ean": item.ean} for item in items]

        from .storage import copy_to_fileobj, put_bytes, put_fileobj

        if job.format == "csv":
            document = export_csv(rows)
        elif job.format == "xlsx":
            document = export_xlsx(rows)
        else:
            document = b""
        if job.format == "image_zip" or job.include_images:
            assets = db.scalars(select(ImageAsset).where(ImageAsset.run_id == job.run_id)).all()
            with TemporaryFile(mode="w+b") as output:
                with ZipFile(output, "w", ZIP_DEFLATED) as archive:
                    if job.format != "image_zip":
                        archive.writestr(f"catalog.{job.format}", document)
                    for asset in assets:
                        name = asset.object_key.rsplit("/", 1)[-1]
                        with archive.open(
                            f"images/{asset.id}_{name}", "w", force_zip64=True
                        ) as member:
                            copy_to_fileobj(asset.object_key, member)
                output.seek(0)
                object_key = f"exports/{job.id}.zip"
                put_fileobj(object_key, output, "application/zip")
        else:
            content_type = (
                "text/csv"
                if job.format == "csv"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            object_key = f"exports/{job.id}.{job.format}"
            put_bytes(object_key, document, content_type)
        job.object_key = object_key
        job.status = "completed"
    return {"ok": True, "object_key": object_key}


@celery.task(name="app.create_export")
def create_export(export_id: str) -> dict:
    export_uuid = uuid.UUID(export_id)
    try:
        return _create_export(export_uuid)
    except Exception as exc:
        error = type(exc).__name__
        with SessionLocal.begin() as db:
            job = db.get(ExportJob, export_uuid)
            if job:
                job.status = "failed"
                job.error = error
        raise RuntimeError(error) from None


@celery.task(name="app.purge_deleted_runs")
def purge_deleted_runs() -> dict:
    now = datetime.now(UTC)
    with SessionLocal.begin() as db:
        runs = db.scalars(
            select(Run).where(Run.deleted_at.is_not(None), Run.purge_after <= now)
        ).all()
        run_ids = [run.id for run in runs]
        object_keys = list(
            db.scalars(select(ImageAsset.object_key).where(ImageAsset.run_id.in_(run_ids))).all()
        )
        object_keys.extend(
            db.scalars(
                select(ExportJob.object_key).where(
                    ExportJob.run_id.in_(run_ids), ExportJob.object_key.is_not(None)
                )
            ).all()
        )
        input_keys = {run.input_object_key for run in runs if run.input_object_key}
        for key in input_keys:
            active_reference = db.scalar(
                select(func.count())
                .select_from(Run)
                .where(Run.input_object_key == key, Run.id.not_in(run_ids))
            )
            preflights = db.scalars(
                select(PreflightRecord).where(PreflightRecord.input_object_key == key)
            ).all()
            if (
                active_reference
                or not preflights
                or any(record.consumed_at is None for record in preflights)
            ):
                continue
            object_keys.append(key)
            for record in preflights:
                db.delete(record)
        from .storage import delete_keys

        delete_keys(object_keys)
        for run in runs:
            db.delete(run)
        return {"purged": len(runs)}


celery_app = celery
