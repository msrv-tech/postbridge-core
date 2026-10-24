"""Idempotent gateway video operations, with free polling and durable cost recovery."""
import base64
from datetime import UTC, datetime
import json
from uuid import NAMESPACE_URL, uuid5

import httpx

from postbridge.ai.urls import join_openai_compatible_path
from postbridge.config import get_settings
from postbridge.domain.errors import PostbridgeError
from postbridge.models.domain import MediaAssetOrm
from postbridge.services.video_media import _ffmpeg, inspect_media, video_error


class WaitingForVideo(Exception):
    """Yield the production worker while a known asynchronous operation runs."""


def gateway_video(method, path, payload=None, *, timeout=75):
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.ai_gateway_api_key}"}
    with httpx.Client(timeout=timeout) as client:
        with client.stream(method, join_openai_compatible_path(settings.ai_gateway_base_url, path),
                           headers=headers, json=payload) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 44 * 1024 * 1024:
                    raise ValueError("Video gateway response exceeded its limit")
    body = json.loads(data)
    if not isinstance(body, dict):
        raise ValueError("Invalid video gateway response")
    return body


def generation_price():
    """Read the funded payer's quote; Postbridge never sets video coefficients."""
    from postbridge.services.video_studio import capabilities
    if not capabilities()["video_generation_enabled"]:
        raise video_error("VIDEO_GENERATION_UNAVAILABLE", "AI video generation is not configured")
    settings = get_settings()
    try:
        catalog = gateway_video("GET", "/v1/videos/models", timeout=10)
        model = next(m for m in catalog["models"] if m["id"] == settings.video_generation_model)
        quote = next(q for q in model["quotes"] if q["duration_seconds"] == 8)
        units = quote["billing_units"]
        resolution = model["resolution"]
        expected_resolution = "720p" if settings.video_generation_model.endswith(":720p") else "1080p"
        if (catalog.get("enabled") is not True or resolution != expected_resolution or isinstance(units, bool)
                or not isinstance(units, int) or not 0 < units <= 100_000_000):
            raise ValueError("Invalid gateway quote")
    except (httpx.HTTPError, KeyError, TypeError, ValueError, StopIteration) as exc:
        raise video_error("VIDEO_PRICING_UNAVAILABLE", "The video price is temporarily unavailable") from exc
    return {"source": "gitsell", "model": model["id"], "duration_seconds": 8,
        "units_per_scene": units, "resolution": resolution}


def _lookup(step):
    return gateway_video("GET", f"/v1/videos/generations/by-request/{step['request_key']}")


def _pending_flag(engine):
    engine.progress["video_settlement_pending"] = any(s.get("kind") == "gateway_video" and (
        s.get("state") in {"video_pending", "unknown"} or s.get("state") == "received" and s.get("gateway_status") == "ready")
        for s in engine.progress.get("steps", {}).values())


def _record(engine, step, body):
    from postbridge.services.video_autopilot import charged_units
    status = body.get("status")
    step["external_request_id"] = body.get("id")
    if status in {"ready", "expired", "failed", "cancelled"}:
        step.update(state="received", units=charged_units(body), gateway_status=status)
        _pending_flag(engine)
        engine.save(financial=True)
        return True
    if status == "reconciliation":
        step.update(state="unknown", gateway_status=status)
        engine.save(financial=True)
        raise video_error("VIDEO_AI_RECONCILIATION_REQUIRED", "The video provider acknowledgement needs reconciliation")
    if status not in {"queued", "starting", "generating", "billing_pending"}:
        raise ValueError("Unknown video operation state")
    step.update(state="video_pending", gateway_status=status)
    _pending_flag(engine)
    engine.save(financial=True)
    return False


def _wait(engine, step):
    if datetime.now(UTC).timestamp() - datetime.fromisoformat(step["submitted_at"]).timestamp() > 7200:
        step["state"] = "unknown"
        engine.save(financial=True)
        raise video_error("VIDEO_AI_RECONCILIATION_REQUIRED", "Video polling timed out; financial recovery continues without resubmission")
    raise WaitingForVideo()


def _store_result(engine, step, body, directory):
    generated_id = step["artifact_id"]
    try:
        raw = base64.b64decode(body["video"]["b64_json"], validate=True)
        if not raw or len(raw) > 32 * 1024 * 1024:
            raise video_error("VIDEO_GENERATION_FAILED", "The generated scene exceeds its file limit")
        output = directory / f"generated-scene-{generated_id}.mp4"
        output.write_bytes(raw)
        meta = inspect_media(output)
        expected_size = (720, 1280) if step["model"].endswith(":720p") else (1080, 1920)
        if meta["kind"] != "video" or not 7.8 <= meta["duration_seconds"] <= 8.2 or (meta["width"], meta["height"]) != expected_size:
            raise video_error("VIDEO_GENERATION_FAILED", "The generated scene has an unexpected format or duration")
    except (ValueError, KeyError, PostbridgeError):
        step.update(state="invalid_result", gateway_status="invalid_result")
        _pending_flag(engine)
        engine.save(financial=True)
        raise video_error("VIDEO_GENERATION_FAILED", "The paid scene has an invalid output format") from None
    from postbridge.services.video_studio import store_video_material
    with engine.factory() as db:
        existing = db.get(MediaAssetOrm, generated_id)
        if not existing:
            store_video_material(db, engine.tenant_id, output, asset_id=generated_id, name="AI video scene")
    step.update(state="done", result={"asset_id": generated_id})
    _pending_flag(engine)
    engine.save(financial=True)
    return generated_id


