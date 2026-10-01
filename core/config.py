from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    telegram_bot_token: str
    database_url: str
    app_env: str = "development"
    log_level: str = "INFO"
    port: int = 8080
    api_cors_origins: str = ""
    queue_name: str = "media_jobs"
    queue_visibility_seconds: int = 60
    queue_max_attempts: int = 3
    queue_heartbeat_seconds: int = 20
    queue_long_job_visibility_seconds: int = 300
    telegram_hosted_upload_limit_bytes: int = 49_000_000
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_presign_seconds: int = 3600
    archive_presign_seconds: int = 900
    session_master_key: str | None = None
    instagram_session_username: str | None = None
    instagram_session_password: str | None = None
    apify_api_token: str | None = None
    apify_stories_actor_id: str = "mmsVe3IhljF36qhot"
    apify_max_total_charge_usd: float = 0.05
    apify_story_limit: int = 10
    admin_telegram_user_ids: str = ""
    upstash_redis_rest_url: str | None = None
    upstash_redis_rest_token: str | None = None
    sentry_dsn: str | None = None
    sentry_environment: str = "development"
    provider_instagram_concurrency: int = 2
    live_max_concurrent_recordings: int = 1
    live_segment_seconds: int = 60
    live_hard_max_minutes: int = 120
    live_work_dir: str = "/tmp/social-saver-live"
    live_allow_manual_source: bool = False
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
