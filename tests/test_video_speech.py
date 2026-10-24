"""Google speech contracts, durable billing checkpoints and language variants."""
import base64
from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
import struct
import subprocess
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from postbridge.models.domain import MediaAssetOrm, VideoProductionJobOrm, VideoSpeechAssetOrm
from postbridge.services import video_autopilot
from postbridge.services.video_studio import VideoCreate, VideoRevise, create_project, get_project, revise_project
from postbridge.services.video_speech import VoiceVariantRequest, create_voice_variant
from postbridge.services.video_post_drafts import PrepareVideoPost, prepare_post_draft
from postbridge.services.media_assets import delete_media_asset
from postbridge.services.video_media import inspect_media
from postbridge.workers import video_tasks
from tests.test_video_autopilot import auto
from tests.test_video_studio import state, error


@pytest.fixture
def speech(auto, monkeypatch):
    monkeypatch.setenv("VIDEO_SPEECH_ENABLED", "1")
    calls = []
    pcm = b"".join(struct.pack("<h", int(7000 * math.sin(2 * math.pi * 440 * i / 24000))) for i in range(54000))
    def gateway(path, body, correlation):
        calls.append((path, body))
        if path.endswith("completions"):
            count = len(json.loads(body["messages"][-1]["content"])["scenes"])
            return {"usage_tokens_charged": 7, "choices": [{"message": {"content": json.dumps({"scenes": [
                {"caption": "Try the product", "narration_text": "Try our product today."} for _ in range(count)]})}}]}
        return {"id": "speech-external-id", "usage_tokens_charged": 11,
            "audio": {"b64_pcm": base64.b64encode(pcm).decode(), "sample_rate": 24000, "sample_width": 2, "channels": 1}}
    monkeypatch.setattr(video_autopilot, "gateway_json", gateway)
    return (*auto, calls)


def make(speech, *, narrated=True, text="Попробуйте наш продукт."):
    session, tenant, _, material, *_ = speech
    project = create_project(session, tenant, VideoCreate(brief="Product demo", request_key=str(uuid4()),
        scenes=[{"asset_id": material["media_asset_id"], "duration_seconds": 1, "caption": "Демонстрация", "narration_text": text}],
        narration_enabled=narrated))
    video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    return get_project(session, tenant, project["id"])


def variant(speech, project, *, language="en", voice="Kore", source_version=None, key=None):
    session, tenant, *_ = speech
    body = VoiceVariantRequest(request_key=key or str(uuid4()), expected_version=project["current_version"],
        source_version=source_version or project["current_version"], language=language, voice=voice)
    result = create_voice_variant(session, tenant, project["id"], body)
    video_tasks.process_video_job.run(result["requested_revision"]["job_id"])
    session.expire_all()
    return get_project(session, tenant, project["id"]), body


def test_generated_speech_controls_scene_length_and_real_audible_output(speech):
    session, tenant, _, _, tmp, calls = speech
    project = make(speech)
    current = project["current"]
    assert current["status"] == "succeeded", current
    assert current["language"] == "ru" and current["voice"] == "Kore"
    assert current["usage"]["tokens_charged"] == 11
    scene = current["scenes"][0]
    assert scene["duration_seconds"] == 2.25 and scene["speech_seconds"] == 2.25
    assert scene["speech_url"].endswith(".wav")
    asset = session.get(MediaAssetOrm, current["output_asset_id"])
    path = tmp / "storage" / asset.object_key
    assert 2.2 <= inspect_media(path)["duration_seconds"] < 2.4
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "s16le", "-"],
        check=True, capture_output=True, timeout=20).stdout
    assert len(decoded) > 24000 and max(abs(n[0]) for n in struct.iter_unpack("<h", decoded)) > 1000
    assert len(calls) == 1 and calls[0][0] == "/v1/audio/generations"
    with error("VALIDATION_MEDIA_ASSET_IN_USE"):
        delete_media_asset(session, tenant_id=tenant, media_asset_id=scene["speech_asset_id"])


