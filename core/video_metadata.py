"""Inspect MP4 display geometry without rewriting, cropping, or transcoding.

The worker image includes ffprobe via the ffmpeg package. This metadata is
used only for Telegram's native sendVideo parameters and sanitized diagnostics.
"""
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    duration: int | None = None

    def telegram_kwargs(self) -> dict[str, int]:
        fields = {"width": self.width, "height": self.height}
        if self.duration is not None:
            fields["duration"] = self.duration
        return fields


def probe_video_metadata(path: Path, *, runner=None) -> VideoMetadata | None:
    """Read video-stream width/height and display rotation with a bounded probe.

    Square files are reported as square, not arbitrarily stretched to 9:16.
    FFprobe failures return None so existing MP4 delivery remains possible.
    """
    runner = runner or subprocess.run
    try:
        result = runner(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries",
                "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, check=True, timeout=8,
        )
        if len(result.stdout) > 65536:
            return None
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        if not streams or not isinstance(streams[0], dict):
            return None
        stream = streams[0]
        width, height = stream.get("width"), stream.get("height")
        if not isinstance(width, int) or isinstance(width, bool):
            return None
        if not isinstance(height, int) or isinstance(height, bool):
            return None
        if not (1 <= width <= 16384 and 1 <= height <= 16384):
            return None

        # Rotate the advertised display geometry only when MP4 explicitly
        # supplies a 90/270-degree display matrix or legacy rotate tag.
        rotation = (stream.get("tags") or {}).get("rotate", 0)
        for item in stream.get("side_data_list") or []:
            if isinstance(item, dict) and item.get("rotation") is not None:
                rotation = item["rotation"]
                break
        rotation = float(rotation)
        if not math.isfinite(rotation):
            return None
        rotated = round(rotation) % 360
        if abs(rotation - round(rotation)) > 0.01 or rotated not in (0, 90, 180, 270):
            return None
        if rotated in (90, 270):
            width, height = height, width

        duration = None
        raw_duration = (payload.get("format") or {}).get("duration")
        if raw_duration is not None:
            seconds = float(raw_duration)
            if math.isfinite(seconds) and 0 < seconds <= 86400:
                duration = max(1, round(seconds))
        return VideoMetadata(width=width, height=height, duration=duration)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError):
        return None
