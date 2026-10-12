"""Strict TikTok link normalization and bounded short-share resolution.

Only a TikTok-owned short link can be dereferenced, and only TikTok-owned
short links or concrete video targets can be followed. No arbitrary redirects,
browser-exposed provider URLs, cookies, or provider session credentials.
"""
import re
from urllib.parse import urljoin, urlsplit

import httpx

from providers.base import UnsupportedUrl, SourceUnavailable


_TIKTOK_HOSTS = frozenset({"tiktok.com", "www.tiktok.com", "m.tiktok.com"})
_TIKTOK_SHORT_HOSTS = frozenset({"vm.tiktok.com", "vt.tiktok.com"})
_TIKTOK_ALLOWED_HOSTS = _TIKTOK_HOSTS | _TIKTOK_SHORT_HOSTS
_VIDEO_PATH = re.compile(r"^/@([A-Za-z0-9._]{1,30})/video/([0-9]{6,25})/?$")
_SHARE_PATH = re.compile(r"^/t/([A-Za-z0-9_-]{5,64})/?$")
_SHORT_HOST_PATH = re.compile(r"^/([A-Za-z0-9_-]{5,64})/?$")
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_MAX_SHORT_LINK_HOPS = 4


def is_tiktok_link(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        host = (urlsplit(value.strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in _TIKTOK_ALLOWED_HOSTS


def _validated_parts(value: str):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        raise UnsupportedUrl("Send a public TikTok video or share URL")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise UnsupportedUrl("Send a public TikTok video or share URL") from None
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or host not in _TIKTOK_ALLOWED_HOSTS
            or parsed.username is not None or parsed.password is not None
            or port is not None or parsed.fragment or len(parsed.query) > 2048):
        raise UnsupportedUrl("Only HTTPS TikTok video or share links are supported")
    return parsed, host


def normalize_tiktok_video_url(value: str) -> str:
    parsed, host = _validated_parts(value)
    if host not in _TIKTOK_HOSTS:
        raise UnsupportedUrl("Only public TikTok video links are supported")
    match = _VIDEO_PATH.fullmatch(parsed.path)
    if not match:
        raise UnsupportedUrl("Only direct public TikTok video links are supported right now")
    username, post_id = match.groups()
    return f"https://www.tiktok.com/@{username}/video/{post_id}"


def normalize_tiktok_share_url(value: str) -> str:
    """Canonicalize only TikTok's public redirect-link shapes, no network."""
    parsed, host = _validated_parts(value)
    match = (_SHARE_PATH.fullmatch(parsed.path) if host in _TIKTOK_HOSTS
             else _SHORT_HOST_PATH.fullmatch(parsed.path))
    if match is None:
        raise UnsupportedUrl("Send a direct TikTok video or TikTok short-share link")
    token = match.group(1)
    if host in _TIKTOK_HOSTS:
        return f"https://www.tiktok.com/t/{token}/"
    return f"https://{host}/{token}/"


def normalize_tiktok_quick_save_url(value: str) -> str:
    """Validate at Telegram ingress; never resolve short links in the bot."""
    try:
        return normalize_tiktok_video_url(value)
    except UnsupportedUrl as direct_error:
        try:
            return normalize_tiktok_share_url(value)
        except UnsupportedUrl:
            raise direct_error from None


async def resolve_tiktok_quick_save_url(value: str, *, client=None) -> str:
    """Resolve approved TikTok short links to one canonical public video.

    Redirects are inspected manually, and every next destination is validated
    *before* making another network request. The GET response body is not read.
    """
    canonical = normalize_tiktok_quick_save_url(value)
    try:
        return normalize_tiktok_video_url(canonical)
    except UnsupportedUrl:
        pass

    if client is not None:
        return await _follow_tiktok_redirects(canonical, client)

    # No cookie jar from any user session, proxy environment, or automatic
    # redirects. Reading only redirect headers keeps bandwidth bounded.
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout, trust_env=False) as owned:
        return await _follow_tiktok_redirects(canonical, owned)


async def _follow_tiktok_redirects(current: str, client) -> str:
    for _ in range(_MAX_SHORT_LINK_HOPS):
        try:
            return normalize_tiktok_video_url(current)
        except UnsupportedUrl:
            # Any non-video hop must still be a strictly valid TikTok short link.
            normalize_tiktok_share_url(current)
        try:
            async with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code not in _REDIRECT_CODES:
                    raise UnsupportedUrl("TikTok share link did not redirect to a public video")
                location = response.headers.get("location")
        except (httpx.TimeoutException, httpx.TransportError):
            raise SourceUnavailable("TikTok share link could not be resolved") from None
        if not location or len(location) > 4096:
            raise UnsupportedUrl("TikTok share link returned an invalid redirect")
        # urljoin also handles relative locations. Validate the next destination
        # before any further network I/O, preventing off-domain SSRF.
        next_url = urljoin(current, location)
        current = normalize_tiktok_quick_save_url(next_url)
    try:
        return normalize_tiktok_video_url(current)
    except UnsupportedUrl:
        raise UnsupportedUrl("TikTok share link exceeded the redirect limit") from None
