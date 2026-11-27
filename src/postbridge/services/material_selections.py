"""Verified source selections for the existing search agent and content schedules."""
from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select

from postbridge.agent.scheduling import next_run_at_from_cron
from postbridge.config import get_settings
from postbridge.domain.errors import PostbridgeError, ValidationError
from postbridge.infrastructure.media_storage import managed_media_url
from postbridge.integrations.material_catalog import CatalogError, CatalogueScope, mentions, normalize_text
from postbridge.integrations.material_catalogues import CATALOGUES, open_catalogue
from postbridge.models.domain import (
    AgentRunOrm, AgentTaskOrm, ChannelOrm, MaterialSelectionOrm, MaterialSelectionUseOrm, MediaAssetOrm,
)
from postbridge.services.media_assets import store_media_asset
from postbridge.services.video_media import inspect_media
from postbridge.services.postbridge_workspace_content import _dump_extra, _load_extra, create_postbridge_content_item

logger = logging.getLogger(__name__)

SOURCE_GRAPH = "material_selection"
DRAFT_GRAPH = "material_draft"
MATERIAL_GRAPHS = (SOURCE_GRAPH, DRAFT_GRAPH)
REUSABLE_LICENSES = frozenset({
    "https://creativecommons.org/publicdomain/zero/1.0",
    "https://creativecommons.org/publicdomain/mark/1.0",
    "https://creativecommons.org/licenses/by/4.0",
})


Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class SelectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    providers: list[str] = Field(min_length=1, max_length=4)
    queries: list[Query] = Field(min_length=1, max_length=8)
    places: list[Query] = Field(default_factory=list, max_length=12)
    year_from: int = Field(default=1, ge=1, le=9999)
    year_to: int = Field(default=9999, ge=1, le=9999)
    first_topic: str | None = Field(default=None, min_length=1, max_length=400)
    first_queries: list[Query] = Field(default_factory=list, max_length=4)
    first_place: Query | None = None
    first_year: int | None = Field(default=None, ge=1, le=9999)
    min_frames: int = Field(default=3, ge=1, le=6)
    max_frames: int = Field(default=5, ge=1, le=6)


    @model_validator(mode="before")
    @classmethod
    def previous_single_source(cls, values):
        if isinstance(values, dict) and "provider" in values and "providers" not in values:
            return {**{key: value for key, value in values.items() if key != "provider"},
                    "providers": [values["provider"]]}
        return values


class DraftPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_task_id: UUID
    language: str = Field(pattern="^(ru|en)$")


class MaterialTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9:_-]+$")
    source: SelectionPolicy | None = None
    draft: DraftPolicy | None = None
    replace_task_id: UUID | None = None
    expected_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def material_error(code, message):
    return ValidationError(code=code, message=message, details={})


def task_hash(task):
    values = [task.channel_id, task.goal_text, task.editorial_instructions, task.schedule_cron,
              task.timezone, json.loads(task.task_config_json)]
    return sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def source_task(db, tenant_id, task_id):
    task = db.get(AgentTaskOrm, str(task_id))
    if (task and task.tenant_id == tenant_id and task.mode == "topic_scout"
            and task.status != "archived" and json.loads(task.task_config_json).get("materials", {}).get("source")):
        return task
    raise material_error("MATERIAL_SOURCE_INVALID", "Choose a material selection task from this workspace")


