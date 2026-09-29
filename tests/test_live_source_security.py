import socket
import pytest
from providers.live import LiveSource
from core.live_source_security import (
    UnsafeLiveSource, validate_https_url, sanitize_headers, ffmpeg_http_args,
)

@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "http://example.com/live.m3u8", "https://127.0.0.1/x",
    "https://169.254.169.254/latest/meta-data", "https://10.0.0.4/live.m3u8",
    "https://user:pass@example.com/live.m3u8", "https://example.com:8443/live.m3u8",
])
def test_rejects_unsafe_manifest_urls(url):
    with pytest.raises(UnsafeLiveSource):
        validate_https_url(url, resolve_dns=False)

def test_allows_normal_https_without_dns_for_unit_test():
    assert validate_https_url("https://cdn.example.com/live.m3u8", resolve_dns=False).startswith("https://")

def test_header_allowlist_and_injection_rejection():
    h=sanitize_headers({"User-Agent":"SocialSaver", "X-Debug":"secret", "Cookie":"a=b"})
    assert h == {"user-agent":"SocialSaver", "cookie":"a=b"}
    with pytest.raises(UnsafeLiveSource):
        sanitize_headers({"Referer":"https://ok/\r\nX-Evil: yes"})

def test_ffmpeg_args_do_not_enable_file_protocol():
    args=ffmpeg_http_args({"User-Agent":"ua", "Referer":"https://instagram.com/"})
    assert "-user_agent" in args and "-referer" in args
