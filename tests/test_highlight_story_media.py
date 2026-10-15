from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

import apps.api.main as api
from apps.worker.main import complete_highlight_story_media_failure
from apps.worker.main import staging_highlight_canary_provider
from apps.worker.processors.highlight_story_media import (
    HighlightProviderExecutionPolicy,
    TikTokHighlightStoryMediaFailure,
    process_archive_highlight_story_media,
)
from apps.worker.processors.highlight_canary_execution import HighlightCanaryExecutionPolicy
from core.highlight_media import HighlightStoryMediaService
from core.media_download import DownloadedAsset
from providers.base import HighlightStoryMediaDescriptor, SourceUnavailable
from providers.tiktok.apify_highlight_story_media_provider import (
    ApifyHighlightStoryMediaProvider,
    PROVIDER_NAMESPACE,
)
from providers.tiktok.apify_highlights import HighlightStory, HighlightStoryPage
from providers.tiktok.apify_highlight_canary import ApifyHighlightCanaryMediaProvider


PROFILE = UUID("11111111-1111-1111-1111-111111111111")
MEMBERSHIP = UUID("22222222-2222-2222-2222-222222222222")
ASSET = UUID("33333333-3333-3333-3333-333333333333")


class Repo:
    def __init__(self):
        self.seeded = []
        self.attached = []
        self.failures = []
        self.pending = [{"id": ASSET, "position": 0, "asset_type": "video"}]

    async def get_owned_highlight_story_media_target(self, *_):
        return {"username": "owner", "provider_namespace": "synthetic", "provider_collection_id": "c", "provider_item_id": "story", "media_type": "video"}

    async def upsert_owned_highlight_story_media_assets(self, _, __, ___, descriptors):
        self.seeded = list(descriptors)
        return {"inserted": len(descriptors), "refreshed": 0}

    async def list_owned_highlight_story_media_assets(self, *_):
        return list(self.pending)

    async def record_owned_highlight_story_media_failure(self, *args):
        self.failures.append(args)

    async def update_job_progress(self, *_):
        pass


class Provider:
    async def resolve_story_media(self, **_):
        return (HighlightStoryMediaDescriptor(0, "video", ("https://ephemeral.invalid/video",)),)


class Downloader:
    async def download(self, descriptor, destination):
        assert descriptor.candidates == ("https://ephemeral.invalid/video",)
        destination.write_bytes(b"media")
        return DownloadedAsset(destination, 5, "a" * 64, "video/mp4")


class Service:
    async def persist(self, **kwargs):
        assert kwargs["asset_id"] == ASSET
        return False


class LedgerRepo(Repo):
    def __init__(self):
        super().__init__()
        self.ledger = []

    async def initialize_owned_highlight_provider_budget(self, *args):
        self.ledger.append(("budget", args)); return {"id": "budget"}

    async def reserve_owned_highlight_provider_invocation(self, *args, **kwargs):
        self.ledger.append(("reserve", args, kwargs)); return {"id": "invocation"}

    async def claim_owned_highlight_provider_invocation(self, *args):
        self.ledger.append(("claim", args)); return {"id": "invocation", "status": "started"}

    async def finish_owned_highlight_provider_invocation(self, *args, **kwargs):
        self.ledger.append(("finish", args, kwargs)); return {"id": "invocation", "status": kwargs["status"]}


