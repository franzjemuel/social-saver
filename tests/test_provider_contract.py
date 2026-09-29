from providers.instagram.provider import InstagramProvider

def test_instagram_supports_only_media_urls():
    p = InstagramProvider()
    assert p.supports("https://www.instagram.com/reel/ABC123/")
    assert p.supports("https://www.instagram.com/p/ABC123/")
    assert not p.supports("https://www.instagram.com/someprofile/")
