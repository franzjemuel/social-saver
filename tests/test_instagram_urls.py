import pytest
from providers.instagram.provider import normalize_instagram_url
from providers.base import UnsupportedUrl

def test_normalizes_tracking_query():
    assert normalize_instagram_url("https://www.instagram.com/reel/ABC123/?igsh=x") == "https://www.instagram.com/reel/ABC123/"

def test_rejects_profile():
    with pytest.raises(UnsupportedUrl): normalize_instagram_url("https://www.instagram.com/example/")

def test_rejects_lookalike_host():
    with pytest.raises(UnsupportedUrl): normalize_instagram_url("https://instagram.com.evil.test/reel/ABC/")
