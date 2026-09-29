import logging
from core.config import settings

def init_observability(service):
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if settings.sentry_dsn:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment,
            release="social-saver@1.4.0",
            traces_sample_rate=0.10,
            send_default_pii=False,
        )
        sentry_sdk.set_tag("service",service)

def capture_job_exception(exc,job):
    if not settings.sentry_dsn: return
    import sentry_sdk
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("job_type",job.get("job_type","unknown"))
        scope.set_context("job",{"id":str(job.get("id"))})
        sentry_sdk.capture_exception(exc)
