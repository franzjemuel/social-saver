from datetime import datetime, timezone

import pytest

from apps.worker.processors import watch as processor
from providers.base import ResolvedAsset, ResolvedMedia


def story():
    return ResolvedMedia(
        platform="instagram",platform_media_id="story-1",
        canonical_url="https://www.instagram.com/stories/nasa/story-1/",
        media_type="story_video",
        assets=[ResolvedAsset(0,"video","https://scontent.cdninstagram.com/story.mp4")],
        creator_username="nasa",published_at=datetime.now(timezone.utc),
        strategy="apify_public_stories_v1")


class FakeService:
    def __init__(self,watch):
        self.watch=watch; self.marked=[]; self.updated=[]
    async def get(self,watch_id): return self.watch
    async def list_active_story_watches(self,platform,target):
        return [self.watch,{**self.watch,"id":"watch-2","user_id":"user-2"}]
    async def mark_seen_and_job(self,watch,media,job_type="resolve_media",input_data=None):
        self.marked.append((watch["id"],media.platform_media_id,job_type,input_data))
        return f"job-{watch['id']}"
    async def update_poll(self,watch_id,cursor,error=None,new_items=0):
        self.updated.append((watch_id,cursor,error,new_items))


class FakeProvider:
    def __init__(self,*args): pass
    async def resolve(self,target,only_new=False):
        assert target=="nasa" and only_new is True
        return [story()]


@pytest.mark.asyncio
@pytest.mark.parametrize("initialized,expected_jobs",[(False,0),(True,2)])
async def test_public_story_poll_baselines_then_fans_out(monkeypatch,initialized,expected_jobs):
    watch={
        "id":"watch-1","user_id":"user-1","status":"active","platform":"instagram",
        "target_key":"nasa","target_display":"nasa","content_mode":"stories",
        "cursor":{"stories":{"initialized":True}} if initialized else {},
        "auto_archive":False,"auto_deliver":True,
    }
    service=FakeService(watch)
    monkeypatch.setattr(processor,"WatchService",lambda pool:service)
    monkeypatch.setattr(processor,"ApifyInstagramStoriesProvider",FakeProvider)
    db=type("DB",(),{"pool":object()})()
    result=await processor.process_poll_watch({"input":{"watch_id":"watch-1"}},db)
    assert result["queued"]==expected_jobs
    assert len(service.marked)==expected_jobs
    assert service.updated[-1][1]["stories"]["initialized"] is True
    if expected_jobs:
        assert {entry[0] for entry in service.marked}=={"watch-1","watch-2"}
        assert all(entry[2]=="deliver_story" for entry in service.marked)
