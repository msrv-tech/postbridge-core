"""Version-bound video handoff, with no external publication side effect."""
from pathlib import Path
from uuid import uuid4
import runpy

import pytest
from sqlalchemy import func, select

from postbridge.models.domain import ContentItemOrm, PublicationPlanOrm, VideoPostDraftOrm
from postbridge.services.postbridge_workspace_content import content_item_to_api_dict, update_postbridge_content_item, delete_postbridge_content_item
from postbridge.services.video_studio import VideoRevise, create_project, get_project, revise_project
from postbridge.services.video_post_drafts import PrepareVideoPost, prepare_post_draft
from postbridge.workers import video_tasks
from tests.test_video_studio import state, error, request


@pytest.fixture
def ready(state):
    db, tenant, _, material, _ = state
    project = create_project(db, tenant, request(material))
    video_tasks.process_video_job.run(project["current"]["job_id"])
    db.expire_all()
    return state, get_project(db, tenant, project["id"])


def test_ready_video_becomes_one_canonical_draft_with_explicit_caption(ready):
    (db, tenant, _, _, _), project = ready
    body = PrepareVideoPost(expected_version=1, caption="Try the demo today")
    result = prepare_post_draft(db, tenant, project["id"], body)
    assert prepare_post_draft(db, tenant, project["id"], body) == result
    post = db.get(ContentItemOrm, result["content_item_id"])
    assert post.source_type == "postbridge" and post.status == "draft"
    assert post.body_markdown == body.caption
    assert project["brief"] not in post.body_markdown
    assert post.media_url == project["current"]["output_url"] and post.media_urls == [post.media_url]
    origin = content_item_to_api_dict(post)["video_origin"]
    assert origin == {"project_id": project["id"], "version": 1,
        "output_asset_id": project["current"]["output_asset_id"], "output_url": post.media_url}
    assert get_project(db, tenant, project["id"])["current"]["post_draft"]["content_item_id"] == post.id
    assert db.scalar(select(func.count()).select_from(ContentItemOrm)) == 1
    assert db.scalar(select(func.count()).select_from(PublicationPlanOrm)) == 0
    with error("VIDEO_POST_DRAFT_EXISTS"):
        prepare_post_draft(db, tenant, project["id"], body.model_copy(update={"caption": "A different post"}))


def test_new_revision_never_rewrites_or_inherits_previous_post(ready):
    (db, tenant, _, material, _), project = ready
    old_body = PrepareVideoPost(expected_version=1)
    old = prepare_post_draft(db, tenant, project["id"], old_body)
    post = db.get(ContentItemOrm, old["content_item_id"])
    update_postbridge_content_item(db, row=post, content_md="Edited in the post editor")
    db.commit()
    revised = revise_project(db, tenant, project["id"], VideoRevise(**request(material).model_dump(), expected_version=1))
    assert revised["current"]["post_draft"] is None
    assert revised["revisions"][1]["post_draft"]["content_item_id"] == post.id
    assert prepare_post_draft(db, tenant, project["id"], old_body)["content_item_id"] == post.id
    assert post.body_markdown == "Edited in the post editor"
    assert content_item_to_api_dict(post)["video_origin"]["version"] == 1
    with error("VIDEO_NOT_READY"):
        prepare_post_draft(db, tenant, project["id"], PrepareVideoPost(expected_version=2))


def test_deleted_post_is_not_recreated_by_a_late_retry(ready):
    (db, tenant, _, _, _), project = ready
    body = PrepareVideoPost(expected_version=1, caption="Demo")
    first = prepare_post_draft(db, tenant, project["id"], body)
    # Exercise the real FK tombstone behavior, including SQLite's opt-in FK checks.
    with db.get_bind().connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
    delete_postbridge_content_item(db, row=db.get(ContentItemOrm, first["content_item_id"]))
    db.commit()
    db.expire_all()
    replay = prepare_post_draft(db, tenant, project["id"], body)
    assert replay["status"] == "deleted" and replay["content_item_id"] is None
    assert get_project(db, tenant, project["id"])["current"]["post_draft"]["status"] == "deleted"
    assert db.scalar(select(func.count()).select_from(VideoPostDraftOrm)) == 1
    assert db.scalar(select(func.count()).select_from(ContentItemOrm)) == 0


def test_handoff_rejects_foreign_unfinished_stale_or_disabled_projects(state, monkeypatch):
    db, tenant, other, material, _ = state
    project = create_project(db, tenant, request(material))
    body = PrepareVideoPost(expected_version=1)
    with error("VIDEO_PROJECT_NOT_FOUND"):
        prepare_post_draft(db, other, project["id"], body)
    with error("VIDEO_NOT_READY"):
        prepare_post_draft(db, tenant, project["id"], body)
    revise_project(db, tenant, project["id"], VideoRevise(**request(material).model_dump(), expected_version=1))
    with error("VIDEO_VERSION_CONFLICT"):
        prepare_post_draft(db, tenant, project["id"], body)
    monkeypatch.setenv("VIDEO_STUDIO_ENABLED", "0")
    with error("VIDEO_STUDIO_DISABLED"):
        prepare_post_draft(db, tenant, project["id"], PrepareVideoPost(expected_version=2))
    assert db.scalar(select(func.count()).select_from(ContentItemOrm)) == 0


def test_video_api_prepares_and_exposes_the_same_post(ready):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from postbridge.api.video_studio import router
    from postbridge.db import get_db_session
    (db, tenant, _, _, _), project = ready
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: db
    client = TestClient(app)
    headers = {"Authorization": "Bearer svc-test-secret", "X-Tenant-Id": tenant}
    path = f"/internal/service/videos/{project['id']}"
    result = client.post(path + "/post-draft", headers=headers, json={"expected_version": 1, "caption": "Demo"})
    assert result.status_code == 200, result.text
    assert client.get(path, headers=headers).json()["current"]["post_draft"]["content_item_id"] == result.json()["content_item_id"]
    assert client.post(path + "/post-draft", headers=headers, json={"expected_version": 1, "status": "published"}).status_code == 422


def test_post_draft_migration_handles_baseline_and_existing_table(state):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    db, *_ = state
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/20261019_video_post_drafts.py"))
    with db.get_bind().begin() as connection:
        VideoPostDraftOrm.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
            migration["upgrade"]()
