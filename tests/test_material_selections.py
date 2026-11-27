"""Evidence, tenant isolation and the real worker path with simulated providers."""
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
import json
import struct
import zlib
from uuid import uuid4

from sqlalchemy import func, select

from postbridge.agent.storage import cleanup_agent_runtime, create_agent_task
from postbridge.integrations.material_catalog import CatalogError, MaterialRecord
from postbridge.integrations.video_delivery import VideoDeliveryReceipt
from postbridge.models.domain import (
    AgentRunOrm, ChannelOrm, ContentItemOrm, MaterialSelectionOrm, MaterialSelectionUseOrm,
    PublicationTargetOrm, VideoAgentExecutionOrm, VideoBatchItemOrm, VideoBatchOrm, VideoProductionJobOrm,
)
from postbridge.services import material_selections as materials, video_autopilot
from postbridge.services.media_assets import delete_media_asset
from postbridge.services.publication_target_executor import PublicationTargetExecutor
from postbridge.services.video_auto_publication import (
    approve_auto_post, auto_approval, execution_revisions, prepare_auto_posts, schedule_bridges,
)
from postbridge.services.video_schedules import enqueue_video_agent_run, synchronize_video_agent_runs
from postbridge.services.video_post_drafts import PrepareVideoPost, prepare_post_draft
from postbridge.workers import video_batch_tasks, video_schedule_tasks, video_tasks, tasks

from tests.test_video_autopilot import auto
from tests.test_video_bridges import video_bridge
from tests.test_video_post_drafts import ready
from tests.test_video_publication import publication
from tests.test_video_schedules import scheduled
from tests.test_video_speech import speech
from tests.test_video_studio import PNG, error, state

LICENSE = "https://creativecommons.org/publicdomain/zero/1.0/"


class Catalogue:
    search_language = "en"

    def __init__(self, subjects):
        self.subjects = subjects
        self.queries = []

    def search(self, phrase):
        self.queries.append(phrase)
        return [(f"{phrase}-{i}", phrase) for i in range(5)] if phrase in self.subjects else []

    def photo(self, lookup):
        key, subject = lookup
        key = key.replace(" ", "-")
        year, place = self.subjects[subject]
        index = int(key[-1])
        return MaterialRecord(reference=key, record_url=f"https://example.test/cards/{key}",
            title=f"{subject.title()} {index + 1}", year=year, catalog_date=str(year),
            place=place if index != 3 else "Elsewhere", creator="Archive photographer", credit="Municipal archive",
            license=LICENSE if index != 4 else "https://example.test/restricted", image_url=f"https://example.test/jpeg/{key}")

    def download(self, photo):
        data = b"Fixture\x00" + photo.reference.encode()
        chunk = b"tEXt" + data
        png = PNG[:-12] + struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk)) + PNG[-12:]
        return png, "image/png"


def setup_catalogue(monkeypatch, name, subjects):
    catalogue = Catalogue(subjects)
    monkeypatch.setitem(materials.CATALOGUES, name, catalogue)
    monkeypatch.setattr(materials, "open_catalogue", lambda name, scope, deadline: nullcontext(materials.CATALOGUES[name]))
    return catalogue


def task(db, tenant, channel, *, config, mode="topic_scout", brief="Stories from the archive", cron="0 9 * * *"):
    return create_agent_task(db, tenant_id=tenant, channel_id=channel, mode=mode, goal_text=brief,
        editorial_instructions=None, schedule_cron=cron, timezone="UTC", max_candidates_per_run=1,
        autonomy_mode="draft_approval", provider_config_id=None, model_name=None, content_item_id=None,
        task_config=config, created_by="actor")


def selector(db, tenant, channel, *, provider, queries, place, first_topic=None, first_queries=None):
    return task(db, tenant, channel, config={"materials": {"request_key": str(uuid4()),
        "source": {"providers": [provider] if isinstance(provider, str) else provider, "queries": queries, "places": [place], "year_from": 1870, "year_to": 1930,
                   "first_topic": first_topic, "first_queries": first_queries or [], "min_frames": 2, "max_frames": 3}}})


def video_config(source, **options):
    key = str(uuid4())
    return {"request_key": key, "material_source_task_id": source.id, "max_units_per_run": 500,
        "max_units_per_month": 2500,
        "video": {"brief": "Stories from the archive", "request_key": key, "mode": "autopilot",
                  "language": options.pop("language", "en"), "duration_seconds": 12,
                  "narration_enabled": options.pop("narration", False)},
        **options}