def save_task(db, *, tenant_id, channel_id, goal_text, editorial_instructions, schedule_cron,
              timezone, config, created_by, mode, autonomy_mode, content_item_id):
    try:
        settings = MaterialTaskConfig.model_validate(config)
        if (mode != "topic_scout" or not created_by or content_item_id or autonomy_mode != "draft_approval"
                or bool(settings.source) == bool(settings.draft)):
            raise ValueError
        ZoneInfo(timezone)
        if schedule_cron and (len(schedule_cron.split()) != 5 or not croniter.is_valid(schedule_cron)):
            raise ValueError
        if settings.source:
            source = settings.source
            if (any(provider not in CATALOGUES for provider in source.providers)
                    or len(source.providers) != len(set(source.providers)) or source.year_from > source.year_to or source.min_frames > source.max_frames
                    or (source.first_queries or source.first_place or source.first_year) and not source.first_topic
                    or source.first_year is not None and not source.year_from <= source.first_year <= source.year_to):
                raise ValueError
        if bool(settings.replace_task_id) != bool(settings.expected_hash) or settings.replace_task_id and not settings.source:
            raise ValueError
    except (ValueError, ZoneInfoNotFoundError, TypeError):
        raise material_error("MATERIAL_SCHEDULE_INVALID", "Check catalogue, search bounds, schedule and draft settings") from None
    channel = db.get(ChannelOrm, channel_id)
    if not channel or channel.tenant_id != tenant_id or channel.platform != "postbridge":
        raise material_error("MATERIAL_SCHEDULE_INVALID", "Choose a Postbridge editorial context in this workspace")
    if settings.draft:
        source_task(db, tenant_id, settings.draft.source_task_id)
    task_id = str(settings.replace_task_id or uuid5(NAMESPACE_URL, f"postbridge-material-task:{tenant_id}:{settings.request_key}"))
    # Lock on the stable parent when creating; all existing-task edits and occurrences lock the task.
    if not settings.replace_task_id:
        db.scalar(select(ChannelOrm.id).where(ChannelOrm.id == channel_id).with_for_update())
    task = db.scalar(select(AgentTaskOrm).where(AgentTaskOrm.id == task_id).with_for_update())
    canonical = {"materials": settings.model_dump(mode="json", exclude={"replace_task_id", "expected_hash"})}
    wanted = (channel_id, goal_text, editorial_instructions, schedule_cron, timezone, canonical)
    if task:
        if task.tenant_id != tenant_id or task.created_by != created_by or task.status == "archived":
            raise material_error("MATERIAL_SCHEDULE_INVALID", "Choose a material task you manage in this workspace")
        current = (task.channel_id, task.goal_text, task.editorial_instructions, task.schedule_cron,
                   task.timezone, json.loads(task.task_config_json))
        if current == wanted:
            return task
        if not settings.replace_task_id or not current[-1].get("materials", {}).get("source") or task_hash(task) != settings.expected_hash:
            raise material_error("MATERIAL_SCHEDULE_CHANGED", "Reload the existing material schedule before editing")
        if task.channel_id != channel_id:
            raise material_error("MATERIAL_SCHEDULE_INVALID", "Keep the same editorial context when editing materials")
    elif settings.replace_task_id:
        raise material_error("MATERIAL_SOURCE_INVALID", "The material schedule is unavailable")
    else:
        task = AgentTaskOrm(id=task_id, tenant_id=tenant_id, channel_id=channel_id, mode=mode,
            max_candidates_per_run=1, autonomy_mode=autonomy_mode, created_by=created_by, status="active")
        db.add(task)
    task.goal_text, task.editorial_instructions, task.schedule_cron, task.timezone = wanted[1:5]
    task.task_config_json = json.dumps(canonical)
    task.next_run_at = next_run_at_from_cron(schedule_cron, timezone=timezone) if task.status == "active" else None
    db.flush()
    return task


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def enqueue(db, task_id, tenant_id, *, scheduled=False, occurrence_key=None, now=None):
    now = now or datetime.now(UTC)
    task = db.scalar(select(AgentTaskOrm).where(AgentTaskOrm.id == task_id, AgentTaskOrm.tenant_id == tenant_id)
                     .with_for_update(skip_locked=scheduled).execution_options(populate_existing=True))
    config = json.loads(task.task_config_json).get("materials") if task else None
    if not config or task.status == "archived":
        return None
    if scheduled and (task.status != "active" or not task.next_run_at or _utc(task.next_run_at) > now):
        return None
    key = f"slot:{_utc(task.next_run_at).isoformat()}" if scheduled else f"manual:{occurrence_key or uuid4()}"
    run_id = str(uuid5(NAMESPACE_URL, f"postbridge-material-run:{task.id}:{key}"))
    if existing := db.get(AgentRunOrm, run_id):
        return existing
    graph = SOURCE_GRAPH if config.get("source") else DRAFT_GRAPH
    active = db.scalar(select(AgentRunOrm.id).where(AgentRunOrm.agent_task_id == task.id,
        AgentRunOrm.status == "queued", AgentRunOrm.graph_name.in_(MATERIAL_GRAPHS)).limit(1))
    run = AgentRunOrm(id=run_id, tenant_id=tenant_id, channel_id=task.channel_id, agent_task_id=task.id,
        graph_name=graph, trigger_type="scheduled" if scheduled else "manual", user_request=task.goal_text,
        status="cancelled" if active else "queued", completed_at=now if active else None,
        error_code="MATERIAL_RUN_OVERLAP" if active else None,
        error_message="A material run is already pending" if active else None,
        trace_json=json.dumps({"material_request": config, "editorial_instructions": task.editorial_instructions}))
    db.add(run)
    db.flush()
    if not active and graph == DRAFT_GRAPH:
        try:
            bundle = claim(db, tenant_id, config["draft"]["source_task_id"], task.id, run.id)
            trace = json.loads(run.trace_json)
            run.trace_json = json.dumps({**trace, "material_selection": bundle})
        except PostbridgeError as exc:
            _finish(run, exc)
    task.last_run_at = now
    if scheduled:
        task.next_run_at = next_run_at_from_cron(task.schedule_cron, base=now, timezone=task.timezone)
    return run