def generate_scene(engine, *, index, scene, options, directory):
    settings = get_settings()
    name = f"video:{index}"
    steps = engine.progress.setdefault("steps", {})
    generated_id = str(uuid5(NAMESPACE_URL, f"postbridge-video:{engine.job_id}:generated-video:{index}"))
    step = steps.get(name)
    if step and step.get("state") == "invalid_result":
        raise video_error("VIDEO_GENERATION_FAILED", "The provider returned an invalid paid scene")
    if step and step.get("state") == "done":
        return step["result"]["asset_id"]
    if not step:
        if not settings.video_generation_enabled:
            raise video_error("VIDEO_GENERATION_UNAVAILABLE", "AI video generation is not configured")
        if scene["duration_seconds"] > 8:
            raise video_error("VIDEO_GENERATION_DURATION", "AI video scenes may contain at most eight seconds")
        engine.authorize()
        step = steps[name] = {"state": "video_pending", "kind": "gateway_video", "model": settings.video_generation_model,
            "request_key": str(uuid5(NAMESPACE_URL, f"postbridge:video-operation:{engine.job_id}:{index}")),
            "artifact_id": generated_id, "max_units": engine.progress["billing"]["limit_units"] - engine.spent(),
            "submitted_at": datetime.now(UTC).isoformat()}
        engine.progress["video_settlement_pending"] = True
        engine.save()
    with engine.factory() as db:
        asset = db.get(MediaAssetOrm, generated_id)
        if asset and asset.tenant_id == engine.tenant_id and step.get("state") == "received":
            step.update(state="done", result={"asset_id": asset.id})
            _pending_flag(engine)
            engine.save(financial=True)
            return asset.id
    try:
        try:
            body = _lookup(step)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            # This route is explicitly idempotent. A lost submit acknowledgement
            # always reuses the same key and cannot start a second provider operation.
            engine.authorize()
            payload = {"request_key": step["request_key"], "model": step["model"], "prompt": (
                "Create one vertical promotional scene with no text, logos, watermarks or invented readable interfaces. "
                + scene["image_prompt"] + "\nStyle: " + options.get("style", "")),
                "duration_seconds": 8, "resolution": "720p" if step["model"].endswith(":720p") else "1080p",
                "aspect_ratio": "9:16", "max_units": step["max_units"]}
            if scene.get("regenerate_video") and scene.get("asset_id"):
                from postbridge.infrastructure.media_storage import copy_media_object_to_file
                with engine.factory() as db:
                    source = db.get(MediaAssetOrm, scene["asset_id"])
                    if not source or source.tenant_id != engine.tenant_id or not source.content_type.startswith(("image/", "video/")):
                        raise video_error("VIDEO_GENERATION_SEED_INVALID", "Choose an image or clip for the first frame")
                    path = directory / f"video-seed-{index}"
                    copy_media_object_to_file(source.object_key, path)
                seed = directory / f"video-seed-{index}.jpg"
                # Keep base64 plus JSON below gateways with a 1 MiB body limit.
                # This is local compression, never another paid generation.
                for quality in (3, 6, 10):
                    _ffmpeg(["-protocol_whitelist", "file,pipe", "-format_whitelist", "image2,png_pipe,jpeg_pipe,webp_pipe,mov,matroska,webm", "-i", str(path), "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
                        "-frames:v", "1", "-q:v", str(quality), "-threads", "1", "-filter_threads", "1", str(seed)])
                    if seed.stat().st_size <= 650 * 1024:
                        break
                if seed.stat().st_size > 650 * 1024:
                    raise video_error("VIDEO_GENERATION_SEED_INVALID", "The first frame exceeds the image size limit")
                payload["image"] = {"mime_type": "image/jpeg", "b64_json": base64.b64encode(seed.read_bytes()).decode()}
            body = gateway_video("POST", "/v1/videos/generations", payload)
        finished = _record(engine, step, body)
    except httpx.HTTPStatusError as exc:
        if exc.request.method == "POST" and exc.response.status_code in {400, 401, 402, 403, 413, 415, 422}:
            step.update(state="received", units=0, gateway_status="rejected")
            _pending_flag(engine)
            engine.save(financial=True)
            raise video_error("VIDEO_GENERATION_NOT_AUTHORIZED", "The video scene was rejected before provider submission") from exc
        _wait(engine, step)
    except (httpx.TransportError, ValueError) as exc:
        _wait(engine, step)
    if not finished:
        _wait(engine, step)
    if body.get("status") != "ready":
        raise video_error("VIDEO_GENERATION_FAILED", "The provider did not return a usable video scene")
    return _store_result(engine, step, body, directory)


def settle_pending(engine):
    """Only read existing gateway intents after cancellation; never submit a new scene."""
    for step in engine.progress.get("steps", {}).values():
        if step.get("kind") != "gateway_video" or step.get("state") not in {"video_pending", "unknown", "received"}:
            continue
        try:
            body = _lookup(step)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            step.update(state="received", units=0, gateway_status="not_submitted")
            _pending_flag(engine)
            engine.save(financial=True)
            continue
        _record(engine, step, body)
        if body.get("status") == "ready":
            from pathlib import Path
            from tempfile import TemporaryDirectory
            with TemporaryDirectory(prefix="postbridge-video-settlement-") as directory:
                _store_result(engine, step, body, Path(directory))