def produce_selection(db, source, key):
    occurrence = materials.enqueue(db, source.id, source.tenant_id, occurrence_key=key)
    db.commit()
    materials.process(db, occurrence.id)
    db.refresh(occurrence)
    return occurrence


def test_same_schedule_switches_catalogue_with_pinned_history_and_second_workspace_draft(auto, monkeypatch):
    db, tenant, other, *_ = auto
    first = ChannelOrm(id=str(uuid4()), tenant_id=tenant, platform="postbridge", kind="source", title="First",
                       external_id="editorial-first", status="connected")
    second = ChannelOrm(id=str(uuid4()), tenant_id=other, platform="postbridge", kind="source", title="Second",
                        external_id="editorial-other", status="connected")
    db.add_all([first, second])
    db.commit()
    setup_catalogue(monkeypatch, "city-a", {"ferry": (1909, "Dublin"), "docks": (1901, "Dublin")})
    setup_catalogue(monkeypatch, "city-b", {"bridge": (1874, "Dublin"), "market": (1920, "Lisbon")})
    source = selector(db, tenant, first.id, provider="city-a", queries=["ferry", "docks"], place="Dublin")
    video = task(db, tenant, first.id, config=video_config(source), mode="video_autopilot")
    db.commit()
    before = enqueue_video_agent_run(db, tenant_id=tenant, task_id=video.id, occurrence_key="missing-0001")
    assert before.error_code == "MATERIAL_NOT_READY"
    assert not db.get(VideoAgentExecutionOrm, before.id).input_snapshot.get("material_selection")
    first_pick = produce_selection(db, source, "source-original")
    assert first_pick.status == "completed", first_pick.error_message
    pending_old = produce_selection(db, source, "source-backlog")
    assert pending_old.status == "completed", pending_old.error_message
    consumed = enqueue_video_agent_run(db, tenant_id=tenant, task_id=video.id, occurrence_key="pinned-0001")
    db.commit()
    frozen = db.get(VideoAgentExecutionOrm, consumed.id).input_snapshot
    assert frozen["material_selection"]["version"] == first_pick.id
    old = json.loads(source.task_config_json)["materials"]
    old["source"].update(providers=["city-b"], queries=["bridge"])
    replacement = task(db, tenant, first.id, config={"materials": {**old, "request_key": str(uuid4()),
        "replace_task_id": source.id, "expected_hash": materials.task_hash(source)}})
    assert replacement.id == source.id
    template = video.task_config_json
    consumed.status = "completed"
    db.commit()
    assert enqueue_video_agent_run(db, tenant_id=tenant, task_id=video.id, occurrence_key="pinned-0001").id == consumed.id
    next_pick = produce_selection(db, source, "source-replacement")
    assert next_pick.status == "completed", next_pick.error_message
    new = enqueue_video_agent_run(db, tenant_id=tenant, task_id=video.id, occurrence_key="pinned-0002")
    assert db.get(VideoAgentExecutionOrm, new.id).input_snapshot["material_selection"]["version"] == next_pick.id
    assert db.get(VideoAgentExecutionOrm, consumed.id).input_snapshot == frozen
    assert video.task_config_json == template
    assert db.scalar(select(func.count()).select_from(MaterialSelectionUseOrm)) == 2
    with error("MATERIAL_SOURCE_INVALID"):
        task(db, other, second.id, config=video_config(source), mode="video_autopilot")
    db.rollback()
    # Separate tenant, location, period, one language and a text draft.
    other_source = selector(db, other, second.id, provider="city-b", queries=["market"], place="Lisbon")
    draft_task = task(db, other, second.id, config={"materials": {"request_key": str(uuid4()),
        "draft": {"source_task_id": other_source.id, "language": "en"}}}, cron=None)
    other_pick = produce_selection(db, other_source, "other-source")
    other_video = task(db, other, second.id, config=video_config(other_source), mode="video_autopilot")
    other_video_run = enqueue_video_agent_run(db, tenant_id=other, task_id=other_video.id, occurrence_key="draft-video")
    assert db.get(VideoAgentExecutionOrm, other_video_run.id).input_snapshot["material_selection"]["version"] == other_pick.id
    db.commit()
    monkeypatch.setattr(video_schedule_tasks, "SESSION_LOCAL", video_tasks.SESSION_LOCAL)

    def storyboard(path, body, correlation):
        ctx = json.loads(body["messages"][-1]["content"])
        order = ctx["required_asset_order"]
        output = {"scenes": [{"asset_id": aid, "duration_seconds": 12 / len(order), "caption": "Lisbon"}
                  for aid in order], "publication": {"title": "Lisbon, 1920", "description": "A market in Lisbon."}}
        return {"usage": {"total_tokens": 7}, "choices": [{"message": {"content": json.dumps(output)}}]}

    monkeypatch.setattr(video_autopilot, "gateway_json", storyboard)
    video_schedule_tasks.prepare_video_agent_run.run(other_video_run.id)
    db.expire_all()
    video_execution = db.get(VideoAgentExecutionOrm, other_video_run.id)
    from postbridge.services.video_studio import get_project
    project = get_project(db, other, video_execution.project_id)
    video_tasks.process_video_job.run(project["current"]["job_id"])
    db.expire_all()
    video_post = prepare_post_draft(db, other, project["id"], PrepareVideoPost(expected_version=1))
    video_content = db.get(ContentItemOrm, video_post["content_item_id"])
    assert (video_content.tenant_id, video_content.language, video_content.status) == (other, "en", "draft")
    assert "A market in Lisbon." in video_content.body_markdown and "https://example.test/cards/" in video_content.body_markdown
    draft = produce_selection(db, draft_task, "manual-draft")
    assert draft.status == "awaiting_review", draft.error_message
    content = db.get(ContentItemOrm, draft.content_item_id)
    assert (content.tenant_id, content.language, content.status) == (other, "en", "draft")
    assert "Lisbon, 1920" in content.body_markdown and "Sources" in content.body_markdown
    assert len(content.media_urls) == 3 and len(set(content.media_urls)) == 3
    assert produce_selection(db, draft_task, "manual-draft").content_item_id == content.id
    assert db.scalar(select(func.count()).select_from(PublicationTargetOrm)) == 0
    frame = db.get(MaterialSelectionOrm, other_pick.id).snapshot["frames"][0]
    with error("VALIDATION_MEDIA_ASSET_IN_USE"):
        delete_media_asset(db, tenant_id=other, media_asset_id=frame["asset_id"])
    db.rollback()
    other_pick.created_at = datetime.now(UTC) - timedelta(days=365)
    db.commit()
    cleanup_agent_runtime(db, tenant_id=other, retention_days=1)
    assert db.get(AgentRunOrm, other_pick.id) is not None