def _policy_revision(config):
    return sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def _ready(db, tenant_id, source_task_id):
    return select(MaterialSelectionOrm).join(AgentRunOrm, AgentRunOrm.id == MaterialSelectionOrm.id).where(
        MaterialSelectionOrm.tenant_id == tenant_id, MaterialSelectionOrm.source_task_id == source_task_id,
        AgentRunOrm.status == "completed")


def claim(db, tenant_id, source_task_id, task_id, run_id):
    previous = db.get(MaterialSelectionUseOrm, run_id)
    if previous:
        selected = db.get(MaterialSelectionOrm, previous.selection_id)
        if selected and selected.tenant_id == tenant_id and previous.task_id == task_id:
            return selected.snapshot
        raise material_error("MATERIAL_SOURCE_INVALID", "The selection does not belong to this run")
    source = source_task(db, tenant_id, source_task_id)
    revision = _policy_revision(json.loads(source.task_config_json)["materials"]["source"])
    seen = select(MaterialSelectionUseOrm.selection_id).where(MaterialSelectionUseOrm.task_id == task_id)
    ready = db.scalars(_ready(db, tenant_id, source_task_id).where(MaterialSelectionOrm.id.not_in(seen))
                       .order_by(AgentRunOrm.completed_at, MaterialSelectionOrm.id))
    for selection in ready:
        if selection.snapshot.get("source_revision") != revision:
            continue
        frames = selection.snapshot["frames"]
        ids = {frame["asset_id"] for frame in frames}
        managed = set(db.scalars(select(MediaAssetOrm.id).where(MediaAssetOrm.tenant_id == tenant_id,
                                                                  MediaAssetOrm.id.in_(ids))))
        if ids == managed and frames:
            db.add(MaterialSelectionUseOrm(run_id=run_id, task_id=task_id, selection_id=selection.id))
            db.flush()
            return selection.snapshot
    raise material_error("MATERIAL_NOT_READY", "No unused verified selection with available files is ready for this schedule")


def _eligible(record, policy, used, *, first=False):
    return (record.reference not in used and record.license.rstrip("/") in REUSABLE_LICENSES
            and policy.year_from <= record.year <= min(policy.year_to, datetime.now(UTC).year)
            and (not policy.places or any(mentions(record.place, place) for place in policy.places))
            and (not first or not policy.first_place or mentions(record.place, policy.first_place)))


def _first_year(policy):
    if policy.first_year:
        return policy.first_year
    dates = set(re.findall(r"\b[1-9]\d{3}\b", policy.first_topic or ""))
    return int(dates.pop()) if len(dates) == 1 else None


def _verified_image(data, mime):
    suffixes = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    if mime not in suffixes or not 0 < len(data) <= 10_000_000:
        return False
    with TemporaryDirectory(prefix="postbridge-catalogue-image-") as directory:
        image = Path(directory) / f"asset.{suffixes[mime]}"
        image.write_bytes(data)
        try:
            return inspect_media(image)["kind"] == "image"
        except PostbridgeError as exc:
            if exc.code != "VIDEO_INVALID_MEDIA":
                raise
            return False


