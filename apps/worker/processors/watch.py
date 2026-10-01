from providers.instagram.watch import InstagramWatchProvider
from providers.instagram.apify_stories import ApifyInstagramStoriesProvider, story_job_input
from providers.base import DiscoveredMedia
from core.config import settings
from core.watches import WatchService

async def process_poll_watch(job,db):
    watch_id=(job["input"] or {}).get("watch_id")
    service=WatchService(db.pool)
    watch=await service.get(watch_id)
    if not watch or watch["status"]!="active": return {"skipped":True}
    if watch["platform"]!="instagram": raise ValueError("Unsupported watch platform")

    cursor=dict(watch["cursor"])
    all_items=[]
    new_cursor=dict(cursor)
    queued=0
    try:
        if watch["content_mode"] in ("posts","both"):
            posts,post_cursor=await InstagramWatchProvider(db.pool).discover(watch["target_key"],cursor.get("posts",{}),12)
            for item in posts: item.content_kind="post"
            all_items.extend(posts); new_cursor["posts"]=post_cursor
        if watch["content_mode"] in ("stories","both"):
            prior=cursor.get("stories",{})
            provider=ApifyInstagramStoriesProvider(
                settings.apify_api_token,settings.apify_stories_actor_id,
                settings.apify_max_total_charge_usd,settings.apify_story_limit)
            resolved=await provider.resolve(watch["target_display"],only_new=True)
            story_ids=[media.platform_media_id for media in resolved]
            if prior.get("initialized"):
                target_watches=await service.list_active_story_watches("instagram",watch["target_display"])
                for media in resolved:
                    discovered=DiscoveredMedia(
                        media.platform_media_id,media.canonical_url,media.published_at,"story")
                    for target_watch in target_watches:
                        if await service.mark_seen_and_job(
                            target_watch,discovered,"deliver_story",story_job_input(media)):
                            queued+=1
            new_cursor["stories"]={"initialized":True,"recent_story_ids":story_ids[:100]}

        for item in sorted(all_items,key=lambda x: x.published_at or 0):
            if await service.mark_seen_and_job(watch,item): queued+=1
        await service.update_poll(watch["id"],new_cursor,new_items=queued)
        return {"discovered":len(all_items),"queued":queued,"mode":watch["content_mode"]}
    except Exception as exc:
        await service.update_poll(watch["id"],cursor,str(exc)[:500])
        raise
