"""Durable, bounded montage jobs on a dedicated Celery queue."""

from datetime import UTC, datetime, timedelta
import logging
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4, uuid5, NAMESPACE_URL

from sqlalchemy import or_, select, update

from postbridge.config import get_settings
from postbridge.db import SESSION_LOCAL
from postbridge.domain.errors import PostbridgeError
from postbridge.infrastructure.media_storage import copy_media_object_to_file, delete_media_object, upload_media_file
from postbridge.models.domain import MediaAssetOrm, VideoRevisionOrm, VideoProductionJobOrm
from postbridge.services.video_media import MAX_UPLOAD_BYTES, inspect_media, render_montage, video_error
from postbridge.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="postbridge.video.dispatch")
def dispatch_video_jobs():
    from postbridge.workers.video_schedule_tasks import dispatch_video_agent_executions
    dispatch_video_agent_executions(SESSION_LOCAL)
    if not get_settings().video_studio_enabled:
        return
    with SESSION_LOCAL() as session:
        now = datetime.now(UTC)
        # The database is the outbox. Broker failures leave jobs recoverable on the next tick.
        ids = session.scalars(select(VideoProductionJobOrm.id).where(
            or_(VideoProductionJobOrm.status == "queued",
                (VideoProductionJobOrm.status == "running") & (VideoProductionJobOrm.lease_until < now))
        ).order_by(VideoProductionJobOrm.created_at).limit(20)).all()
    for job_id in ids:
        try:
            # send_task always uses the broker, even if the installation enables
            # eager execution for other tasks. Montage must stay off delivery workers.
            celery_app.send_task("postbridge.video.render", args=[job_id], queue="video")
        except Exception:
            logger.warning("Video job dispatch deferred: %s", job_id)
            break


@celery_app.task(name="postbridge.video.render", acks_late=True, reject_on_worker_lost=True,
                 soft_time_limit=2400, time_limit=2450)
