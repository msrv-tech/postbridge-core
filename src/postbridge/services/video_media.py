"""Bounded media inspection and montage. Inputs are managed assets, never remote URLs."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import wave
from pathlib import Path

from postbridge.domain.errors import ValidationError

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_SCENES = 8
MAX_DURATION = 45
WIDTH, HEIGHT, FPS = 1080, 1920, 24


def video_error(code: str, message: str) -> ValidationError:
    return ValidationError(code=code, message=message, details={})


def inspect_media(path: Path) -> dict:
    """Probe with a fixed executable, local protocols and a wall-clock limit."""
    if not shutil.which("ffprobe"):
        raise video_error("VIDEO_RENDERER_UNAVAILABLE", "Video processing is not configured")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-max_alloc", "67108864", "-protocol_whitelist", "file,pipe",
             "-format_whitelist", "image2,png_pipe,jpeg_pipe,webp_pipe,mov,matroska,webm",
             "-show_entries", "format=format_name,duration:stream=codec_type,codec_name,width,height",
             "-of", "json", str(path)],
            capture_output=True, check=True, timeout=20,
        )
        metadata = json.loads(result.stdout)
        stream = next(s for s in metadata["streams"] if s.get("codec_type") == "video")
        width, height = int(stream["width"]), int(stream["height"])
        duration = float(metadata["format"].get("duration", 0))
        formats = set(metadata["format"]["format_name"].split(","))
        allowed = {"image2", "png_pipe", "jpeg_pipe", "webp_pipe", "mov", "mp4", "matroska", "webm"}
        if not formats.intersection(allowed) or not 0 < width * height <= 16_777_216 or not 0 <= duration <= 180:
            raise ValueError("unsupported media")
        codec = stream.get("codec_name")
        is_image = codec in {"png", "mjpeg", "webp"} and not formats.intersection({"mov", "mp4", "matroska", "webm"})
        if not is_image and duration <= 0:
            raise ValueError("missing duration")
        return {"width": width, "height": height, "duration_seconds": duration, "kind": "image" if is_image else "video", "has_audio": any(s.get("codec_type") == "audio" for s in metadata["streams"])}
    except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise video_error("VIDEO_INVALID_MEDIA", "Use a PNG, JPEG, WebP, MP4 or WebM file, up to 180 seconds and 16 megapixels") from exc


def inspect_speech(path: Path) -> int:
    try:
        if path.stat().st_size > 24000 * 2 * 15 + 44:
            raise ValueError("Audio file exceeds scene limit")
        with wave.open(str(path), "rb") as audio:
            count = audio.getnframes()
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 24000) or not 0 < count <= 360000:
                raise ValueError("Unsupported speech PCM")
            if len(audio.readframes(count + 1)) != count * 2:
                raise ValueError("Truncated speech PCM")
        return count
    except (OSError, ValueError, wave.Error, EOFError) as exc:
        raise video_error("VIDEO_SPEECH_ASSET_INVALID", "Speech asset failed verification") from exc


def analysis_frames(source: Path, directory: Path, asset_id: str) -> tuple[dict, list[tuple[float, Path]]]:
    """Sample only local managed media; five frames bound the vision request size."""
    meta = inspect_media(source)
    times = [0.0] if meta["kind"] == "image" else [round(max(0, meta["duration_seconds"] - 0.1) * fraction, 3) for fraction in (0, .25, .5, .75, 1)]
    frames = []
    for index, at in enumerate(times):
        target = directory / f"vision-{asset_id}-{index}.jpg"
        _ffmpeg([*( ["-ss", str(at)] if meta["kind"] == "video" else []),
            "-protocol_whitelist", "file,pipe", "-format_whitelist", "image2,png_pipe,jpeg_pipe,webp_pipe,mov,matroska,webm",
            "-i", str(source), "-vf", "scale=640:640:force_original_aspect_ratio=decrease", "-frames:v", "1", "-q:v", "4",
            "-threads", "1", "-filter_threads", "1", str(target)])
        if not target.exists() or target.stat().st_size > 1024 * 1024:
            raise video_error("VIDEO_INVALID_MEDIA", "Unable to prepare a media preview")
        frames.append((at, target))
    return meta, frames


def render_montage(scenes: list[dict], sources: dict[str, Path], directory: Path, on_scene=None, *, speech_sources=None, options=None) -> Path:
    """Render uniform H.264/AAC segments; loop short clips, preserve screenshot proportions."""
    if not shutil.which("ffmpeg"):
        raise video_error("VIDEO_RENDERER_UNAVAILABLE", "Video processing is not configured")
    segments = []
    options = options or {}
    for index, scene in enumerate(scenes):
        if on_scene:
            on_scene(index)
        source = sources[scene["asset_id"]]
        meta = inspect_media(source)
        speech_path = (speech_sources or {}).get(scene.get("speech_asset_id"))
        start = float(scene.get("source_start_seconds") or 0)
        if start and (meta["kind"] != "video" or start >= meta["duration_seconds"]):
            raise video_error("VIDEO_INVALID_TRIM", "The selected fragment exceeds the source clip")
        if start and start + scene["duration_seconds"] > meta["duration_seconds"] + .05:
            if not speech_path:
                raise video_error("VIDEO_INVALID_TRIM", "The selected fragment exceeds the source clip")
            if scene["duration_seconds"] > meta["duration_seconds"]:
                raise video_error("VIDEO_SPEECH_TOO_LONG", "Shorten narration so its scene fits the selected source clip")
            # A new language may need more time; preserve its full audio and
            # move the visual window back instead of cutting the narration.
            start = meta["duration_seconds"] - scene["duration_seconds"]
            scene["source_start_seconds"] = start
        segment = directory / f"segment-{index}.mp4"
        caption = directory / f"caption-{index}.txt"
        caption.write_text("\n".join(textwrap.wrap(scene.get("caption", ""), width=32)), encoding="utf-8")
        speech_seconds = inspect_speech(speech_path) / 24000 if speech_path else None
        if speech_seconds and scene["duration_seconds"] + 0.0001 < speech_seconds:
            raise video_error("VIDEO_SPEECH_TOO_LONG", "A scene cannot cut off its narration")
        filters = (
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=0x121820,setsar=1,fps={FPS}"
        )
        motion = scene.get("motion") or "none"
        if meta["kind"] == "image" and motion != "none":
            frames = max(1, round(scene["duration_seconds"] * FPS) - 1)
            zoom = f"1+0.08*on/{frames}" if motion == "zoom_in" else "1.08"
            x = f"(iw-iw/zoom)*on/{frames}" if motion == "pan_right" else f"(iw-iw/zoom)*(1-on/{frames})" if motion == "pan_left" else "(iw-iw/zoom)/2"
            filters += f",zoompan=z='{zoom}':x='{x}':y='(ih-ih/zoom)/2':d=1:s={WIDTH}x{HEIGHT}:fps={FPS}"
        if (scene.get("purpose") == "cta" or index == len(scenes) - 1) and options.get("brand_name"):
            brand = directory / f"brand-{index}.txt"
            brand.write_text("\n".join(textwrap.wrap(options["brand_name"], width=32)), encoding="utf-8")
            color = (options.get("brand_color") or "#38bdf8").replace("#", "0x")
            filters += f",drawbox=x=0:y=0:w=iw:h=18:color={color}:t=fill,drawtext=textfile='{brand}':expansion=none:fontcolor={color}:fontsize=42:x=(w-text_w)/2:y=96"
        if scene.get("caption"):
            filters += (
                f",drawtext=textfile='{caption}':expansion=none:fontcolor=white:fontsize=45:"
                f"x=(w-text_w)/2:y={'270' if speech_path else 'h-360-text_h'}:box=1:boxcolor=black@0.7:boxborderw=24"
            )
        if speech_path and scene.get("narration_text"):
            words = scene.get("speech_words") or []
            cues = words if words else [{"text": scene["narration_text"], "start": 0, "end": speech_seconds}]
            for n, cue in enumerate(cues):
                start_at, end_at = float(cue["start"]), float(cue["end"])
                if not 0 <= start_at < end_at <= speech_seconds + .1:
                    raise video_error("VIDEO_SPEECH_ASSET_INVALID", "Invalid subtitle interval")
                subtitle = directory / f"subtitle-{index}-{n}.txt"
                subtitle.write_text("\n".join(textwrap.wrap(cue["text"], width=32)), encoding="utf-8")
                filters += (f",drawtext=textfile='{subtitle}':expansion=none:fontcolor=white:fontsize=42:"
                            f"x=(w-text_w)/2:y=h-330-text_h:box=1:boxcolor=black@0.7:boxborderw=18:"
                            f"enable='gte(t,{start_at:.3f})*lt(t,{end_at:.3f})'")
        if scene.get("transition") == "fade":
            filters += f",fade=t=in:st=0:d=0.2,fade=t=out:st={max(.2, scene['duration_seconds'] - .2)}:d=0.2"
        input_args = ["-loop", "1"] if meta["kind"] == "image" else ["-stream_loop", "-1"]
        if start:
            input_args += ["-ss", str(start)]
        audio_input = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "wav", "-i", str(speech_path)] if speech_path else [
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        _ffmpeg([
            *input_args, "-protocol_whitelist", "file,pipe", "-format_whitelist", "image2,png_pipe,jpeg_pipe,webp_pipe,mov,matroska,webm", "-i", str(source),
            *audio_input,
            "-map", "0:v:0", "-map", "1:a:0" if speech_path or not meta["has_audio"] else "0:a:0", "-vf", filters,
            "-t", str(scene["duration_seconds"]), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-maxrate", "6M", "-bufsize", "12M", "-pix_fmt", "yuv420p", "-threads", "2", "-filter_threads", "1",
            "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2", "-af", "apad", "-movflags", "+faststart", str(segment),
        ])
        segments.append(segment)
    manifest = directory / "segments.txt"
    manifest.write_text("".join(f"file '{p}'\n" for p in segments))
    output = directory / "output.mp4"
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(output)])
    meta = inspect_media(output)
    if ((meta["width"], meta["height"]) != (WIDTH, HEIGHT)
            or not 0 < meta["duration_seconds"] <= MAX_DURATION + 1 or output.stat().st_size > MAX_UPLOAD_BYTES):
        raise video_error("VIDEO_INVALID_OUTPUT", "The assembled file failed validation")
    return output


def _ffmpeg(args: list[str]) -> None:
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-max_alloc", "67108864", *args],
                       check=True, capture_output=True, timeout=240)
    except (subprocess.SubprocessError, OSError) as exc:
        raise video_error("VIDEO_RENDER_FAILED", "Video assembly failed; your source materials are preserved") from exc


def render_cover(scene, source, directory, options):
    """A separate, free cover from a verified visual and publishable scene text."""
    meta = inspect_media(source)
    title = (scene.get("caption") or options.get("brand_name") or "")[:120]
    text = directory / "cover-title.txt"
    text.write_text("\n".join(textwrap.wrap(title, width=24)), encoding="utf-8")
    color = (options.get("brand_color") or "#38bdf8").replace("#", "0x")
    filters = ("scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,"
        f"drawbox=x=0:y=0:w=iw:h=12:color={color}:t=fill")
    if title:
        filters += (f",drawtext=textfile='{text}':expansion=none:fontcolor=white:fontsize=48:"
            "x=(w-text_w)/2:y=h*0.62:box=1:boxcolor=black@0.72:boxborderw=22")
    output = directory / "cover.jpg"
    at = scene.get("source_start_seconds", 0) + min(scene["duration_seconds"] / 2, max(0, meta.get("duration_seconds", 1) / 2))
    _ffmpeg([*(["-ss", str(at)] if meta["kind"] == "video" else []), "-protocol_whitelist", "file,pipe",
        "-i", str(source), "-vf", filters, "-frames:v", "1", "-q:v", "3", "-threads", "1", "-filter_threads", "1", str(output)])
    return output, title