def test_existing_scheduler_recovers_a_committed_slot_without_duplicate_work(scheduled, monkeypatch):
    db, tenant, _, _, channel = scheduled
    catalogue = setup_catalogue(monkeypatch, "catalogue", {"canal": (1899, "Utrecht")})
    source = selector(db, tenant, channel, provider="catalogue", queries=["canal"], place="Utrecht")
    source.next_run_at = datetime.now(UTC) - timedelta(minutes=2)
    db.commit()
    deliveries = []
    monkeypatch.setattr(tasks.process_agent_material_run, "delay", deliveries.append)
    assert tasks.process_due_agent_tasks_task.run() == 1
    assert tasks.process_due_agent_tasks_task.run() == 1  # Broker acknowledgement can be lost.
    assert len(set(deliveries)) == 1
    for run_id in deliveries:
        tasks.process_agent_material_run.run(run_id)
    assert catalogue.queries == ["canal"]
    db.expire_all()
    assert db.get(AgentRunOrm, deliveries[0]).status == "completed"
    assert tasks.process_due_agent_tasks_task.run() == 0


def test_ordered_sources_continue_after_backoff_and_insufficient_material(scheduled, monkeypatch):
    db, tenant, _, _, channel = scheduled
    unavailable = setup_catalogue(monkeypatch, "retry", {})
    empty = setup_catalogue(monkeypatch, "empty", {})
    good = setup_catalogue(monkeypatch, "good", {"harbour": (1901, "Dublin")})

    def backoff(phrase):
        raise CatalogError("MATERIAL_CATALOG_BACKOFF", "Retry later")

    monkeypatch.setattr(unavailable, "search", backoff)
    source = selector(db, tenant, channel, provider=["retry", "empty", "good"], queries=["harbour"], place="Dublin")
    run = produce_selection(db, source, "fall-through")
    assert run.status == "completed"
    trace = json.loads(run.trace_json)["result"]
    assert [attempt["status"] for attempt in trace["catalogue_attempts"]] == [
        "MATERIAL_CATALOG_BACKOFF", "insufficient", "ready"]
    frames = trace["material_selection"]["frames"]
    assert len(frames) == 3 and {frame["provider"] for frame in frames} == {"good"}
    assert all(frame["record_url"].startswith("https://example.test/cards/") for frame in frames)
    assert produce_selection(db, source, "fall-through").id == run.id
    assert empty.queries == good.queries == ["harbour"]
    source.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    due = materials.enqueue(db, source.id, tenant, scheduled=True)
    db.commit()
    materials.process(db, due.id)
    assert due.status == "failed" and due.error_code == "MATERIAL_NO_VERIFIED_SELECTION"
    assert source.next_run_at is not None
    assert db.scalar(select(func.count()).select_from(MaterialSelectionOrm)) == 1
    assert "retry" in due.error_message and "good" in due.error_message


