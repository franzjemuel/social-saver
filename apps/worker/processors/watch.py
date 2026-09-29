from providers.instagram.watch import InstagramWatchProvider
from providers.instagram.stories import InstagramStoryDiscovery
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
    try:
        if watch["content_mode"] in ("posts","both"):
            posts,post_cursor=await InstagramWatchProvider(db.pool).discover(watch["target_key"],cursor.get("posts",{}),12)
            for item in posts: item.content_kind="post"
            all_items.extend(posts); new_cursor["posts"]=post_cursor
        if watch["content_mode"] in ("stories","both"):
            stories,story_cursor=await InstagramStoryDiscovery(db.pool).discover(watch["target_key"],cursor.get("stories",{}),25)
            all_items.extend(stories); new_cursor["stories"]=story_cursor

        queued=0
        for item in sorted(all_items,key=lambda x: x.published_at or 0):
            if await service.mark_seen_and_job(watch,item): queued+=1
        await service.update_poll(watch["id"],new_cursor,new_items=queued)
        return {"discovered":len(all_items),"queued":queued,"mode":watch["content_mode"]}
    except Exception as exc:
        await service.update_poll(watch["id"],cursor,str(exc)[:500])
        raise
