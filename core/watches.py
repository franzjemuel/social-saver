class WatchService:
    def __init__(self,pool): self.pool=pool
    async def count_active(self,user_id):
        return int(await self.pool.fetchval("select count(*) from watches where user_id=$1 and status='active'",user_id))
    async def create(self,user_id,platform,target_key,target_display,content_mode='posts',poll_interval_seconds=900):
        return await self.pool.fetchval(
          """insert into watches(user_id,platform,target_key,target_display,content_mode,poll_interval_seconds,next_poll_at)
          values($1,$2,$3,$4,$5,$6,now())
          on conflict(user_id,platform,target_type,target_key,content_mode) do update set
            status='active',poll_interval_seconds=excluded.poll_interval_seconds,next_poll_at=now(),last_error=null
          returning id""",user_id,platform,target_key,target_display,content_mode,poll_interval_seconds)

    async def save_friend(self,user_id,platform,target_key,target_display,poll_interval_seconds=60):
        return await self.pool.fetchval(
          """insert into watches(user_id,platform,target_key,target_display,content_mode,
             saved_friend,auto_archive,auto_deliver,priority,poll_interval_seconds,next_poll_at)
             values($1,$2,$3,$4,'stories',true,true,true,'high',$5,now())
             on conflict(user_id,platform,target_type,target_key,content_mode) do update set
               target_display=excluded.target_display,status='active',saved_friend=true,
               auto_archive=true,auto_deliver=true,priority='high',
               poll_interval_seconds=excluded.poll_interval_seconds,next_poll_at=now(),last_error=null
             returning id""",user_id,platform,target_key,target_display,poll_interval_seconds)

    async def list_saved_friends(self,user_id):
        return await self.pool.fetch(
          """select * from watches where user_id=$1 and saved_friend=true
             order by status='active' desc,target_display""",user_id)

    async def remove_saved_friend(self,user_id,target_display):
        name=target_display.lstrip('@').lower()
        return await self.pool.fetchval(
          """delete from watches where user_id=$1 and saved_friend=true
             and lower(target_display)=$2 returning id""",user_id,name)
    async def list(self,user_id):
        return await self.pool.fetch("select * from watches where user_id=$1 order by created_at desc",user_id)
    async def remove(self,user_id,watch_id):
        return await self.pool.fetchval("delete from watches where id=$1 and user_id=$2 returning id",watch_id,user_id)
    async def get(self,watch_id):
        return await self.pool.fetchrow("select * from watches where id=$1",watch_id)
    async def list_active_story_watches(self,platform,target_display):
        return await self.pool.fetch(
          """select * from watches where platform=$1 and lower(target_display)=lower($2)
             and status='active' and content_mode in ('stories','both')""",platform,target_display)
    async def update_poll(self,watch_id,cursor,error=None,new_items=0):
        # Saved Friends adapt their cadence without changing the provider contract.
        # New content => fast (60s). Quiet polls gradually back off to warm (180s)
        # and idle (900s). Errors back off to avoid hammering a challenged session.
        await self.pool.execute(
          """update watches set cursor=$2,last_polled_at=now(),
          consecutive_failures=case when $3::text is null then 0 else consecutive_failures+1 end,
          last_error=$3,
          status=case when $3::text is null then status when consecutive_failures+1>=5 then 'error' else status end,
          last_new_item_at=case when $4::int > 0 then now() else last_new_item_at end,
          idle_poll_count=case when $3::text is not null then idle_poll_count
                               when $4::int > 0 then 0 else idle_poll_count+1 end,
          polling_state=case
            when saved_friend=false then polling_state
            when $3::text is not null then 'idle'
            when $4::int > 0 then 'fast'
            when idle_poll_count+1 < 3 then 'fast'
            when idle_poll_count+1 < 8 then 'warm'
            else 'idle' end,
          poll_interval_seconds=case
            when saved_friend=false then poll_interval_seconds
            when $3::text is not null then least(3600, greatest(900, poll_interval_seconds*2))
            when $4::int > 0 then 60
            when idle_poll_count+1 < 3 then 60
            when idle_poll_count+1 < 8 then 180
            else 900 end,
          next_poll_at=now()+make_interval(secs=>case
            when saved_friend=false then poll_interval_seconds
            when $3::text is not null then least(3600, greatest(900, poll_interval_seconds*2))
            when $4::int > 0 then 60
            when idle_poll_count+1 < 3 then 60
            when idle_poll_count+1 < 8 then 180
            else 900 end)
          where id=$1""",str(watch_id),cursor,error,new_items)
    async def mark_seen_and_job(self,watch,media,job_type="resolve_media",input_data=None):
        # asyncpg may return UUID columns as UUID objects while the production
        # pool's prepared statements expect string UUID inputs. Normalize IDs
        # crossing back into SQL so scheduled watches work with either form.
        watch_id=str(watch["id"])
        user_id=str(watch["user_id"])
        async with self.pool.acquire() as con:
            async with con.transaction():
                fresh=await con.fetchval(
                  """insert into watch_seen_items(watch_id,content_kind,platform_media_id) values($1,$2,$3)
                  on conflict do nothing returning platform_media_id""",watch_id,media.content_kind,media.platform_media_id)
                if not fresh: return None
                payload=input_data or {"url":media.canonical_url}
                payload.update({"watch_id":str(watch["id"]),"archive":bool(watch["auto_archive"]),
                                "auto_delivery":bool(watch["auto_deliver"]),"content_kind":media.content_kind})
                job_id=await con.fetchval(
                  """insert into jobs(user_id,telegram_chat_id,job_type,input)
                  select $1,ta.telegram_user_id,$2,$3 from telegram_accounts ta
                  where ta.app_user_id=$1 order by ta.created_at limit 1 returning id""",
                  user_id,job_type,payload)
                await con.execute(
                  "insert into watch_deliveries(watch_id,content_kind,platform_media_id,job_id) values($1,$2,$3,$4)",
                  watch_id,media.content_kind,media.platform_media_id,str(job_id))
                await con.execute("select pgmq.send('media_jobs',jsonb_build_object('version',1,'job_id',$1::text),0)",str(job_id))
                return job_id
