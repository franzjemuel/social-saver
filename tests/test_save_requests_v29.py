import pytest
from core.save_requests import normalize_save_url
from providers.base import UnsupportedUrl

def test_instagram_save_normalizes_tracking_query_away():
    r=normalize_save_url("https://www.instagram.com/reel/ABC123/?igsh=tracking")
    assert r.platform == "instagram"
    assert r.canonical_url == "https://www.instagram.com/reel/ABC123/"

def test_save_rejects_unknown_provider():
    with pytest.raises(UnsupportedUrl):
        normalize_save_url("https://example.com/video/123")

def test_save_rejects_instagram_profile():
    with pytest.raises(UnsupportedUrl):
        normalize_save_url("https://www.instagram.com/someone/")
