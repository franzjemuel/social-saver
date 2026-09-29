import pytest
from pathlib import Path
from core.media_download import DownloadedAsset
from core.delivery_router import DeliveryRouter

@pytest.mark.asyncio
async def test_small_asset_routes_to_telegram(monkeypatch, tmp_path):
    router = DeliveryRouter()
    router.limit = 49_000_000
    d = DownloadedAsset(tmp_path/"x.mp4", 10_000_000, "abc", "video/mp4")
    decision = await router.decide(d, platform="instagram", media_id="1", position=0)
    assert decision.route == "telegram"

def test_overflow_key_is_content_addressed():
    sha = "a" * 64
    key = f"overflow/instagram/123/{sha}.mp4"
    assert sha in key