@pytest.mark.asyncio
async def test_synthetic_story_media_pipeline_keeps_descriptors_in_memory(tmp_path):
    repo = Repo()
    result = await process_archive_highlight_story_media(
        {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
        repo, Provider(), Downloader(), Service(), execution_enabled=True,
    )
    assert result == {"assets_discovered": 1, "assets_seeded": 1, "attached": 1, "uploaded": 1, "reused": 0, "failures": 0, "pending_after": 0, "outcome": "complete"}
    assert repo.seeded[0].candidates == ("https://ephemeral.invalid/video",)
    assert not repo.failures


@pytest.mark.asyncio
async def test_budgeted_descriptor_resolution_claims_once_then_persists_private_asset():
    repo = LedgerRepo()
    policy = HighlightProviderExecutionPolicy("synthetic", 10, 10)
    result = await process_archive_highlight_story_media(
        {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
        repo, Provider(), Downloader(), Service(), execution_enabled=True,
        provider_execution_policy=policy,
    )
    assert result["attached"] == 1 and result["outcome"] == "complete"
    assert [entry[0] for entry in repo.ledger] == ["budget", "reserve", "claim", "finish"]
    assert repo.ledger[-1][-1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_budgeted_ambiguous_provider_failure_is_terminal_and_reservation_is_not_reused():
    class AmbiguousProvider:
        async def resolve_story_media(self, **_):
            raise SourceUnavailable("private provider response")
    repo = LedgerRepo()
    with pytest.raises(TikTokHighlightStoryMediaFailure) as error:
        await process_archive_highlight_story_media(
            {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
            repo, AmbiguousProvider(), execution_enabled=True,
            provider_execution_policy=HighlightProviderExecutionPolicy("synthetic", 10, 10),
        )
    assert error.value.code == "highlight_media_provider_indeterminate"
    assert repo.ledger[-1][-1] == {"status": "indeterminate", "error_code": "provider_ambiguous_failure"}


@pytest.mark.asyncio
async def test_budgeted_known_provider_failure_is_terminal_without_descriptor_or_asset_mutation():
    from providers.base import MediaNotFound
    class MissingProvider:
        async def resolve_story_media(self, **_):
            raise MediaNotFound("private source")
    repo = LedgerRepo()
    with pytest.raises(TikTokHighlightStoryMediaFailure) as error:
        await process_archive_highlight_story_media(
            {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
            repo, MissingProvider(), execution_enabled=True,
            provider_execution_policy=HighlightProviderExecutionPolicy("synthetic", 10, 10),
        )
    assert error.value.code == "highlight_media_provider_failed"
    assert repo.seeded == []
    assert repo.ledger[-1][-1] == {"status": "failed_known", "error_code": "provider_known_failure"}


@pytest.mark.asyncio
async def test_dispatched_provider_with_ambiguous_receipt_write_is_terminal_before_media_persistence():
    class ReceiptRepo(LedgerRepo):
        async def finish_owned_highlight_provider_invocation(self, *args, **kwargs):
            self.ledger.append(("finish", args, kwargs)); return None
    repo = ReceiptRepo()
    with pytest.raises(TikTokHighlightStoryMediaFailure) as error:
        await process_archive_highlight_story_media(
            {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
            repo, Provider(), Downloader(), Service(), execution_enabled=True,
            provider_execution_policy=HighlightProviderExecutionPolicy("synthetic", 10, 10),
        )
    assert error.value.code == "highlight_media_provider_receipt_unavailable"
    assert repo.seeded == []


@pytest.mark.asyncio
async def test_synthetic_apify_story_to_private_object_pipeline_keeps_descriptor_out_of_persistence(tmp_path):
    """One Story's descriptor crosses only the in-memory worker boundary."""
    class POCClient:
        async def stories(self, collection_id, *, posts_per_page, expected_item_count, live_authorized):
            assert collection_id == "collection" and posts_per_page == 30
            assert expected_item_count is None and live_authorized is False
            return HighlightStoryPage((HighlightStory(
                "story", "collection", 0, "video", None, 2.0,
                ("https://ephemeral.invalid/video",),
            ),), 1, 0, posts_per_page, False)

    object_id = UUID("55555555-5555-5555-5555-555555555555")
    class ArchiveRepo(LedgerRepo):
        async def get_owned_highlight_story_media_target(self, *_):
            return {
                "username": "owner", "provider_namespace": PROVIDER_NAMESPACE,
                "provider_collection_id": "collection", "provider_item_id": "story", "media_type": "video",
            }
        async def get_stored_object_by_sha(self, _): return None
        async def create_stored_object(self, sha, key, size, content_type):
            assert sha == "a" * 64 and size == 5 and content_type == "video/mp4"
            assert key.startswith("archive/aa/")
            return object_id
        async def attach_owned_highlight_story_media_object(self, user_id, profile_id, asset_id, saved_object_id):
            assert (user_id, profile_id, asset_id, saved_object_id) == ("tenant", str(PROFILE), ASSET, object_id)
            self.attached.append(saved_object_id)
            return asset_id

    class Storage:
        async def put_file(self, path, key, content_type):
            assert path.read_bytes() == b"media" and key.startswith("archive/")
            assert content_type == "video/mp4"

    errors = []
    class DiagnosticService(HighlightStoryMediaService):
        async def persist(self, **kwargs):
            try:
                return await super().persist(**kwargs)
            except Exception as exc:
                errors.append(exc)
                raise

    repo = ArchiveRepo()
    result = await process_archive_highlight_story_media(
        {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
        repo,
        ApifyHighlightStoryMediaProvider(POCClient()),
        Downloader(),
        DiagnosticService(repo, Storage()),
        execution_enabled=True,
        provider_execution_policy=HighlightProviderExecutionPolicy(PROVIDER_NAMESPACE, 10, 10),
    )
    assert not errors
    assert result["attached"] == result["uploaded"] == 1, result
    assert repo.attached == [object_id]
    assert repo.seeded[0].candidates == ("https://ephemeral.invalid/video",)
    # Repository calls receive only normalized asset shape, never candidates.
    assert "ephemeral.invalid" not in repr(repo.ledger)


@pytest.mark.asyncio
async def test_live_canary_bridge_runs_two_receipted_operations_then_persists_one_video(tmp_path):
    """Synthetic Actor responses exercise the exact staged paid-provider seam."""
    class CanaryRepo(Repo):
        def __init__(self):
            super().__init__()
            self.operations = {}
            self.authorization = []

        async def get_owned_highlight_story_media_target(self, *_):
            return {
                "username": "owner", "provider_namespace": PROVIDER_NAMESPACE,
                "provider_collection_id": "collection", "provider_item_id": "story", "media_type": "video",
            }

        async def claim_owned_highlight_canary_authorization(self, *_):
            return {"budget_micro_usd": 650_000}

        async def complete_owned_highlight_canary_authorization(self, *_args, **kwargs):
            self.authorization.append(kwargs["status"])
            return {"status": kwargs["status"]}

        async def initialize_owned_highlight_provider_budget(self, *_):
            return {"id": "budget"}

        async def reserve_owned_highlight_provider_invocation(self, *_args, **kwargs):
            key = kwargs["execution_key"]
            if key not in self.operations:
                self.operations[key] = {"id": key, "status": "reserved", "provider_run_receipt": None}
            return self.operations[key]

        async def claim_owned_highlight_provider_invocation(self, *_args):
            row = self.operations[_args[-1]]
            if row["status"] != "reserved":
                return None
            row["status"] = "started"
            return row

        async def mark_owned_highlight_provider_invocation_dispatched(self, *_args):
            row = self.operations[_args[-2]]
            row.update(status="dispatched", provider_run_receipt=_args[-1])
            return row

        async def finish_owned_highlight_provider_invocation(self, *_args, **kwargs):
            row = self.operations[_args[-1]]
            row["status"] = kwargs["status"]
            if kwargs.get("receipt"):
                row["provider_run_receipt"] = kwargs["receipt"]
            return row

    class Actor:
        def __init__(self):
            self.started = []

        async def start_run(self, payload, *, maximum_micro_usd):
            self.started.append((payload, maximum_micro_usd))
            return f"run_{len(self.started)}"

        async def collect_run(self, receipt):
            if receipt == "run_1":
                return [{"unique_id": "owner", "highlight_id": "collection", "post_count": 1}]
            return [{
                "highlight_id": "collection", "type": "video", "video_id": "story",
                "video_url": "https://ephemeral.invalid/video", "duration": 2.0,
            }]

    repo = CanaryRepo()
    actor = Actor()
    result = await process_archive_highlight_story_media(
        {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
        repo,
        ApifyHighlightCanaryMediaProvider(actor),
        Downloader(),
        Service(),
        execution_enabled=True,
        provider_execution_policy=HighlightCanaryExecutionPolicy(PROVIDER_NAMESPACE),
    )
    assert result["attached"] == result["uploaded"] == 1
    assert result["outcome"] == "complete"
    assert repo.authorization == ["consumed"]
    assert [amount for _, amount in actor.started] == [100_000, 550_000]
    assert sorted(row["status"] for row in repo.operations.values()) == ["succeeded", "succeeded"]
    assert "ephemeral.invalid" not in repr(repo.operations)


@pytest.mark.asyncio
async def test_partial_story_failure_preserves_other_position_and_records_safe_code():
    repo = Repo()
    second = UUID("44444444-4444-4444-4444-444444444444")
    repo.pending = [
        {"id": ASSET, "position": 0, "asset_type": "photo"},
        {"id": second, "position": 1, "asset_type": "photo"},
    ]
    class Photos:
        async def resolve_story_media(self, **_):
            return (
                HighlightStoryMediaDescriptor(0, "photo", ("https://ephemeral.invalid/0",)),
                HighlightStoryMediaDescriptor(1, "photo", ("https://ephemeral.invalid/1",)),
            )
    class FailingDownloader:
        async def download(self, descriptor, destination):
            if descriptor.position == 1:
                raise SourceUnavailable("private provider URL")
            destination.write_bytes(b"image")
            return DownloadedAsset(destination, 5, "b" * 64, "image/jpeg")
    result = await process_archive_highlight_story_media(
        {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
        repo, Photos(), FailingDownloader(), Service(), execution_enabled=True,
    )
    assert result["attached"] == result["uploaded"] == 1
    assert result["failures"] == result["pending_after"] == 1
    assert result["outcome"] == "partial"
    assert repo.failures[-1][-1] == "acquisition_or_storage_failed"


@pytest.mark.asyncio
async def test_disabled_highlight_media_fails_before_provider_or_repository_work():
    class NeverCalled:
        def __getattr__(self, _):
            raise AssertionError("disabled job must not resolve a target or provider")

    with pytest.raises(TikTokHighlightStoryMediaFailure) as error:
        await process_archive_highlight_story_media(
            {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
            NeverCalled(), execution_enabled=False,
        )
    assert error.value.code == "highlight_media_feature_disabled"


@pytest.mark.asyncio
async def test_unregistered_highlight_provider_is_terminal_not_retryable():
    with pytest.raises(TikTokHighlightStoryMediaFailure) as error:
        await process_archive_highlight_story_media(
            {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
            Repo(), execution_enabled=True,
        )
    assert error.value.code == "highlight_media_provider_unavailable"


@pytest.mark.asyncio
async def test_paid_capable_provider_requires_explicit_execution_policy_before_any_resolution():
    class PaidCapableProvider:
        requires_execution_policy = True
        async def resolve_story_media(self, **_):
            raise AssertionError("provider must not be called without a ledger policy")
    with pytest.raises(TikTokHighlightStoryMediaFailure) as error:
        await process_archive_highlight_story_media(
            {"id": "job", "user_id": "tenant", "input": {"profile_id": str(PROFILE), "membership_id": str(MEMBERSHIP)}},
            Repo(), PaidCapableProvider(), execution_enabled=True,
        )
    assert error.value.code == "highlight_media_provider_policy_required"


@pytest.mark.asyncio
async def test_terminal_highlight_media_failure_archives_without_bot_notification():
    calls = []

    class FailureRepo:
        async def fail_job(self, *args):
            calls.append(("fail", args))

    class Queue:
        async def archive(self, message_id):
            calls.append(("archive", message_id))

    await complete_highlight_story_media_failure(
        FailureRepo(), Queue(), {"msg_id": 41}, "job",
        TikTokHighlightStoryMediaFailure("highlight_media_feature_disabled"),
    )
    assert calls == [
        ("fail", ("job", "HIGHLIGHT_MEDIA_FEATURE_DISABLED", "highlight_media_feature_disabled")),
        ("archive", 41),
    ]


@pytest.mark.asyncio
async def test_highlight_persistence_dedupes_and_uses_tenant_attachment(tmp_path):
    file = tmp_path / "video.mp4"; file.write_bytes(b"video")
    downloaded = DownloadedAsset(file, 5, "c" * 64, "video/mp4")
    calls = []
    class PersistenceRepo:
        async def get_stored_object_by_sha(self, _): return {"id": ASSET}
        async def attach_owned_highlight_story_media_object(self, *args): calls.append(args); return ASSET
    reused = await HighlightStoryMediaService(PersistenceRepo(), SimpleNamespace()).persist(
        user_id="tenant", profile_id=PROFILE, asset_id=ASSET, downloaded=downloaded,
    )
    assert reused and calls == [("tenant", PROFILE, ASSET, ASSET)]


@pytest.mark.asyncio
async def test_playback_is_tenant_scoped_and_gated_queue_does_not_enqueue(monkeypatch):
    class ApiRepo:
        async def get_owned_highlight_story_media_playback(self, *_):
            return {"asset_type": "video", "storage_key": "private/key"}
    class Storage:
        async def presigned_get(self, *_): return "https://signed.invalid/short"
    monkeypatch.setattr(api, "_repo", ApiRepo())
    monkeypatch.setattr(api, "_r2", Storage())
    response = await api.highlight_story_media_playback(
        str(PROFILE), str(MEMBERSHIP), 0, identity={"app_user_id": "tenant", "telegram_user_id": 1},
    )
    assert response.available and response.playback_url == "https://signed.invalid/short"
    monkeypatch.setattr(api.settings, "tiktok_highlights_media_enabled", False)
    with pytest.raises(HTTPException) as error:
        await api.archive_highlight_story_media(
            str(PROFILE), str(MEMBERSHIP), identity={"app_user_id": "tenant", "telegram_user_id": 1},
        )
    assert error.value.status_code == 404


def test_highlight_schema_never_persists_descriptor_columns():
    migration = Path("supabase/migrations/025_highlight_story_media.sql").read_text()
    assert "source_url" not in migration and "descriptor" in migration


def test_worker_never_registers_live_highlight_provider_while_any_server_gate_is_disabled(monkeypatch):
    monkeypatch.setattr(api.settings, "app_env", "staging")
    monkeypatch.setattr(api.settings, "tiktok_highlights_media_enabled", True)
    monkeypatch.setattr(api.settings, "tiktok_highlights_live_provider_enabled", True)
    monkeypatch.setattr(api.settings, "tiktok_highlights_canary_enabled", True)
    monkeypatch.setattr(api.settings, "tiktok_highlights_canary_operator_enabled", False)
    monkeypatch.setattr(api.settings, "apify_tiktok_highlights_token", "worker-only-token")
    assert staging_highlight_canary_provider() == (None, None)