def test_two_editions_use_frozen_frames_and_deliver_four_localized_shorts(speech, publication, monkeypatch):
    db, tenant, _, _, channels = publication
    monkeypatch.setattr(video_batch_tasks, "SESSION_LOCAL", video_tasks.SESSION_LOCAL)
    monkeypatch.setattr(video_schedule_tasks, "SESSION_LOCAL", video_tasks.SESSION_LOCAL)
    first, second = [video_bridge(db, tenant, channel) for channel in channels]
    second.source_channel_id = first.source_channel_id
    channels[1].external_id = "43"
    from postbridge.infrastructure.crypto.credentials import encrypt_credential_secret
    from postbridge.models.domain import ChannelCredentialOrm
    credential = db.scalar(select(ChannelCredentialOrm).where(ChannelCredentialOrm.channel_id == channels[1].id))
    credential.encrypted_secret = encrypt_credential_secret(json.dumps({"channel_id": "43", "access_token": "fixture-token"}))
    db.commit()

    catalogue = setup_catalogue(monkeypatch, "local-heritage", {"walking path": (1900, "Paris"),
        "rail exhibition": (1900, "Paris"), "market": (1902, "Paris")})
    source = selector(db, tenant, first.source_channel_id, provider="local-heritage", queries=["unavailable", "market"],
                      place="Paris", first_topic="Париж, 1900. Тротуар, который ехал сам",
                      first_queries=["walking path", "rail exhibition"])
    bridges = schedule_bridges(db, tenant, first.source_channel_id)["items"]
    policy = {"confirmed": True, "bridge_ids": [first.id, second.id],
        "expected_bridges": {b["id"]: b["settings_hash"] for b in bridges},
        "language_bridge_ids": {"ru": [first.id], "en": [second.id]}, "caption": "City archive profile"}
    video = task(db, tenant, first.source_channel_id, mode="video_autopilot", config=video_config(source, language="ru",
                 narration=True, variants=[{"language": "en", "voice": "Charon"}], auto_publication=policy))
    db.commit()

    old_gateway = video_autopilot.gateway_json
    storyboards = []
    translations = []

    def gateway(path, body, correlation):
        if path.endswith("/completions"):
            ctx = json.loads(next(m for m in body["messages"] if m["role"] == "user")["content"])
            if "required_asset_order" in ctx:
                storyboards.append(ctx)
                if len(storyboards) == 1:  # Strongest invariant: the model cannot replace a fixed frame.
                    order = [*ctx["required_asset_order"][:-1], str(uuid4())]
                else:
                    order = ctx["required_asset_order"]
                year = ctx["materials"][0]["year"]
                plan = {"scenes": [{"asset_id": aid, "duration_seconds": 12 / len(order),
                    "caption": f"Кадр {i + 1}", "narration_text": f"Архивный кадр номер {i + 1}."}
                    for i, aid in enumerate(order)],
                    "publication": {"title": f"Город, {year}", "description": f"Архивные снимки города за {year} год."}}
            else:
                translations.append(ctx)
                year = ctx["publication"]["title"].rsplit(" ", 1)[-1]
                plan = {"scenes": [{"caption": f"Frame {i + 1}", "narration_text": f"Archive photograph number {i + 1}."}
                    for i, scene in enumerate(ctx["scenes"])],
                    "publication": {"title": f"The city, {year}", "description": f"Photographs from {year}."}}
            return {"usage": {"total_tokens": 7}, "choices": [{"message": {"content": json.dumps(plan)}}]}
        assert not path.endswith("/images/generations")
        return old_gateway(path, body, correlation)

    monkeypatch.setattr(video_autopilot, "gateway_json", gateway)
    sent = []

    class FakePublisher:
        def publish_video(self, channel, post, creds, context):
            sent.append((channel, post))
            return VideoDeliveryReceipt(f"fake-delivery-{len(sent)}")

    for index in range(2):
        selected = produce_selection(db, source, f"source-run-{index}")
        assert selected.status == "completed", selected.error_message
        bundle = db.get(MaterialSelectionOrm, selected.id).snapshot
        assert "Тротуар" in bundle["topic"] if index == 0 else "Market" in bundle["topic"]
        run = enqueue_video_agent_run(db, tenant_id=tenant, task_id=video.id, occurrence_key=f"video-run-{index}")
        db.commit()
        assert enqueue_video_agent_run(db, tenant_id=tenant, task_id=video.id, occurrence_key=f"video-run-{index}").id == run.id
        video_schedule_tasks.prepare_video_agent_run.run(run.id)
        db.expire_all()
        execution = db.get(VideoAgentExecutionOrm, run.id)
        assert execution.input_snapshot["material_selection"] == bundle
        for _ in range(7):
            batches = db.scalars(select(VideoBatchOrm).where(VideoBatchOrm.tenant_id == tenant)).all()
            for batch in batches:
                if batch.id != execution.batch_id and batch.policy_json.get("root_batch_id") != execution.batch_id:
                    continue
                video_batch_tasks.advance_video_batch.run(batch.id)
                db.expire_all()
                for item in db.scalars(select(VideoBatchItemOrm).where(VideoBatchItemOrm.batch_id == batch.id,
                                                                        VideoBatchItemOrm.status == "running")).all():
                    job = db.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == item.revision_id))
                    video_tasks.process_video_job.run(job.id)
                    db.expire_all()
            synchronize_video_agent_runs(db)
            db.commit()
            if db.get(AgentRunOrm, run.id).status == "awaiting_review":
                break
        assert db.get(AgentRunOrm, run.id).status == "awaiting_review"
        revisions = execution_revisions(db, execution)
        assert len(revisions) == 2
        jobs = [db.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.revision_id == r.id)) for r in revisions]
        assert all(job.progress_json["material_selection"] == bundle and job.progress_json["output_duration_seconds"] > 0
                   for job in jobs)
        assert [s["asset_id"] for s in jobs[0].progress_json["scenes"]] == [s["asset_id"] for s in jobs[1].progress_json["scenes"]]
        posts = prepare_auto_posts(db, tenant, run.id)["items"]
        assert len(posts) == 2
        assert prepare_auto_posts(db, tenant, run.id)["items"] == posts
        publications = {}
        for post in posts:
            cid = post["content_item_id"]
            content = db.get(ContentItemOrm, cid)
            approval = auto_approval(db, tenant, run.id, cid)
            view = approve_auto_post(db, tenant, run.id, cid, approval)
            assert approve_auto_post(db, tenant, run.id, cid, approval)["approval_id"] == view["approval_id"]
            assert len(view["targets"]) == 1
            target_id = view["targets"][0]["id"]
            executor = PublicationTargetExecutor(db, FakePublisher())
            assert executor.run(target_id) == 1
            assert executor.run(target_id) == 0
            target = db.get(PublicationTargetOrm, target_id)
            assert target.external_post_id and target.status == "published"
            publications[content.language] = sent[-1]
            assert all(frame["record_url"] in content.body_markdown for frame in bundle["frames"])
        assert publications["ru"][0] == channels[0].external_id and publications["en"][0] == channels[1].external_id
        assert "Сделано в Postbridge" in publications["ru"][1].text
        assert "Made with Postbridge" in publications["en"][1].text
        assert publications["ru"][1].video_title != publications["en"][1].video_title
    assert len(sent) == 4 and len(storyboards) == 3 and len(translations) == 2
    assert catalogue.queries == ["walking path", "rail exhibition", "unavailable", "market"]
