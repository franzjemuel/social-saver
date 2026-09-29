class Repository:
    def __init__(self, pool):
        self.pool = pool

    async def get_or_create_telegram_user(self, telegram_user_id, username, first_name):
        async with self.pool.acquire() as con:
            async with con.transaction():
                existing = await con.fetchrow(
                    "select app_user_id from telegram_accounts where telegram_user_id=$1",
                    telegram_user_id
                )
                if existing:
                    return existing["app_user_id"]
                user_id = await con.fetchval("insert into app_users default values returning id")
                await con.execute(
                    """insert into telegram_accounts
                    (telegram_user_id, app_user_id, username, first_name)
                    values ($1,$2,$3,$4)""",
                    telegram_user_id, user_id, username, first_name
                )
                return user_id

    async def create_job(self, user_id, chat_id, job_type, input_data=None, *, source_channel="telegram", client_request_id=None):
        """Create a job, idempotently when client_request_id is supplied."""
        if client_request_id:
            return await self.pool.fetchval(
                """insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel,client_request_id)
                values($1,$2,$3,$4,$5,$6)
                on conflict (user_id,client_request_id) where client_request_id is not null
                do update set client_request_id=excluded.client_request_id
                returning id""",
                user_id, chat_id, job_type, input_data or {}, source_channel, client_request_id
            )
        return await self.pool.fetchval(
            """insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel)
            values($1,$2,$3,$4,$5) returning id""",
            user_id, chat_id, job_type, input_data or {}, source_channel
        )

    async def get_job(self, job_id):
        return await self.pool.fetchrow("select * from jobs where id=$1", job_id)

    async def get_owned_job_status(self, user_id, job_id):
        """Return the customer-safe projection of a job only when it belongs to user_id."""
        return await self.pool.fetchrow(
            """select id, job_type, status, progress, error_code, created_at, started_at, completed_at
               from jobs where id=$1 and user_id=$2""",
            job_id, user_id
        )

    async def start_job(self, job_id):
        await self.pool.execute(
            "update jobs set status='running',started_at=coalesce(started_at,now()) where id=$1 and status <> 'completed'",
            job_id
        )

    async def complete_job(self, job_id, result):
        await self.pool.execute(
            "update jobs set status='completed',progress=100,result=$2,completed_at=now() where id=$1",
            job_id, result
        )

    async def fail_job(self, job_id, code, message):
        await self.pool.execute(
            """update jobs set status='failed',error_code=$2,error_message=$3,completed_at=now()
            where id=$1 and status <> 'completed'""", job_id, code, message[:1000]
        )

    async def upsert_resolved_media(self, media):
        async with self.pool.acquire() as con:
            async with con.transaction():
                item_id = await con.fetchval(
                    """insert into media_items
                    (platform,platform_media_id,canonical_url,creator_username,media_type,caption,published_at,resolver_strategy,metadata)
                    values($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    on conflict(platform,platform_media_id) do update set
                      canonical_url=excluded.canonical_url,
                      creator_username=excluded.creator_username,
                      media_type=excluded.media_type,
                      caption=excluded.caption,
                      published_at=excluded.published_at,
                      resolver_strategy=excluded.resolver_strategy,
                      metadata=excluded.metadata
                    returning id""",
                    media.platform, media.platform_media_id, media.canonical_url,
                    media.creator_username, media.media_type, media.caption,
                    media.published_at, media.strategy, media.metadata
                )
                for asset in media.assets:
                    await con.execute(
                        """insert into media_assets
                        (media_item_id,position,asset_type,source_url,width,height,duration_seconds)
                        values($1,$2,$3,$4,$5,$6,$7)
                        on conflict(media_item_id,position) do update set
                          asset_type=excluded.asset_type,
                          source_url=excluded.source_url,
                          width=excluded.width,
                          height=excluded.height,
                          duration_seconds=excluded.duration_seconds""",
                        item_id, asset.position, asset.asset_type, asset.source_url,
                        asset.width, asset.height, asset.duration_seconds
                    )
                return item_id

    async def update_asset_storage(self, media_item_id, position, *, size_bytes, sha256, storage_provider=None, storage_key=None):
        await self.pool.execute(
            """update media_assets
               set size_bytes=$3, sha256=$4, storage_provider=$5, storage_key=$6,
                   archived_at=case when $5 is null then archived_at else now() end
               where media_item_id=$1 and position=$2""",
            media_item_id, position, size_bytes, sha256, storage_provider, storage_key
        )


    async def get_media_asset(self, media_item_id, position):
        return await self.pool.fetchrow("select * from media_assets where media_item_id=$1 and position=$2", media_item_id, position)

    async def get_stored_object_by_sha(self, sha256):
        return await self.pool.fetchrow("select * from stored_objects where sha256=$1 and deleted_at is null", sha256)

    async def create_stored_object(self, sha256, key, size_bytes, content_type):
        return await self.pool.fetchval("""insert into stored_objects(sha256,storage_key,size_bytes,content_type)
            values($1,$2,$3,$4) on conflict(sha256) do update set deleted_at=null returning id""",
            sha256,key,size_bytes,content_type)

    async def get_or_create_archive_entry(self, user_id, media_item_id):
        return await self.pool.fetchval("""insert into archive_entries(user_id,media_item_id) values($1,$2)
            on conflict(user_id,media_item_id) do update set deleted_at=null returning id""", user_id,media_item_id)

    async def attach_archive_asset(self, entry_id, media_asset_id, stored_object_id):
        await self.pool.execute("""insert into archive_entry_assets(archive_entry_id,media_asset_id,stored_object_id)
            values($1,$2,$3) on conflict(archive_entry_id,media_asset_id)
            do update set stored_object_id=excluded.stored_object_id""",entry_id,media_asset_id,stored_object_id)

    async def list_archive_entries(self, user_id, limit=10):
        return await self.pool.fetch("""select ae.id,ae.created_at,mi.platform,mi.creator_username,mi.media_type,
            mi.canonical_url,count(aea.media_asset_id)::int asset_count
            from archive_entries ae join media_items mi on mi.id=ae.media_item_id
            left join archive_entry_assets aea on aea.archive_entry_id=ae.id
            where ae.user_id=$1 and ae.deleted_at is null group by ae.id,mi.id
            order by ae.created_at desc limit $2""",user_id,limit)

    async def get_owned_archive_entry(self, user_id, entry_id):
        return await self.pool.fetchrow(
            """select ae.id,ae.created_at,mi.platform,mi.creator_username,mi.media_type,
                      mi.canonical_url,mi.caption,mi.published_at
               from archive_entries ae join media_items mi on mi.id=ae.media_item_id
               where ae.id=$1 and ae.user_id=$2 and ae.deleted_at is null""",
            entry_id,user_id)

    async def list_archive_assets_safe(self, user_id, entry_id):
        return await self.pool.fetch(
            """select ma.id media_asset_id,ma.position,ma.asset_type,ma.width,ma.height,
                      ma.duration_seconds,so.size_bytes,so.content_type
               from archive_entries ae join archive_entry_assets aea on aea.archive_entry_id=ae.id
               join media_assets ma on ma.id=aea.media_asset_id join stored_objects so on so.id=aea.stored_object_id
               where ae.id=$1 and ae.user_id=$2 and ae.deleted_at is null and so.deleted_at is null
               order by ma.position""",entry_id,user_id)

    async def list_archive_assets(self, user_id, entry_id):
        return await self.pool.fetch("""select ma.id media_asset_id,ma.position,ma.asset_type,so.storage_key,so.size_bytes
            from archive_entries ae join archive_entry_assets aea on aea.archive_entry_id=ae.id
            join media_assets ma on ma.id=aea.media_asset_id join stored_objects so on so.id=aea.stored_object_id
            where ae.id=$1 and ae.user_id=$2 and ae.deleted_at is null and so.deleted_at is null
            order by ma.position""",entry_id,user_id)

    async def get_owned_archive_object(self, user_id, entry_id, media_asset_id):
        """Resolve a physical object only through a live archive row owned by user_id.

        This is the authorization boundary used before minting an R2 bearer URL.
        Do not replace this with a direct stored_objects lookup.
        """
        return await self.pool.fetchrow(
            """select so.id, so.storage_key, so.size_bytes, so.content_type
               from archive_entries ae
               join archive_entry_assets aea on aea.archive_entry_id=ae.id
               join stored_objects so on so.id=aea.stored_object_id
               where ae.id=$1 and ae.user_id=$2 and aea.media_asset_id=$3
                 and ae.deleted_at is null and so.deleted_at is null""",
            entry_id, user_id, media_asset_id
        )

    async def soft_delete_archive_entry(self, user_id, entry_id):
        async with self.pool.acquire() as con:
            async with con.transaction():
                owned=await con.fetchval("update archive_entries set deleted_at=now() where id=$1 and user_id=$2 and deleted_at is null returning id",entry_id,user_id)
                if not owned: return None
                return await con.fetch("""select so.id,so.storage_key from archive_entry_assets mine
                    join stored_objects so on so.id=mine.stored_object_id where mine.archive_entry_id=$1 and so.deleted_at is null
                    and not exists(select 1 from archive_entry_assets other join archive_entries ae2 on ae2.id=other.archive_entry_id
                    where other.stored_object_id=mine.stored_object_id and ae2.deleted_at is null and ae2.id<>$1)""",entry_id)

    async def list_unreferenced_objects_for_archive_entry(self, entry_id):
        """Return physical objects formerly attached to entry_id that have no live owner.

        This is intentionally re-evaluated by the worker after the user-facing soft delete,
        avoiding deletion of a deduplicated object that another tenant still references.
        """
        return await self.pool.fetch("""select distinct so.id,so.storage_key from archive_entry_assets mine
            join stored_objects so on so.id=mine.stored_object_id
            where mine.archive_entry_id=$1 and so.deleted_at is null
            and not exists(select 1 from archive_entry_assets other
                join archive_entries ae2 on ae2.id=other.archive_entry_id
                where other.stored_object_id=mine.stored_object_id and ae2.deleted_at is null)""", entry_id)

    async def mark_stored_object_deleted(self, object_id):
        await self.pool.execute("update stored_objects set deleted_at=now() where id=$1",object_id)

    async def dashboard_summary(self, user_id):
        """Tenant-scoped dashboard projection for the Mini App API."""
        return await self.pool.fetchrow(
            """select
                 (select count(*)::int from archive_entries where user_id=$1 and deleted_at is null) archive_count,
                 (select count(*)::int from watches where user_id=$1 and status='active') active_watch_count,
                 (select count(*)::int from jobs where user_id=$1 and status in ('queued','running')) active_job_count""",
            user_id,
        )

    async def list_owned_watches(self, user_id, limit=100):
        return await self.pool.fetch(
            """select id,platform,target_type,target_key,target_display,status,polling_state,
                      next_poll_at,last_polled_at,last_new_item_at,last_error
               from watches where user_id=$1 order by created_at desc limit $2""",
            user_id, limit,
        )

    async def get_account_deletion_state(self, user_id):
        return await self.pool.fetchrow(
            "select deletion_requested_at,deletion_started_at from app_users where id=$1", user_id
        )

    async def request_account_deletion(self, user_id):
        """Atomically disable user automation and hide all archives.

        Returns False when the account no longer exists, otherwise True. This is
        idempotent so a retried DELETE /v1/me cannot reactivate or duplicate data.
        """
        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    """update app_users set deletion_requested_at=coalesce(deletion_requested_at,now())
                       where id=$1 returning deletion_requested_at""", user_id
                )
                if row is None:
                    return False
                await con.execute(
                    "update watches set status='paused', next_poll_at=now() where user_id=$1 and status <> 'paused'",
                    user_id,
                )
                await con.execute(
                    "update archive_entries set deleted_at=coalesce(deleted_at,now()) where user_id=$1",
                    user_id,
                )
                return True

    async def begin_account_purge(self, user_id):
        return await self.pool.fetchval(
            """update app_users set deletion_started_at=coalesce(deletion_started_at,now())
               where id=$1 and deletion_requested_at is not null returning id""", user_id
        )

    async def list_unreferenced_objects_for_user(self, user_id):
        """Physical blobs from this user's archives that no live tenant still references."""
        return await self.pool.fetch(
            """select distinct so.id,so.storage_key
               from archive_entries mine
               join archive_entry_assets mea on mea.archive_entry_id=mine.id
               join stored_objects so on so.id=mea.stored_object_id
               where mine.user_id=$1 and so.deleted_at is null
                 and not exists(
                   select 1 from archive_entry_assets other
                   join archive_entries live on live.id=other.archive_entry_id
                   where other.stored_object_id=so.id and live.deleted_at is null
                 )""", user_id
        )

    async def finalize_account_deletion(self, user_id):
        """Delete the tenant root only after worker-side object cleanup succeeds."""
        return await self.pool.fetchval(
            """delete from app_users where id=$1 and deletion_requested_at is not null returning id""",
            user_id,
        )