def _choose(catalog, policy, ready, *, first, deadline):
    used_references = {frame["reference"] for row in ready for frame in row.snapshot["frames"]}
    used_digests = {frame["sha256"] for row in ready for frame in row.snapshot["frames"]}
    used_topics = {row.snapshot["edition_key"] for row in ready}
    searches = [tuple(policy.first_queries or [policy.first_topic])] if first else [(query,) for query in policy.queries]
    first_year = _first_year(policy) if first else None
    lookup_cache = {}
    for phrases in dict.fromkeys(searches):
        if monotonic() >= deadline:
            break
        candidates = (item for phrase in phrases for item in catalog.search(phrase)[:18])
        matches = list(dict.fromkeys(candidates))[:36]
        groups = defaultdict(dict)
        for item in matches:
            if monotonic() >= deadline:
                break
            if item not in lookup_cache:
                lookup_cache[item] = catalog.photo(item)
            record = lookup_cache[item]
            if (record and _eligible(record, policy, used_references, first=first)
                    and (not first_year or record.year == first_year)):
                groups[normalize_text(record.place), record.year][record.reference] = record
        for (place, year), records in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0])):
            if len(records) < policy.min_frames or monotonic() >= deadline:
                continue
            topic_key = sha256(f"{normalize_text(policy.first_topic if first else phrases[0])}:{place}:{year}".encode()).hexdigest()
            if topic_key in used_topics:
                continue
            photos = []
            digests = used_digests.copy()
            for record in sorted(records.values(), key=lambda item: item.reference):
                if monotonic() >= deadline or len(photos) == policy.max_frames:
                    break
                data, mime = catalog.download(record)
                if not _verified_image(data, mime):
                    continue
                digest = sha256(data).hexdigest()
                if digest not in digests:
                    photos.append((record, data, mime, digest))
                    digests.add(digest)
            if len(photos) >= policy.min_frames:
                return topic_key, policy.first_topic if first else photos[0][0].title, photos
    return None


def find_selection(policy, ready):
    attempts = []
    unavailable = set()
    phases = (True, False) if not ready and policy.first_topic else (False,)
    deadline = monotonic() + 76
    for first in phases:
        first_year = _first_year(policy) if first else None
        places = (policy.first_place,) if first and policy.first_place else tuple(policy.places)
        scope = CatalogueScope(places, first_year or policy.year_from, first_year or policy.year_to)
        for catalogue_id in policy.providers:
            if catalogue_id in unavailable:
                continue
            result = None
            try:
                if catalogue_id not in CATALOGUES:
                    raise CatalogError("MATERIAL_CATALOG_UNAVAILABLE", "Catalogue is unavailable")
                local_deadline = min(deadline, monotonic() + 30)
                with open_catalogue(catalogue_id, scope, local_deadline) as catalogue:
                    result = _choose(catalogue, policy, ready, first=first, deadline=local_deadline)
                status = "ready" if result else "insufficient"
            except CatalogError as exc:
                unavailable.add(catalogue_id)
                status = exc.code
            attempts.append({"provider": catalogue_id, "status": status, "first_topic": first})
            if result:
                return catalogue_id, *result, attempts
    detail = ", ".join(f"{item['provider']} ({item['status']})" for item in attempts)
    code = "MATERIAL_CATALOG_UNAVAILABLE" if len(unavailable) == len(policy.providers) else "MATERIAL_NO_VERIFIED_SELECTION"
    raise CatalogError(code, f"No verified material selection ({detail})", attempts=attempts)


def _finish(run, exc=None):
    run.completed_at = datetime.now(UTC)
    if exc:
        run.status = "failed"
        run.error_code = exc.code
        run.error_message = (exc.message if isinstance(exc, PostbridgeError) else str(exc))[:512]


def _prepare(db, task, run, config):
    policy = SelectionPolicy.model_validate(config)
    ready = db.scalars(_ready(db, task.tenant_id, task.id)).all()
    provider, edition_key, topic, photos, attempts = find_selection(policy, ready)
    frames = []
    for record, data, mime, digest in photos:
        stored = store_media_asset(db, tenant_id=task.tenant_id, data=data, content_type=mime,
                                   deduplicate=True, library_name=record.title, commit=False)
        frames.append({**record.model_dump(exclude={"image_url"}, exclude_none=True), "provider": provider,
                       "sha256": digest, "asset_id": stored["media_asset_id"]})
    snapshot = {"version": run.id, "source_task_id": task.id, "provider": provider, "edition_key": edition_key,
                "source_revision": _policy_revision(config),
                "topic": topic, "place": frames[0]["place"], "year": frames[0]["year"], "frames": frames}
    db.add(MaterialSelectionOrm(id=run.id, tenant_id=task.tenant_id, source_task_id=task.id,
                                edition_key=edition_key, snapshot=snapshot))
    run.status = "completed"
    return {"material_selection": snapshot, "catalogue_attempts": attempts}


def source_lines(bundle, language):
    heading = "Источники" if language == "ru" else "Sources"
    lines = [f"{frame['creator'][:100]} · {frame['credit'][:100]} · {frame['license']} · {frame['record_url']}"
             for frame in bundle["frames"]]
    return f"{heading}\n" + "\n".join(dict.fromkeys(lines))