def test_language_and_voice_versions_reuse_visuals_and_have_independent_posts(speech):
    session, tenant, _, _, _, calls = speech
    original = make(speech)
    original_url = original["current"]["output_url"]
    localized, request = variant(speech, original)
    assert localized["current"]["status"] == "succeeded", localized
    assert localized["current"]["language"] == "en"
    assert localized["current"]["source_version"] == 1
    assert localized["current"]["usage"]["tokens_charged"] == 18
    assert localized["revisions"][1]["output_url"] == original_url
    assert localized["current"]["scenes"][0]["asset_id"] == original["current"]["scenes"][0]["asset_id"]
    changed, _ = variant(speech, localized, language="ru", voice="Charon", source_version=1)
    assert changed["current"]["status"] == "succeeded"
    assert changed["current"]["voice"] == "Charon"
    assert [r["language"] for r in changed["revisions"]] == ["ru", "en", "ru"]
    assert len(calls) == 4 and all(not path.endswith("images/generations") for path, _ in calls)
    # Acknowledgement replay remains stable after another language/voice was added.
    replay = create_voice_variant(session, tenant, original["id"], request)
    assert replay["requested_revision"]["version"] == 2
    assert replay["current_version"] == 3
    russian = prepare_post_draft(session, tenant, changed["id"], PrepareVideoPost(expected_version=3, source_version=1))
    english = prepare_post_draft(session, tenant, changed["id"], PrepareVideoPost(expected_version=3, source_version=2))
    assert russian["content_item_id"] != english["content_item_id"]
    assert session.scalar(select(func.count()).select_from(VideoSpeechAssetOrm)) == 3


def test_same_text_voice_is_reused_without_second_paid_call(speech):
    *_, calls = speech
    first = make(speech)
    second, _ = variant(speech, first, language="ru")
    assert second["current"]["status"] == "succeeded"
    assert len(calls) == 1 and second["current"]["usage"]["tokens_charged"] == 0
    assert first["current"]["scenes"][0]["speech_asset_id"] == second["current"]["scenes"][0]["speech_asset_id"]


def test_lost_speech_response_is_not_retried(speech, monkeypatch):
    session, tenant, *_ = speech
    calls = []
    def lost(*args):
        calls.append(args)
        raise httpx.ReadTimeout("lost response")
    monkeypatch.setattr(video_autopilot, "gateway_json", lost)
    result = make(speech)
    assert result["current"]["error_code"] == "VIDEO_AI_RECONCILIATION_REQUIRED"
    assert result["current"]["usage"]["uncertain"]
    video_tasks.process_video_job.run(result["current"]["job_id"])
    assert len(calls) == 1


def test_recovery_after_audio_storage_reuses_received_result(speech, monkeypatch):
    session, tenant, _, material, _, calls = speech
    project = create_project(session, tenant, VideoCreate(brief="Demo", request_key=str(uuid4()), narration_enabled=True,
        scenes=[{"asset_id": material["media_asset_id"], "duration_seconds": 3, "caption": "Demo"}]))
    save = video_autopilot.VideoAutopilot.save
    def crashed(engine, **kwargs):
        if engine.progress.get("steps", {}).get("speech:0", {}).get("state") == "done":
            raise KeyboardInterrupt("worker lost before result checkpoint")
        return save(engine, **kwargs)
    monkeypatch.setattr(video_autopilot.VideoAutopilot, "save", crashed)
    with pytest.raises(KeyboardInterrupt):
        video_tasks.process_video_job.run(project["current"]["job_id"])
    session.expire_all()
    job = session.get(VideoProductionJobOrm, project["current"]["job_id"])
    job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    monkeypatch.setattr(video_autopilot.VideoAutopilot, "save", save)
    video_tasks.process_video_job.run(job.id)
    session.expire_all()
    current = get_project(session, tenant, project["id"])["current"]
    assert current["status"] == "succeeded" and current["usage"]["tokens_charged"] == 11
    assert len(calls) == 1
    assert session.get(VideoProductionJobOrm, job.id).progress_json["steps"]["speech:0"]["state"] == "done"