def process_video_job(job_id: str):
    if not get_settings().video_studio_enabled:
        return
    now, token = datetime.now(UTC), str(uuid4())
    with SESSION_LOCAL() as session:
        job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.id == job_id).with_for_update())
        if job is None or job.status in {"succeeded", "failed", "cancelled"}:
            return
        lease = job.lease_until
        if lease and lease.tzinfo is None:
            lease = lease.replace(tzinfo=UTC)
        if job.status == "running" and lease and lease >= now:
            return
        if job.attempts >= 3:
            job.status, job.stage, job.error_code = "failed", "failed", "VIDEO_ATTEMPT_LIMIT"
            job.completed_at = now
            session.commit()
            return
        job.status, job.stage, job.lease_token = "running", "preparing", token
        job.lease_until = now + timedelta(seconds=2500)
        job.attempts += 1
        revision = session.get(VideoRevisionOrm, job.revision_id)
        scenes, tenant_id = [{**scene, "scene_id": scene.get("scene_id") or str(uuid5(NAMESPACE_URL, f"video:{revision.id}:scene:{index}"))}
            for index, scene in enumerate(revision.scenes)], job.tenant_id
        options = dict(revision.production_options or {})
        from postbridge.models.domain import VideoProjectOrm
        brief = session.get(VideoProjectOrm, revision.project_id).brief
        session.commit()

    def set_stage(stage):
        with SESSION_LOCAL() as session:
            result = session.execute(update(VideoProductionJobOrm).where(
                VideoProductionJobOrm.id == job_id, VideoProductionJobOrm.status == "running",
                VideoProductionJobOrm.lease_token == token,
            ).values(stage=stage))
            session.commit()
            if not result.rowcount:
                raise InterruptedError("Job cancelled or superseded")

    uploaded_key = None
    preview_uploads = []
    try:
        with TemporaryDirectory(prefix="postbridge-video-") as tmp:
            directory, sources, speech_sources = Path(tmp), {}, {}
            def copy_sources(items):
                with SESSION_LOCAL() as session:
                    for asset_id in {scene.get("asset_id") for scene in items} - {None} - sources.keys():
                        asset = session.get(MediaAssetOrm, asset_id)
                        if asset is None or asset.tenant_id != tenant_id or asset.byte_size > MAX_UPLOAD_BYTES:
                            raise ValueError("Invalid source asset")
                        path = directory / f"source-{asset_id}"
                        copy_media_object_to_file(asset.object_key, path)
                        if path.stat().st_size > MAX_UPLOAD_BYTES:
                            raise ValueError("Source asset too large")
                        inspect_media(path)
                        sources[asset_id] = path
            copy_sources(scenes)
            paid_engine = None
            if options.get("mode") == "autopilot" or options.get("narration_enabled") or any(not s.get("asset_id") or s.get("regenerate_image") for s in scenes):
                from postbridge.services.video_autopilot import VideoAutopilot
                paid_engine = VideoAutopilot(SESSION_LOCAL, job_id, token)
                if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
                    raise video_error("VIDEO_RENDERER_UNAVAILABLE", "Video processing is not configured")
                # Verify canonical storage before the first paid generation.
                probe = directory / "storage-probe.txt"
                probe.write_bytes(b"video-ready")
                probe_key = f"tenants/{tenant_id}/video-probes/{job_id}-{token}"
                upload_media_file(probe_key, probe, "text/plain")
                delete_media_object(probe_key)
            if options.get("mode") == "autopilot":
                scenes = paid_engine.produce(
                    brief=brief, options=options, input_scenes=scenes, directory=directory, on_stage=set_stage)
            elif any(not s.get("asset_id") or s.get("regenerate_image") for s in scenes):
                scenes = paid_engine.materialize(scenes, options=options, directory=directory, on_stage=set_stage)
            if options.get("narration_enabled"):
                from postbridge.services.video_speech import produce_speech
                scenes = produce_speech(paid_engine, scenes=scenes, options=options, directory=directory, on_stage=set_stage)
            copy_sources(scenes)
            from postbridge.models.domain import VideoSpeechAssetOrm
            from postbridge.services.video_media import inspect_speech
            with SESSION_LOCAL() as session:
                for asset_id in {s.get("speech_asset_id") for s in scenes} - {None}:
                    speech = session.get(VideoSpeechAssetOrm, asset_id)
                    asset = session.get(MediaAssetOrm, asset_id)
                    if not speech or speech.tenant_id != tenant_id or not asset or asset.tenant_id != tenant_id:
                        raise video_error("VIDEO_SPEECH_ASSET_INVALID", "Speech asset is unavailable")
                    path = directory / f"audio-{asset_id}.wav"
                    copy_media_object_to_file(asset.object_key, path)
                    if inspect_speech(path) != speech.sample_count:
                        raise video_error("VIDEO_SPEECH_ASSET_INVALID", "Speech asset failed verification")
                    speech_sources[asset_id] = path
            output = render_montage(scenes, sources, directory, on_scene=lambda _index: set_stage("assembling"),
                                    speech_sources=speech_sources, options=options)
            set_stage("validating")
            from postbridge.services.video_media import _ffmpeg
            previews, elapsed = [], 0
            for index, scene in enumerate(scenes):
                at = elapsed + scene["duration_seconds"] / 2
                elapsed += scene["duration_seconds"]
                preview_id = str(uuid5(NAMESPACE_URL, f"video:{job_id}:preview:{index}"))
                preview_path = directory / f"preview-{index}.jpg"
                _ffmpeg(["-ss", str(at), "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov", "-i", str(output),
                    "-vf", "scale=360:640", "-frames:v", "1", "-q:v", "4", "-threads", "1", "-filter_threads", "1", str(preview_path)])
                preview_key = f"tenants/{tenant_id}/media/{preview_id}.jpg"
                upload_media_file(preview_key, preview_path, "image/jpeg")
                preview_uploads.append(preview_key)
                previews.append({"asset_id": preview_id, "object_key": preview_key, "byte_size": preview_path.stat().st_size,
                    "at_seconds": round(at, 3), "scene_id": scene.get("scene_id"), "scene_number": index + 1})
            output_id = str(uuid4())
            uploaded_key = f"tenants/{tenant_id}/media/{output_id}.mp4"
            upload_media_file(uploaded_key, output, "video/mp4")
            with SESSION_LOCAL() as session:
                job = session.scalar(select(VideoProductionJobOrm).where(VideoProductionJobOrm.id == job_id).with_for_update())
                if job.status != "running" or job.lease_token != token:
                    raise InterruptedError("Job cancelled or superseded")
                for preview in previews:
                    if session.get(MediaAssetOrm, preview["asset_id"]) is None:
                        session.add(MediaAssetOrm(id=preview["asset_id"], tenant_id=tenant_id, object_key=preview["object_key"],
                            content_type="image/jpeg", byte_size=preview["byte_size"]))
                job.progress_json = {**(job.progress_json or {}), "scenes": scenes, "previews": previews}
                session.add(MediaAssetOrm(id=output_id, tenant_id=tenant_id, object_key=uploaded_key,
                                           content_type="video/mp4", byte_size=output.stat().st_size))
                session.flush()
                job.output_asset_id, job.status, job.stage = output_id, "succeeded", "ready"
                job.completed_at = datetime.now(UTC)
                session.commit()
                uploaded_key = None
    except InterruptedError:
        pass
    except Exception as exc:
        code = exc.code if isinstance(exc, PostbridgeError) else "VIDEO_RENDER_FAILED"
        with SESSION_LOCAL() as session:
            session.execute(update(VideoProductionJobOrm).where(
                VideoProductionJobOrm.id == job_id, VideoProductionJobOrm.status == "running",
                VideoProductionJobOrm.lease_token == token,
            ).values(status="failed", stage="failed", error_code=code, completed_at=datetime.now(UTC)))
            session.commit()
        logger.warning("Video assembly failed: %s (%s)", job_id, code)
    finally:
        for cleanup_key in ([uploaded_key] if uploaded_key else []) + preview_uploads:
            try:
                # A lost commit acknowledgement may already have persisted this asset.
                # Prefer an orphan over deleting an output referenced by the database.
                with SESSION_LOCAL() as session:
                    referenced = session.scalar(select(MediaAssetOrm.id).where(MediaAssetOrm.object_key == cleanup_key))
                if not referenced:
                    delete_media_object(cleanup_key)
            except Exception:
                logger.warning("Video temporary output cleanup deferred: %s", job_id)
