"""Source MP4 geometry and Telegram native video metadata regressions.

The fixtures simulate ffprobe JSON. No network, actual video download, or
Telegram account credentials are required.
"""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.video_metadata import VideoMetadata, probe_video_metadata
from core.telegram_delivery import TelegramDelivery


def make_runner(payload, *, seen=None):
    def runner(args, **kwargs):
        if seen is not None:
            seen.append((args, kwargs))
        return SimpleNamespace(stdout=json.dumps(payload).encode("utf-8"))
    return runner


def test_portrait_video_ffprobe_dimensions_and_duration_are_not_changed(tmp_path):
    seen = []
    meta = probe_video_metadata(
        tmp_path / "portrait.mp4",
        runner=make_runner({
            "streams": [{"width": 576, "height": 1024, "sample_aspect_ratio": "1:1"}],
            "format": {"duration": "15.6"},
        }, seen=seen),
    )
    assert meta == VideoMetadata(width=576, height=1024, duration=16)
    assert meta.telegram_kwargs() == {"width": 576, "height": 1024, "duration": 16}
    assert seen[0][0][0] == "ffprobe"
    assert seen[0][1]["timeout"] == 8
    assert seen[0][1]["check"] is True


def test_rotation_is_respected_for_display_geometry(tmp_path):
    for rotation in ("-90", 90, 270):
        meta = probe_video_metadata(
            tmp_path / "rotated.mp4",
            runner=make_runner({
                "streams": [{
                    "width": 1920, "height": 1080,
                    "side_data_list": [{"side_data_type": "Display Matrix", "rotation": rotation}],
                }],
                "format": {"duration": "5.1"},
            }),
        )
        assert meta == VideoMetadata(width=1080, height=1920, duration=5)


def test_actual_square_source_is_not_faked_to_portrait(tmp_path):
    meta = probe_video_metadata(
        tmp_path / "square.mp4",
        runner=make_runner({
            "streams": [{"width": 720, "height": 720}],
            "format": {"duration": "10"},
        }),
    )
    assert meta == VideoMetadata(width=720, height=720, duration=10)


@pytest.mark.parametrize("payload", [
    {},
    {"streams": []},
    {"streams": [{"width": 0, "height": 1024}]},
    {"streams": [{"width": 576, "height": -1}]},
    {"streams": [{"width": 576, "height": 1024, "tags": {"rotate": "NaN"}}]},
    {"streams": [{"width": 576, "height": 1024, "tags": {"rotate": 45}}]},
])
def test_untrusted_bad_video_metadata_is_not_forwarded(tmp_path, payload):
    assert probe_video_metadata(tmp_path / "video.mp4", runner=make_runner(payload)) is None


def test_metadata_probe_unavailable_is_safe(tmp_path):
    def missing_ffprobe(args, **kwargs):
        raise FileNotFoundError("ffprobe unavailable")

    assert probe_video_metadata(tmp_path / "video.mp4", runner=missing_ffprobe) is None


def test_probe_timed_out_is_safe(tmp_path):
    def timed_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    assert probe_video_metadata(tmp_path / "video.mp4", runner=timed_out) is None


class RecordingBot:
    def __init__(self):
        self.calls = []

    async def send_video(self, chat_id, upload, **kwargs):
        self.calls.append((chat_id, upload, kwargs))
        return SimpleNamespace(message_id=55)

    async def send_photo(self, *args, **kwargs):
        raise AssertionError("A video should not call send_photo")


@pytest.mark.asyncio
async def test_native_telegram_send_video_passes_measured_geometry(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fixture-bytes")
    bot = RecordingBot()
    result = await TelegramDelivery(bot).send_files(
        901, [("video", path)], caption="Social Saver",
        video_metadata=VideoMetadata(width=576, height=1024, duration=16),
    )
    assert result[0].message_id == 55
    chat, upload, kwargs = bot.calls[0]
    assert chat == 901
    assert kwargs["width"] == 576
    assert kwargs["height"] == 1024
    assert kwargs["duration"] == 16
    assert kwargs["supports_streaming"] is True


@pytest.mark.asyncio
async def test_unknown_metadata_preserves_legacy_video_delivery(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fixture-bytes")
    bot = RecordingBot()
    await TelegramDelivery(bot).send_files(901, [("video", path)], video_metadata=None)
    kwargs = bot.calls[0][2]
    assert "width" not in kwargs and "height" not in kwargs
    assert "duration" not in kwargs


@pytest.mark.asyncio
async def test_nonvideo_delivery_rejects_video_metadata_without_sending(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"fixture-bytes")
    bot = RecordingBot()
    with pytest.raises(ValueError):
        await TelegramDelivery(bot).send_files(
            901, [("photo", path)],
            video_metadata=VideoMetadata(width=576, height=1024),
        )
    assert bot.calls == []
