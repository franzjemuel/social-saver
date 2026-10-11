from dataclasses import dataclass

from providers.tiktok.constants import INITIAL_PROFILE_IMPORT_POST_LIMIT


@dataclass(frozen=True)
class ProfileMediaJob:
    """An owned media job plus whether this request created its queue work."""

    id: object
    created: bool


@dataclass(frozen=True)
class ProfileImportValidationJob:
    """An owned validation job plus whether this request enqueued new work."""

    id: object
    created: bool


@dataclass(frozen=True)
class ProfileImportJob:
    """An owned metadata-import workflow plus whether it queued worker work."""

    id: object
    created: bool
    ready: bool = False


class ProfileImportIdempotencyConflict(ValueError):
    """One client retry key cannot be reused for a different profile target."""


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

    async def create_owned_profile_import_validation_job(
        self, user_id, chat_id, target, client_request_id, *, queue_name,
    ):
        """Atomically enqueue one active, tenant-owned TikTok validation job.

        The request id protects network retries; the advisory lock/active-target
        lookup protects duplicate UI taps without revealing other tenants.
        """
        lock_key = f"profile-import-validation:{user_id}:{target}"
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute("select pg_advisory_xact_lock(hashtextextended($1, 0))", lock_key)
                prior = await con.fetchrow(
                    """select id, job_type, input->>'target' as target
                       from jobs where user_id=$1
                         and (client_request_id=$2 or coalesce(input->'idempotency_keys', '[]'::jsonb) ? $2)
                       for update""",
                    user_id, client_request_id,
                )
                if prior is not None:
                    if prior["job_type"] != "validate_profile_import" or prior["target"] != target:
                        raise ProfileImportIdempotencyConflict("idempotency key target conflict")
                    return ProfileImportValidationJob(prior["id"], False)

                active = await con.fetchrow(
                    """select id from jobs
                       where user_id=$1 and job_type='validate_profile_import'
                         and status in ('queued','running')
                         and input->>'platform'='tiktok' and input->>'target'=$2
                       order by created_at limit 1""",
                    user_id, target,
                )
                if active is not None:
                    # Keep every coalesced request key durable. The existing
                    # unique client_request_id remains the primary retry
                    # mechanism; this private array preserves collision checks
                    # for duplicate taps that joined an already-active job.
                    await con.execute(
                        """update jobs set input=jsonb_set(
                               input, '{idempotency_keys}',
                               coalesce(input->'idempotency_keys', '[]'::jsonb) || to_jsonb($2::text)
                             ) where id=$1""",
                        active["id"], client_request_id,
                    )
                    return ProfileImportValidationJob(active["id"], False)

                created = await con.fetchrow(
                    """insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel,client_request_id)
                       values($1,$2,'validate_profile_import',
                              jsonb_build_object('platform','tiktok','target',$3::text,
                                                 'idempotency_keys',jsonb_build_array($4::text)),
                              'mini_app',$4)
                       returning id""",
                    user_id, chat_id, target, client_request_id,
                )
                # The PGMQ message shares the transaction with its job row. A
                # queue error rolls back instead of stranding an active job.
                await con.fetchval(
                    "select * from pgmq.send($1, jsonb_build_object('version',1,'job_id',$2::text), 0)",
                    queue_name, str(created["id"]),
                )
                return ProfileImportValidationJob(created["id"], True)

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

    async def update_job_progress(self, job_id, progress):
        """Coarse worker progress only; provider internals remain private."""
        await self.pool.execute(
            "update jobs set progress=$2 where id=$1 and status='running'", job_id, progress,
        )

    async def fail_job(self, job_id, code, message):
        await self.pool.execute(
            """update jobs set status='failed',error_code=$2,error_message=$3,completed_at=now()
            where id=$1 and status <> 'completed'""", job_id, code, message[:1000]
        )

    async def get_owned_profile_import_validation(self, user_id, job_id):
        """Return internal validation state only after an ownership/type check."""
        return await self.pool.fetchrow(
            """select id, status, result, error_code
               from jobs
               where id=$1 and user_id=$2 and job_type='validate_profile_import'""",
            job_id, user_id,
        )

    async def create_owned_profile_import_job(self, user_id, chat_id, validation_job_id, *, queue_name):
        """Confirm one owned validation and atomically queue its bounded import.

        The lock is keyed to the tenant's stable account identity, so double
        taps *and separate previews of the same account* cannot create multiple
        logical imports. All account identity and target values come from the
        private validation result, never the browser.
        """
        async with self.pool.acquire() as con:
            async with con.transaction():
                validation = await con.fetchrow(
                    """select id, result from jobs
                       where id=$1 and user_id=$2 and job_type='validate_profile_import'
                         and status='completed' for update""",
                    validation_job_id, user_id,
                )
                if validation is None or not isinstance(validation["result"], dict):
                    return None
                result = validation["result"]
                target = result.get("target")
                account_id = result.get("platform_account_id")
                if result.get("platform") != "tiktok" or not isinstance(target, str) or not isinstance(account_id, str):
                    return None
                await con.execute(
                    "select pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"profile-import-confirm:{user_id}:tiktok:{account_id}",
                )

                prior = await con.fetchrow(
                    """select id, status, result from jobs
                       where user_id=$1 and job_type='import_profile'
                         and input->>'validation_job_id'=$2::uuid::text
                       order by created_at desc limit 1 for update""",
                    user_id, validation_job_id,
                )
                if prior is not None:
                    return ProfileImportJob(prior["id"], False, prior["status"] == "completed")

                active = await con.fetchrow(
                    """select id from jobs
                       where user_id=$1 and job_type='import_profile'
                         and status in ('queued','running')
                         and input->>'platform'='tiktok'
                         and input->>'expected_platform_account_id'=$2
                       order by created_at limit 1 for update""",
                    user_id, account_id,
                )
                if active is not None:
                    return ProfileImportJob(active["id"], False)

                existing_profile = await con.fetchrow(
                    """select id, platform, username, display_name, avatar_url
                       from archived_profiles
                       where user_id=$1 and platform='tiktok' and platform_account_id=$2
                       limit 1""",
                    user_id, account_id,
                )
                input_data = {
                    "platform": "tiktok", "target": target,
                    "expected_platform_account_id": account_id,
                    "validation_job_id": str(validation_job_id),
                    "limit": INITIAL_PROFILE_IMPORT_POST_LIMIT,
                }
                if existing_profile is not None:
                    completed = await con.fetchrow(
                        """insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel,status,progress,result,completed_at)
                           values($1,$2,'import_profile',$3,'mini_app','completed',100,$4,now()) returning id""",
                        user_id, chat_id, input_data,
                        {"profile_id": str(existing_profile["id"]), "posts_imported": 0},
                    )
                    return ProfileImportJob(completed["id"], True, True)

                created = await con.fetchrow(
                    """insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel)
                       values($1,$2,'import_profile',$3,'mini_app') returning id""",
                    user_id, chat_id, input_data,
                )
                await con.fetchval(
                    "select * from pgmq.send($1, jsonb_build_object('version',1,'job_id',$2::text), 0)",
                    queue_name, str(created["id"]),
                )
                return ProfileImportJob(created["id"], True)

    async def get_owned_profile_import_workflow(self, user_id, job_id):
        """Internal status needed to safely project validation/import workflow state."""
        return await self.pool.fetchrow(
            """select id, job_type, status, progress, result, error_code
               from jobs where id=$1 and user_id=$2
                 and job_type in ('validate_profile_import','import_profile')""",
            job_id, user_id,
        )

    async def get_owned_archived_profile_import_projection(self, user_id, profile_id):
        """Minimal owned profile projection for the import-ready API state."""
        return await self.pool.fetchrow(
            """select id, platform, username, display_name, avatar_url
               from archived_profiles where id=$1::uuid and user_id=$2""",
            profile_id, user_id,
        )

    async def get_owned_active_profile_import_workflow(self, user_id):
        """Newest restorable validation/import workflow for exactly one tenant."""
        return await self.pool.fetchrow(
            """select id, job_type, status, progress, result, error_code
               from jobs where user_id=$1
                 and ((job_type='validate_profile_import' and status in ('queued','running'))
                      or (job_type='validate_profile_import' and status='completed' and result ? 'platform_account_id'
                          and not exists (
                            select 1 from jobs imported
                            where imported.user_id=jobs.user_id and imported.job_type='import_profile'
                              and imported.input->>'validation_job_id'=jobs.id::text
                          ))
                      or (job_type='import_profile' and status in ('queued','running')))
               order by created_at desc limit 1""",
            user_id,
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

    async def upsert_archived_profile(self, user_id, profile):
        """Create or refresh a private mirrored social account for one tenant."""
        return await self.pool.fetchval(
            """insert into archived_profiles
                (user_id,platform,platform_account_id,username,display_name,bio,avatar_url,metadata)
               values($1,$2,$3,$4,$5,$6,$7,$8)
               on conflict(user_id,platform,platform_account_id) do update set
                 username=excluded.username, display_name=coalesce(excluded.display_name, archived_profiles.display_name),
                 bio=coalesce(excluded.bio, archived_profiles.bio), avatar_url=coalesce(excluded.avatar_url, archived_profiles.avatar_url),
                 metadata=excluded.metadata, last_observed_at=now()
               returning id""",
            user_id, profile.platform, profile.platform_account_id, profile.username,
            profile.display_name, profile.bio, profile.avatar_url, profile.metadata,
        )

    async def upsert_archived_post(self, user_id, archived_profile_id, post):
        """Refresh one post and its metadata without ever deleting archived history.

        The profile ownership check is intentionally in this repository boundary,
        rather than trusting a profile id received from a caller.
        """
        async with self.pool.acquire() as con:
            async with con.transaction():
                owned = await con.fetchval(
                    "select id from archived_profiles where id=$1 and user_id=$2",
                    archived_profile_id, user_id,
                )
                if owned is None:
                    raise PermissionError("Archived profile not found")
                row = await con.fetchrow(
                    """insert into archived_posts
                        (archived_profile_id,platform_post_id,original_url,media_type,caption,
                         published_at,thumbnail_url,is_present_on_original,metadata)
                       values($1,$2,$3,$4,$5,$6,$7,true,$8)
                       on conflict(archived_profile_id,platform_post_id) do update set
                         original_url=excluded.original_url, media_type=excluded.media_type,
                         caption=excluded.caption, published_at=excluded.published_at,
                         thumbnail_url=excluded.thumbnail_url,
                         is_present_on_original=true, last_observed_at=now(),
                         metadata=excluded.metadata
                       returning id, (xmax = 0) as created""",
                    archived_profile_id, post.platform_post_id, post.original_url,
                    post.media_type, post.caption, post.published_at, post.thumbnail_url,
                    post.metadata,
                )
                post_id = row["id"]
                for asset in post.assets:
                    await con.execute(
                        """insert into archived_post_media_assets
                            (archived_post_id,position,asset_type,source_url,thumbnail_url,
                             duration_seconds,width,height,metadata)
                           values($1,$2,$3,$4,$5,$6,$7,$8,$9)
                           on conflict(archived_post_id,position) do update set
                             asset_type=excluded.asset_type, source_url=excluded.source_url,
                             thumbnail_url=excluded.thumbnail_url,
                             duration_seconds=excluded.duration_seconds, width=excluded.width,
                             height=excluded.height, metadata=excluded.metadata
                           where archived_post_media_assets.stored_object_id is null""",
                        post_id, asset.position, asset.asset_type, asset.source_url,
                        asset.thumbnail_url, asset.duration_seconds, asset.width,
                        asset.height, asset.metadata,
                    )
                if post.engagement is not None:
                    metric = post.engagement
                    await con.execute(
                        """insert into archived_post_engagement_snapshots
                            (archived_post_id,observed_at,view_count,like_count,comment_count,
                             repost_count,share_count,save_count,metadata)
                           values($1,$2,$3,$4,$5,$6,$7,$8,$9)
                           on conflict(archived_post_id,observed_at) do update set
                             view_count=excluded.view_count, like_count=excluded.like_count,
                             comment_count=excluded.comment_count, repost_count=excluded.repost_count,
                             share_count=excluded.share_count, save_count=excluded.save_count,
                             metadata=excluded.metadata""",
                        post_id, metric.observed_at, metric.view_count, metric.like_count,
                        metric.comment_count, metric.repost_count, metric.share_count,
                        metric.save_count, metric.metadata,
                    )
                return post_id, bool(row["created"])

    async def mark_archived_post_not_present(self, user_id, archived_profile_id, platform_post_id):
        """Keep a historical copy while recording that it vanished at the source."""
        return await self.pool.fetchval(
            """update archived_posts post set is_present_on_original=false
               from archived_profiles profile
               where post.archived_profile_id=profile.id and profile.user_id=$1
                 and profile.id=$2 and post.platform_post_id=$3
               returning post.id""",
            user_id, archived_profile_id, platform_post_id,
        )

    async def list_owned_archived_profiles(self, user_id, *, limit=25, offset=0):
        """Safe tenant-scoped profile archive summaries for the browser API."""
        return await self.pool.fetch(
            """select profile.id, profile.platform, profile.platform_account_id,
                      profile.username, profile.display_name, profile.bio, profile.avatar_url,
                      profile.first_archived_at, profile.last_observed_at,
                      count(post.id)::int as post_count,
                      count(post.id) filter (where post.is_present_on_original)::int as present_post_count,
                      count(post.id) filter (where not post.is_present_on_original)::int as removed_post_count,
                      max(post.published_at) as latest_post_at
               from archived_profiles profile
               left join archived_posts post on post.archived_profile_id=profile.id
               where profile.user_id=$1
               group by profile.id
               order by profile.last_observed_at desc, profile.id desc
               limit $2 offset $3""",
            user_id, limit, offset,
        )

    async def get_owned_archived_profile(self, user_id, profile_id):
        """Return a safe profile summary only when it belongs to this tenant."""
        return await self.pool.fetchrow(
            """select profile.id, profile.platform, profile.platform_account_id,
                      profile.username, profile.display_name, profile.bio, profile.avatar_url,
                      profile.first_archived_at, profile.last_observed_at,
                      count(post.id)::int as post_count,
                      count(post.id) filter (where post.is_present_on_original)::int as present_post_count,
                      count(post.id) filter (where not post.is_present_on_original)::int as removed_post_count,
                      max(post.published_at) as latest_post_at
               from archived_profiles profile
               left join archived_posts post on post.archived_profile_id=profile.id
               where profile.user_id=$1 and profile.id=$2
               group by profile.id""",
            user_id, profile_id,
        )

    async def list_owned_archived_profile_posts(self, user_id, profile_id, *, limit=30, offset=0):
        """List a tenant-owned profile's posts with only the latest engagement snapshot.

        ``None`` means the profile is missing or belongs to another tenant; an
        empty list means an owned profile currently has no posts.
        """
        owned = await self.pool.fetchval(
            "select id from archived_profiles where id=$1 and user_id=$2",
            profile_id, user_id,
        )
        if owned is None:
            return None
        return await self.pool.fetch(
            """select post.id, post.platform_post_id, post.original_url, post.media_type,
                      post.caption, post.published_at, post.thumbnail_url,
                      post.is_present_on_original, post.first_archived_at, post.last_observed_at,
                      exists(select 1 from archived_post_media_assets asset
                             join stored_objects object on object.id=asset.stored_object_id
                             where asset.archived_post_id=post.id and object.deleted_at is null) as has_archived_media,
                      coalesce(photo_assets.total_photo_assets, 0)::int as total_photo_assets,
                      coalesce(photo_assets.persisted_photo_assets, 0)::int as persisted_photo_assets,
                      (coalesce(photo_assets.total_photo_assets, 0)
                       - coalesce(photo_assets.persisted_photo_assets, 0))::int as pending_photo_assets,
                      case
                        when coalesce(photo_assets.total_photo_assets, 0) = 0 then null
                        when coalesce(photo_assets.persisted_photo_assets, 0) = 0 then 'not_started'
                        when photo_assets.persisted_photo_assets = photo_assets.total_photo_assets then 'complete'
                        else 'partial'
                      end as photo_backup_status,
                      engagement.observed_at as engagement_observed_at,
                      engagement.view_count, engagement.like_count, engagement.comment_count,
                      engagement.repost_count, engagement.share_count, engagement.save_count
               from archived_posts post
               left join lateral (
                 select count(*)::int as total_photo_assets,
                        count(object.id)::int as persisted_photo_assets
                 from archived_post_media_assets asset
                 left join stored_objects object on object.id=asset.stored_object_id
                    and object.deleted_at is null
                 where asset.archived_post_id=post.id and asset.asset_type='photo'
               ) photo_assets on true
               left join lateral (
                 select observed_at, view_count, like_count, comment_count,
                        repost_count, share_count, save_count
                 from archived_post_engagement_snapshots
                 where archived_post_id=post.id
                 order by observed_at desc, id desc
                 limit 1
               ) engagement on true
               where post.archived_profile_id=$1
               order by post.published_at desc nulls last, post.first_archived_at desc, post.id desc
               limit $2 offset $3""",
            profile_id, limit, offset,
        )

    async def get_owned_archived_profile_exists(self, user_id, profile_id):
        """Return the profile only through its tenant ownership boundary."""
        return await self.pool.fetchrow(
            "select id from archived_profiles where id=$1 and user_id=$2", profile_id, user_id,
        )

    async def insert_archived_posts_if_missing(self, user_id, archived_profile_id, posts):
        """Index scanner posts while refreshing only safe scanner asset hints.

        Existing scalar post metadata remains untouched. Scanner-provided asset
        candidates are different: they are the authoritative ordered acquisition
        hints for a photo/carousel and may be refreshed without replacing an
        attached ``stored_object_id``.
        """
        inserted = set()
        async with self.pool.acquire() as con:
            async with con.transaction():
                owned = await con.fetchval(
                    "select id from archived_profiles where id=$1::uuid and user_id=$2",
                    archived_profile_id, user_id,
                )
                if owned is None:
                    raise PermissionError("Archived profile not found")
                for post in posts:
                    row = await con.fetchrow(
                        """insert into archived_posts
                             (archived_profile_id,platform_post_id,original_url,media_type,caption,
                              published_at,thumbnail_url,is_present_on_original,metadata)
                           values($1::uuid,$2,$3,$4,$5,$6,$7,true,$8)
                           on conflict(archived_profile_id,platform_post_id) do nothing
                           returning id,platform_post_id""",
                        archived_profile_id, post.platform_post_id, post.original_url,
                        post.media_type, post.caption, post.published_at, post.thumbnail_url,
                        post.metadata,
                    )
                    if row is None:
                        post_id = await con.fetchval(
                            """select id from archived_posts
                               where archived_profile_id=$1::uuid and platform_post_id=$2""",
                            archived_profile_id, post.platform_post_id,
                        )
                    else:
                        post_id = row["id"]
                        inserted.add(row["platform_post_id"])
                    for asset in post.assets:
                        await con.execute(
                            """insert into archived_post_media_assets
                                 (archived_post_id,position,asset_type,source_url,thumbnail_url,
                                  duration_seconds,width,height,metadata)
                               values($1,$2,$3,$4,$5,$6,$7,$8,$9)
                               on conflict(archived_post_id,position) do update set
                                 asset_type=excluded.asset_type, source_url=excluded.source_url,
                                 thumbnail_url=excluded.thumbnail_url,
                                 duration_seconds=excluded.duration_seconds, width=excluded.width,
                                 height=excluded.height, metadata=excluded.metadata
                               where archived_post_media_assets.stored_object_id is null""",
                            post_id, asset.position, asset.asset_type, asset.source_url,
                            asset.thumbnail_url, asset.duration_seconds, asset.width,
                            asset.height, asset.metadata,
                        )
        return inserted

    async def create_owned_profile_full_sync_job(self, user_id, chat_id, profile_id, *, queue_name):
        """Atomically queue one tenant-owned full-history metadata sync."""
        async with self.pool.acquire() as con:
            async with con.transaction():
                profile = await con.fetchrow(
                    """select id,platform from archived_profiles
                       where id=$1::uuid and user_id=$2 for update""",
                    profile_id, user_id,
                )
                if profile is None or profile["platform"] != "tiktok":
                    return None
                await con.execute(
                    "select pg_advisory_xact_lock(hashtextextended($1,0))",
                    f"profile-full-sync:{user_id}:{profile_id}",
                )
                active = await con.fetchrow(
                    """select id from jobs where user_id=$1 and job_type='full_sync_profile'
                       and status in ('queued','running')
                       and input->>'profile_id'=$2::uuid::text
                       order by created_at limit 1 for update""",
                    user_id, profile_id,
                )
                if active is not None:
                    return active["id"], False
                job = await con.fetchrow(
                    """insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel)
                       values($1,$2,'full_sync_profile',
                              jsonb_build_object('profile_id',$3::uuid),'mini_app')
                       returning id""",
                    user_id, chat_id, profile_id,
                )
                await con.fetchval(
                    "select * from pgmq.send($1,jsonb_build_object('version',1,'job_id',$2::text),0)",
                    queue_name, str(job["id"]),
                )
                return job["id"], True

    async def get_owned_profile_full_sync(self, user_id, job_id):
        return await self.pool.fetchrow(
            """select id,status,progress,result,error_code,input->'checkpoint' as checkpoint
               from jobs where id=$1::uuid and user_id=$2 and job_type='full_sync_profile'""",
            job_id, user_id,
        )

    async def update_profile_full_sync_checkpoint(self, job_id, checkpoint, progress):
        """Persist private resume state plus coarse browser-safe progress."""
        await self.pool.execute(
            """update jobs
               set input=jsonb_set(input,'{checkpoint}',$2::jsonb,true), progress=$3
               where id=$1::uuid and job_type='full_sync_profile' and status='running'""",
            job_id, checkpoint, progress,
        )

    async def create_owned_profile_sync_job(self, user_id, chat_id, profile_id, *, queue_name):
        async with self.pool.acquire() as con:
            async with con.transaction():
                profile = await con.fetchrow("""select id,platform,username,platform_account_id from archived_profiles
                    where id=$1::uuid and user_id=$2 for update""", profile_id, user_id)
                if profile is None or profile["platform"] != "tiktok":
                    return None
                await con.execute("select pg_advisory_xact_lock(hashtextextended($1,0))", f"profile-sync:{user_id}:{profile_id}")
                active = await con.fetchrow("""select id from jobs where user_id=$1 and job_type='sync_profile'
                    and status in ('queued','running') and input->>'profile_id'=$2::uuid::text limit 1 for update""", user_id, profile_id)
                if active:
                    return active["id"], False
                job = await con.fetchrow("""insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel)
                    values($1,$2,'sync_profile',jsonb_build_object('profile_id',$3::uuid),'mini_app') returning id""", user_id, chat_id, profile_id)
                await con.fetchval("select * from pgmq.send($1,jsonb_build_object('version',1,'job_id',$2::text),0)", queue_name, str(job["id"]))
                return job["id"], True

    async def get_owned_profile_sync(self, user_id, job_id):
        return await self.pool.fetchrow("""select id,status,progress,result,error_code from jobs
            where id=$1::uuid and user_id=$2 and job_type='sync_profile'""", job_id, user_id)

    async def get_owned_profile_sync_target(self, user_id, profile_id):
        return await self.pool.fetchrow("""select id,username,platform_account_id from archived_profiles
            where id=$1::uuid and user_id=$2 and platform='tiktok'""", profile_id, user_id)

    async def reconcile_archived_profile_presence(self, user_id, profile_id, current_post_ids):
        """Atomically mark only actual present/removed transitions; preserve all assets."""
        async with self.pool.acquire() as con:
            async with con.transaction():
                rows = await con.fetch("""update archived_posts post set is_present_on_original=
                    post.platform_post_id = any($3::text[]), last_observed_at=now()
                    from archived_profiles profile where profile.id=post.archived_profile_id
                    and profile.id=$1::uuid and profile.user_id=$2
                    and post.is_present_on_original is distinct from (post.platform_post_id = any($3::text[]))
                    returning post.is_present_on_original""", profile_id, user_id, current_post_ids)
        restored = sum(1 for row in rows if row["is_present_on_original"])
        return len(rows) - restored, restored

    async def create_owned_profile_media_job(self, user_id, chat_id, profile_id, post_id=None, *, queue_name=None):
        """Create one active owned media job, coalescing duplicate requests.

        A bulk job covers its profile's unpersisted media assets, so a targeted
        request joins an active bulk job rather than initiating a duplicate
        acquisition. The transaction-scoped lock makes that decision atomic.
        """
        # A profile-wide lock also serializes targeted requests against a bulk
        # job, which is the only way to make their overlapping asset sets safe.
        lock_key = f"profile-media:{user_id}:{profile_id}"
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute("select pg_advisory_xact_lock(hashtextextended($1, 0))", lock_key)
                row = await con.fetchrow(
                    """with requested_media as (
                         select $3::uuid as profile_id, $4::uuid as post_id
                       ), owned_request as (
                         select requested_media.profile_id, requested_media.post_id
                         from requested_media
                         where exists(select 1 from archived_profiles profile
                                      where profile.id=requested_media.profile_id and profile.user_id=$1)
                           and (requested_media.post_id is null or exists(select 1 from archived_posts post
                               join archived_post_media_assets asset on asset.archived_post_id=post.id
                               where post.id=requested_media.post_id
                                 and post.archived_profile_id=requested_media.profile_id
                                 and asset.asset_type in ('video','photo')))
                       ), active_job as (
                         select job.id
                         from jobs job join owned_request requested on true
                         where job.user_id=$1 and job.job_type='archive_profile_media'
                           and job.status in ('queued','running')
                           and job.input->>'profile_id'=requested.profile_id::text
                           and (
                             (requested.post_id is null and not (job.input ? 'post_id'))
                             or (requested.post_id is not null and (
                               not (job.input ? 'post_id')
                               or job.input->>'post_id'=requested.post_id::text
                             ))
                           )
                         order by job.created_at
                         limit 1
                       ), created_job as (
                         insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel)
                         select $1,$2,'archive_profile_media',
                                jsonb_strip_nulls(jsonb_build_object(
                                  'profile_id', requested.profile_id,
                                  'post_id', requested.post_id
                                )),
                                'mini_app'
                         from owned_request requested
                         where not exists(select 1 from active_job)
                         returning id
                       )
                       select id, true as created from created_job
                       union all
                       select id, false as created from active_job
                       limit 1""",
                    user_id, chat_id, profile_id, post_id,
                )
                if row and row["created"] and queue_name:
                    # Keep the job row and its PGMQ message atomic. If queueing
                    # fails, the transaction rolls back instead of leaving an
                    # active job that a later duplicate request would coalesce.
                    await con.fetchval(
                        "select * from pgmq.send($1, jsonb_build_object('version',1,'job_id',$2::text), 0)",
                        queue_name, str(row["id"]),
                    )
        return ProfileMediaJob(row["id"], row["created"]) if row else None

    async def create_owned_profile_photo_delivery_job(
        self, user_id, chat_id, profile_id, post_id, *, queue_name=None,
    ):
        """Atomically queue delivery of one complete owned carousel to its owner.

        This first delivery slice deliberately accepts only complete 1--10 image
        sets. The browser never supplies a Telegram chat or storage identifier;
        both are derived from the verified identity and owned archive rows.
        """
        lock_key = f"profile-photo-delivery:{user_id}:{profile_id}:{post_id}"
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute("select pg_advisory_xact_lock(hashtextextended($1, 0))", lock_key)
                row = await con.fetchrow(
                    """with owned_post as (
                         select post.id
                         from archived_posts post
                         join archived_profiles profile on profile.id=post.archived_profile_id
                         where profile.id=$3::uuid and profile.user_id=$1 and post.id=$4::uuid
                           and post.media_type in ('photo','carousel')
                       ), complete_carousel as (
                         select owned_post.id, count(asset.id)::integer as total,
                                count(object.id)::integer as available
                         from owned_post
                         join archived_post_media_assets asset
                           on asset.archived_post_id=owned_post.id and asset.asset_type='photo'
                         left join stored_objects object
                           on object.id=asset.stored_object_id and object.deleted_at is null
                         group by owned_post.id
                         having count(asset.id) between 1 and 10 and count(asset.id)=count(object.id)
                       ), active_job as (
                         select job.id
                         from jobs job join complete_carousel target on true
                         where job.user_id=$1 and job.job_type='deliver_profile_photos'
                           and job.status in ('queued','running')
                           and job.input->>'profile_id'=$3::uuid::text
                           and job.input->>'post_id'=$4::uuid::text
                         order by job.created_at limit 1 for update
                       ), created_job as (
                         insert into jobs(user_id,telegram_chat_id,job_type,input,source_channel)
                         select $1,$2,'deliver_profile_photos',
                                jsonb_build_object('profile_id',$3::uuid,'post_id',$4::uuid,
                                                   'asset_count',target.total),
                                'mini_app'
                         from complete_carousel target
                         where not exists(select 1 from active_job)
                         returning id
                       )
                       select id, true as created from created_job
                       union all
                       select id, false as created from active_job
                       limit 1""",
                    user_id, chat_id, profile_id, post_id,
                )
                if row and row["created"] and queue_name:
                    await con.fetchval(
                        "select * from pgmq.send($1, jsonb_build_object('version',1,'job_id',$2::text),0)",
                        queue_name, str(row["id"]),
                    )
        return ProfileMediaJob(row["id"], row["created"]) if row else None

    async def list_owned_complete_profile_photo_delivery_assets(self, user_id, profile_id, post_id):
        """Worker-only ordered R2 targets for a complete owner delivery.

        Acquisition URLs, object IDs, and hashes do not leave this repository
        boundary. An empty result is deliberately ambiguous for foreign,
        missing, incomplete, and unavailable archive state.
        """
        rows = await self.pool.fetch(
            """select asset.position, object.storage_key, object.size_bytes, object.content_type
               from archived_posts post
               join archived_profiles profile on profile.id=post.archived_profile_id
               join archived_post_media_assets asset
                 on asset.archived_post_id=post.id and asset.asset_type='photo'
               join stored_objects object on object.id=asset.stored_object_id and object.deleted_at is null
               where profile.id=$1::uuid and profile.user_id=$2 and post.id=$3::uuid
                 and post.media_type in ('photo','carousel')
               order by asset.position asc""",
            profile_id, user_id, post_id,
        )
        if not rows or len(rows) > 10:
            return []
        positions = [row["position"] for row in rows]
        if len(set(positions)) != len(positions):
            return []
        return rows

    async def list_owned_archived_video_assets(self, user_id, profile_id, post_id=None):
        """Worker acquisition candidates, always rechecked against tenant ownership."""
        return await self.pool.fetch(
            """select asset.id, asset.asset_type, post.original_url
               from archived_post_media_assets asset
               join archived_posts post on post.id=asset.archived_post_id
               join archived_profiles profile on profile.id=post.archived_profile_id
               where profile.id=$1 and profile.user_id=$2 and profile.platform='tiktok'
                 and post.media_type='video' and asset.asset_type='video'
                 and asset.stored_object_id is null
                 and ($3::uuid is null or post.id=$3)
               order by post.published_at desc nulls last, post.id, asset.position""",
            profile_id, user_id, post_id,
        )

    async def list_owned_archived_media_assets(self, user_id, profile_id, post_id=None):
        """Return unpersisted internal worker candidates for owned video/photo assets.

        ``source_url`` and fallback candidates are intentionally selected only at
        this worker repository boundary; no browser projection calls this query.
        """
        return await self.pool.fetch(
            """select asset.id, asset.asset_type, asset.source_url, asset.metadata,
                      post.original_url, post.id as post_id
               from archived_post_media_assets asset
               join archived_posts post on post.id=asset.archived_post_id
               join archived_profiles profile on profile.id=post.archived_profile_id
               where profile.id=$1 and profile.user_id=$2 and profile.platform='tiktok'
                 and asset.asset_type in ('video','photo')
                 and asset.stored_object_id is null
                 and ($3::uuid is null or post.id=$3)
               order by post.published_at desc nulls last, post.id, asset.position""",
            profile_id, user_id, post_id,
        )

    async def attach_owned_archived_post_media_object(self, user_id, profile_id, asset_id, object_id):
        """Attach storage only when asset, post, profile, and tenant all match."""
        return await self.pool.fetchval(
            """update archived_post_media_assets asset set stored_object_id=$4
               from archived_posts post join archived_profiles profile on profile.id=post.archived_profile_id
               where asset.id=$3 and asset.archived_post_id=post.id and profile.id=$1
                 and profile.user_id=$2 and (asset.stored_object_id is null or asset.stored_object_id=$4)
               returning asset.id""",
            profile_id, user_id, asset_id, object_id,
        )

    async def get_owned_archived_post_playback(self, user_id, profile_id, post_id):
        """Return storage key only after an owned profile/post relationship is proven."""
        return await self.pool.fetchrow(
            """select profile.platform, post.platform_post_id, post.media_type,
                      post.thumbnail_url, object.storage_key, object.content_type
               from archived_posts post
               join archived_profiles profile on profile.id=post.archived_profile_id
               left join archived_post_media_assets asset on asset.archived_post_id=post.id
                   and asset.asset_type='video'
               left join stored_objects object on object.id=asset.stored_object_id and object.deleted_at is null
               where profile.id=$1 and profile.user_id=$2 and post.id=$3
               order by asset.position nulls last limit 1""",
            profile_id, user_id, post_id,
        )

    async def list_owned_archived_post_photo_assets(self, user_id, profile_id, post_id, *, limit=50, offset=0):
        """Return safe, ordered photo asset state only for one tenant-owned post.

        This deliberately omits source/fallback URLs, object IDs, hashes, and
        storage keys. ``None`` uses the same foreign-or-missing boundary as the
        rest of the profile archive API; an empty list is an owned non-photo post.
        """
        owned = await self.pool.fetchval(
            """select post.id
               from archived_posts post
               join archived_profiles profile on profile.id=post.archived_profile_id
               where profile.id=$1::uuid and profile.user_id=$2 and post.id=$3::uuid""",
            profile_id, user_id, post_id,
        )
        if owned is None:
            return None
        return await self.pool.fetch(
            """select asset.position,
                      case
                        when asset.stored_object_id is null then 'pending'
                        when object.id is null then 'unavailable'
                        else 'available'
                      end as state,
                      object.content_type
               from archived_post_media_assets asset
               left join stored_objects object on object.id=asset.stored_object_id
                  and object.deleted_at is null
               where asset.archived_post_id=$1::uuid and asset.asset_type='photo'
               order by asset.position asc
               limit $2 offset $3""",
            post_id, limit, offset,
        )

    async def get_owned_archived_post_photo_asset(self, user_id, profile_id, post_id, position):
        """Return an authorized internal signing target for one photo position.

        A missing position is represented safely for an owned post. Foreign and
        nonexistent posts return ``None`` so callers cannot infer ownership.
        """
        return await self.pool.fetchrow(
            """select case
                        when asset.id is null then 'missing'
                        when asset.stored_object_id is null then 'pending'
                        when object.id is null then 'unavailable'
                        else 'available'
                      end as state,
                      object.storage_key, object.content_type
               from archived_posts post
               join archived_profiles profile on profile.id=post.archived_profile_id
               left join archived_post_media_assets asset on asset.archived_post_id=post.id
                    and asset.asset_type='photo' and asset.position=$4::integer
               left join stored_objects object on object.id=asset.stored_object_id
                    and object.deleted_at is null
               where profile.id=$1::uuid and profile.user_id=$2 and post.id=$3::uuid""",
            profile_id, user_id, post_id, position,
        )

    async def update_asset_storage(self, media_item_id, position, *, size_bytes, sha256, storage_provider=None, storage_key=None):
        await self.pool.execute(
            """update media_assets
               set size_bytes=$3, sha256=$4, storage_provider=$5::text, storage_key=$6,
                   archived_at=case when $5::text is null then archived_at else now() end
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
                    where other.stored_object_id=mine.stored_object_id and ae2.deleted_at is null and ae2.id<>$1)
                    and not exists(select 1 from archived_post_media_assets profile_asset
                      join archived_posts profile_post on profile_post.id=profile_asset.archived_post_id
                      join archived_profiles profile on profile.id=profile_post.archived_profile_id
                      where profile_asset.stored_object_id=so.id)""",entry_id)

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
                where other.stored_object_id=mine.stored_object_id and ae2.deleted_at is null)
            and not exists(select 1 from archived_post_media_assets profile_asset
              join archived_posts profile_post on profile_post.id=profile_asset.archived_post_id
              join archived_profiles profile on profile.id=profile_post.archived_profile_id
              where profile_asset.stored_object_id=so.id)""", entry_id)

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
            """with candidates as (
                 select mea.stored_object_id from archive_entries entry
                 join archive_entry_assets mea on mea.archive_entry_id=entry.id where entry.user_id=$1
                 union
                 select asset.stored_object_id from archived_post_media_assets asset
                 join archived_posts post on post.id=asset.archived_post_id
                 join archived_profiles profile on profile.id=post.archived_profile_id
                 where profile.user_id=$1 and asset.stored_object_id is not null
               ) select distinct so.id,so.storage_key from candidates
               join stored_objects so on so.id=candidates.stored_object_id
               where so.deleted_at is null
                 and not exists(select 1 from archive_entry_assets other
                   join archive_entries live on live.id=other.archive_entry_id
                   where other.stored_object_id=so.id and live.deleted_at is null and live.user_id<>$1)
                 and not exists(select 1 from archived_post_media_assets other
                   join archived_posts post on post.id=other.archived_post_id
                   join archived_profiles profile on profile.id=post.archived_profile_id
                   where other.stored_object_id=so.id and profile.user_id<>$1)""", user_id
        )

    async def finalize_account_deletion(self, user_id):
        """Delete the tenant root only after worker-side object cleanup succeeds."""
        return await self.pool.fetchval(
            """delete from app_users where id=$1 and deletion_requested_at is not null returning id""",
            user_id,
        )
