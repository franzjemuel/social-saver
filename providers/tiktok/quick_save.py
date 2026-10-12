"""Strict public TikTok video targets for Telegram quick-save.

TikTok short-link redirects and photo links are intentionally not resolved by
this first slice. Worker networking receives only a canonical, concrete URL.
"""
import re
from urllib.parse import urlsplit

from providers.base import UnsupportedUrl


_TIKTOK_HOSTS = frozenset({"tiktok.com", "www.tiktok.com", "m.tiktok.com"})
_TIKTOK_SHORT_HOSTS = frozenset({"vm.tiktok.com", "vt.tiktok.com"})
_VIDEO_PATH = re.compile(r"^/@([A-Za-z0-9._]{1,30})/video/([0-9]{6,25})/?$")


def is_tiktok_link(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        host = (urlsplit(value.strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in _TIKTOK_HOSTS | _TIKTOK_SHORT_HOSTS


def normalize_tiktok_video_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise UnsupportedUrl("Send a public TikTok video URL")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise UnsupportedUrl("Send a public TikTok video URL") from None
    if (parsed.scheme != "https" or (parsed.hostname or "").lower() not in _TIKTOK_HOSTS
            or parsed.username is not None or parsed.password is not None or port is not None
            or parsed.fragment or len(parsed.query) > 2048):
        raise UnsupportedUrl("Send a public TikTok video URL; short links are not supported yet")
    match = _VIDEO_PATH.fullmatch(parsed.path)
    if not match:
        raise UnsupportedUrl("Only direct public TikTok video links are supported right now")
    username, post_id = match.groups()
    return f"https://www.tiktok.com/@{username}/video/{post_id}"
