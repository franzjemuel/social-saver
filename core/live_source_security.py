"""Security boundary for network sources handed to FFmpeg.

Ordinary jobs may identify a platform target, but must not choose an arbitrary URL.
A provider resolver running inside the trusted worker produces the LiveSource.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from providers.live import LiveSource

SAFE_HEADER_NAMES = {"user-agent", "referer", "cookie", "authorization", "origin"}

class UnsafeLiveSource(ValueError):
    pass


def _public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or
                ip.is_reserved or ip.is_unspecified)


def validate_https_url(url: str, *, resolve_dns: bool = True) -> str:
    p = urlsplit(url)
    if p.scheme.lower() != "https":
        raise UnsafeLiveSource("LIVE_SOURCE_HTTPS_REQUIRED")
    if not p.hostname or p.username or p.password:
        raise UnsafeLiveSource("LIVE_SOURCE_INVALID_AUTHORITY")
    if p.port not in (None, 443):
        raise UnsafeLiveSource("LIVE_SOURCE_PORT_NOT_ALLOWED")
    # Literal IPs must be globally routable. Hostnames get a preflight DNS check.
    try:
        ipaddress.ip_address(p.hostname)
    except ValueError:
        pass
    else:
        if not _public_ip(p.hostname):
            raise UnsafeLiveSource("LIVE_SOURCE_NON_PUBLIC_IP")
        return url
    if resolve_dns:
        try:
            answers = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeLiveSource("LIVE_SOURCE_DNS_FAILED") from exc
        if not answers:
            raise UnsafeLiveSource("LIVE_SOURCE_DNS_EMPTY")
        for answer in answers:
            ip = answer[4][0]
            if not _public_ip(ip):
                raise UnsafeLiveSource("LIVE_SOURCE_DNS_NON_PUBLIC")
    return url


def sanitize_headers(headers: dict[str, str] | None) -> dict[str, str]:
    clean: dict[str, str] = {}
    for name, value in (headers or {}).items():
        key = str(name).strip().lower()
        if key not in SAFE_HEADER_NAMES:
            continue
        value = str(value)
        if "\r" in value or "\n" in value:
            raise UnsafeLiveSource("LIVE_SOURCE_HEADER_INJECTION")
        clean[key] = value
    return clean


def validate_provider_source(source: LiveSource) -> LiveSource:
    validate_https_url(source.manifest_url)
    return LiveSource(
        platform=source.platform,
        target_key=source.target_key,
        source_id=source.source_id,
        manifest_url=source.manifest_url,
        headers=sanitize_headers(source.headers),
    )


def ffmpeg_http_args(headers: dict[str, str] | None) -> list[str]:
    """Create FFmpeg input options without logging their values.

    -headers applies to HTTP requests made by HLS/DASH demuxers. Values remain in the
    child process argv, so the recorder container must not expose process inspection
    to untrusted tenants. A future recorder sidecar can move credentials behind a
    local authenticated fetch proxy if stronger isolation is needed.
    """
    headers = sanitize_headers(headers)
    args: list[str] = []
    if "user-agent" in headers:
        args += ["-user_agent", headers.pop("user-agent")]
    if "referer" in headers:
        args += ["-referer", headers.pop("referer")]
    if headers:
        block = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        args += ["-headers", block]
    return args
