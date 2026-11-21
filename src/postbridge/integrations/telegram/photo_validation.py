"""Strict, bounded decoding of one local Telegram photo candidate."""

import os
from pathlib import Path
from selectors import DefaultSelector, EVENT_READ
import subprocess
from time import monotonic

_DECODER_TIMEOUT_SECONDS = 5


def has_decodable_photo_frame(path: Path, *, png: bool) -> bool:
    demuxer, codec = ("png_pipe", "png") if png else ("jpeg_pipe", "mjpeg")
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-loglevel", "warning",
        "-xerror", "-abort_on", "empty_output", "-max_alloc", "67108864",
        "-filter_threads", "1", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", demuxer, "-nofind_stream_info", "-f", demuxer,
        "-c:v", codec, "-threads", "1",
        "-max_pixels", "16777216", "-err_detect", "crccheck+bitstream+buffer+explode",
        "-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-threads:v", "1",
        "-f", "null", "-",
    ]
    deadline = monotonic() + _DECODER_TIMEOUT_SECONDS
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as process:
        try:
            # The decoder can return success after concealing damage and logging
            # a warning. Reject the first diagnostic without collecting stderr.
            with DefaultSelector() as events:
                events.register(process.stderr, EVENT_READ)
                if not events.select(max(0, deadline - monotonic())):
                    return False
                if os.read(process.stderr.fileno(), 1):
                    return False
            return process.wait(timeout=max(0, deadline - monotonic())) == 0
        except subprocess.TimeoutExpired:
            return False
        finally:
            if process.poll() is None:
                process.kill()