def publication_caption(bundle, language, description, addition=""):
    labels = {"ru": "Сделано в Postbridge", "en": "Made with Postbridge"}
    attribution = labels.get(language, labels["en"])
    if addition in labels.values():
        addition = ""
    return "\n\n".join(part for part in (description, addition, source_lines(bundle, language), attribution) if part)


def uses_verified_year(bundle, title, *texts):
    year = str(bundle["year"])
    pattern = r"\b\d{4}\b"
    known = {year, year.zfill(4)}
    return (any(re.search(rf"\b{value}\b", title) for value in known)
            and all(value in known for text in (title, *texts) if text for value in re.findall(pattern, text)))


def _draft(db, task, run, config, bundle):
    language = DraftPolicy.model_validate(config).language
    urls = []
    for frame in bundle["frames"]:
        asset = db.get(MediaAssetOrm, frame["asset_id"])
        if not asset or asset.tenant_id != run.tenant_id:
            raise material_error("MATERIAL_NOT_READY", "A verified attachment is no longer available")
        urls.append(managed_media_url(asset.id, asset.object_key))
    text = [f"{bundle['place']}, {bundle['year']}.", *(f"• {frame['title']}" for frame in bundle["frames"]),
            source_lines(bundle, language)]
    content = create_postbridge_content_item(db, tenant_id=run.tenant_id, author_user_id=task.created_by,
        title=bundle["topic"][:500], content_md="\n\n".join(text), content_plain=None, media_url=urls[0], media_urls=urls,
        summary=None, link_url=None, cta=None, tags=None, author=None, cover_image_url=None, status="draft")
    content.language = language
    extra = _load_extra(content.body_structured_json)
    content.body_structured_json = _dump_extra({**extra, "material_selection_version": bundle["version"]})
    run.content_item_id, run.status = content.id, "awaiting_review"
    return {"material_selection": bundle, "content_item_id": content.id}


def _assert_actor(task):
    settings = get_settings()
    if settings.postbridge_app_mode != "saas":
        return
    with httpx.Client(timeout=10) as client:
        response = client.post(f"{settings.saas_base_url.rstrip('/')}/internal/agent/authorize-material-run",
            headers={"X-Core-Event-Token": settings.status_event_webhook_token},
            json={"tenant_id": task.tenant_id, "actor_id": task.created_by})
    if response.status_code == 401:
        raise material_error("MATERIAL_AUTH_SERVICE_UNAVAILABLE", "Workspace access could not be verified")
    if response.status_code in {403, 404}:
        raise material_error("MATERIAL_ACTOR_REVOKED", "The schedule owner no longer has workspace editing access")
    response.raise_for_status()


def process(db, run_id):
    run = db.get(AgentRunOrm, run_id)
    if not run or run.graph_name not in MATERIAL_GRAPHS or run.status != "queued":
        return
    task = db.scalar(select(AgentTaskOrm).where(AgentTaskOrm.id == run.agent_task_id,
        AgentTaskOrm.tenant_id == run.tenant_id).with_for_update(skip_locked=True))
    if not task:
        return
    db.refresh(run)
    if run.status != "queued":
        return
    run.started_at = datetime.now(UTC)
    trace = json.loads(run.trace_json)
    config = trace["material_request"]
    try:
        with db.begin_nested():
            if task.status == "archived":
                raise material_error("MATERIAL_SOURCE_INVALID", "The material schedule was archived before execution")
            if run.trigger_type == "scheduled" or run.graph_name == SOURCE_GRAPH:
                _assert_actor(task)
            if run.graph_name == SOURCE_GRAPH:
                result = _prepare(db, task, run, config["source"])
            else:
                result = _draft(db, task, run, config["draft"], trace["material_selection"])
        run.trace_json = json.dumps({"result": result})
        _finish(run)
    except (CatalogError, PostbridgeError) as exc:
        if isinstance(exc, CatalogError) and exc.attempts:
            run.trace_json = json.dumps({"result": {"catalogue_attempts": exc.attempts}})
        _finish(run, exc)
    except Exception as exc:
        attempts = trace.get("attempts", 0) + 1
        logger.warning("Material run %s attempt %d failed: %s", run.id, attempts, type(exc).__name__)
        run.trace_json = json.dumps({**trace, "attempts": attempts})
        if attempts >= 3:
            _finish(run, material_error("MATERIAL_PROCESSING_FAILED", "Material processing failed after three attempts"))
    db.commit()