def test_manual_edit_preserves_speech_and_rejects_a_different_transcript(speech, monkeypatch):
    session, tenant, *_ = speech
    project = make(speech)
    source = project["current"]["scenes"][0]
    body = {key: source[key] for key in ("asset_id", "duration_seconds", "caption", "narration_text", "speech_asset_id")}
    body["caption"] = "New heading"
    monkeypatch.setenv("VIDEO_SPEECH_ENABLED", "0")
    revision = revise_project(session, tenant, project["id"], VideoRevise(brief=project["brief"], request_key=str(uuid4()),
        expected_version=1, scenes=[body]))
    video_tasks.process_video_job.run(revision["current"]["job_id"])
    session.expire_all()
    assert get_project(session, tenant, project["id"])["current"]["status"] == "succeeded"
    body["narration_text"] = "A different recording"
    with error("VIDEO_SPEECH_ASSET_INVALID"):
        revise_project(session, tenant, project["id"], VideoRevise(brief=project["brief"], request_key=str(uuid4()), expected_version=2, scenes=[body]))


def test_voice_variant_rejects_cross_tenant_and_stale_source(speech):
    session, tenant, other, *_ = speech
    project = make(speech)
    request = VoiceVariantRequest(request_key=str(uuid4()), source_version=1, expected_version=1, language="en")
    with error("VIDEO_PROJECT_NOT_FOUND"):
        create_voice_variant(session, other, project["id"], request)
    with error("VIDEO_NOT_READY"):
        create_voice_variant(session, tenant, project["id"], request.model_copy(update={"source_version": 99}))


def test_overlong_audio_is_charged_but_never_cut_off(speech, monkeypatch):
    original = video_autopilot.gateway_json
    def long_audio(*args):
        body = original(*args)
        body["audio"]["b64_pcm"] = base64.b64encode(b"\x00\x01" * 24000 * 16).decode()
        return body
    monkeypatch.setattr(video_autopilot, "gateway_json", long_audio)
    current = make(speech)["current"]
    assert current["status"] == "failed" and current["error_code"] == "VIDEO_SPEECH_TOO_LONG"
    assert current["usage"]["tokens_charged"] == 11 and not current["usage"]["uncertain"]
    assert current["output_url"] is None


def test_speech_migration_handles_baseline_and_guards_downgrade(speech):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import runpy
    session, *_ = speech
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic/versions/20261019_video_speech.py"))
    with session.get_bind().begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration["upgrade"]()
        migration["upgrade"]()
    make(speech)
    with session.get_bind().begin() as connection, Operations.context(MigrationContext.configure(connection)):
        with pytest.raises(RuntimeError, match="Retain speech"):
            migration["downgrade"]()


@pytest.mark.parametrize('still_long',[False,True])
def test_long_translation_is_shortened_once_and_all_attempts_remain_charged(speech,monkeypatch,still_long):
    original=make(speech)
    gateway=video_autopilot.gateway_json
    attempts=[]
    def response(path,body,correlation):
        if path.endswith('completions') and 'available_seconds' in body['messages'][-1]['content']:
            attempts.append('shorten')
            return {'usage_tokens_charged':7,'choices':[{'message':{'content':json.dumps({'caption':'Try','narration_text':'Try now.'})}}]}
        result=gateway(path,body,correlation)
        if path.endswith('audio/generations'):
            attempts.append(body['text'])
            if body['text']=='Try our product today.' or still_long:
                result['audio']['b64_pcm']=base64.b64encode(b'\x00\x01'*(24000*16)).decode()
        return result
    monkeypatch.setattr(video_autopilot,'gateway_json',response)
    localized,_=variant(speech,original)
    current=localized['current']
    assert attempts==['Try our product today.','shorten','Try now.']
    assert current['usage']['tokens_charged']==36
    assert current['status']==('failed' if still_long else 'succeeded')
    if not still_long:
        assert current['scenes'][0]['narration_text']=='Try now.'
        assert current['scenes'][0]['asset_id']==original['current']['scenes'][0]['asset_id']
