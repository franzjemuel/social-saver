import pytest
from providers.instagram.story_provider import normalize_story_url
from providers.base import UnsupportedUrl

def test_story_url_normalizes():
    assert normalize_story_url("https://www.instagram.com/stories/example/123456/?x=1")=="https://www.instagram.com/stories/example/123456/"

def test_non_story_rejected():
    with pytest.raises(UnsupportedUrl):
        normalize_story_url("https://www.instagram.com/reel/ABC/")
