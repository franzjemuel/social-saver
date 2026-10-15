import json

import httpx
import pytest

from apps.worker.processors.highlight_canary_execution import (
    CANARY_DISCOVERY_RESERVATION_MICRO_USD,
    CANARY_STORY_RESERVATION_MICRO_USD,
    HighlightCanaryExecutionFailure,
    HighlightCanaryExecutionPolicy,
    HighlightCanaryExecutor,
)
from providers.tiktok.apify_highlight_canary import (
    ApifyAsyncHighlightActorClient,
    ApifyCanaryIndeterminate,
    ApifyCanaryKnownFailure,
    ApifyHighlightCanaryMediaProvider,
)
from providers.tiktok.apify_highlights import HighlightIncompleteResponse
from providers.tiktok.apify_highlights_provider import PROVIDER_NAMESPACE


COLLECTIONS = [
    {
        "unique_id": "example_user",
        "highlight_id": "collection-a",
        "title": "Example",
        "post_count": 1,
    }
]
STORIES = [
    {
        "highlight_id": "collection-a",
        "type": "video",
        "video_id": "story-a",
        "create_time": 1724980000,
        "duration": 4.2,
        "video_url": "https://descriptor.invalid/temporary.mp4",
    }
]


def _stories(count: int):
    return [
        {
            "highlight_id": "collection-a",
            "type": "video",
            "video_id": f"story-{index}",
            "create_time": 1724980000 - index,
            "duration": 4.2,
            "video_url": "https://descriptor.invalid/temporary.mp4",
        }
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_async_actor_start_and_collection_use_bounded_charge_and_sanitized_receipt(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.params["waitForFinish"] == "0"
            assert request.url.params["maxTotalChargeUsd"] == "0.100000"
            assert json.loads(request.content) == {"mode": "highlight_list", "uniqueIds": ["example_user"]}
            return httpx.Response(201, json={"data": {"id": "run_123"}})
        if request.url.path.endswith("/actor-runs/run_123"):
            return httpx.Response(200, json={"data": {"status": "SUCCEEDED", "defaultDatasetId": "dataset_123"}})
        assert request.url.path.endswith("/datasets/dataset_123/items")
        return httpx.Response(200, json=COLLECTIONS)

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    client = ApifyAsyncHighlightActorClient("private-token", actor_timeout_seconds=5, poll_seconds=0.1)
    receipt = await client.start_run({"mode": "highlight_list", "uniqueIds": ["example_user"]}, maximum_micro_usd=100_000)
    assert receipt == "run_123"
    assert await client.collect_run(receipt) == COLLECTIONS
    assert all("private-token" not in str(request.url) for request in requests)


@pytest.mark.asyncio
async def test_actor_timeout_and_malformed_receipt_are_sanitized_and_never_retried(monkeypatch):
    def handler(_request):
        raise httpx.ReadTimeout("https://secret.invalid/descriptor")

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    client = ApifyAsyncHighlightActorClient("private-token", actor_timeout_seconds=5, poll_seconds=0.1)
    with pytest.raises(ApifyCanaryIndeterminate) as error:
        await client.start_run({"mode": "highlight_list"}, maximum_micro_usd=100_000)
    assert "secret" not in str(error.value)
    with pytest.raises(ApifyCanaryKnownFailure):
        await client.collect_run("not a valid receipt")


class LedgerRepo:
    def __init__(self):
        self.invocations = {}
        self.reserved = 0
        self.start_calls = 0

    async def reserve_owned_highlight_provider_invocation(self, *_args, **kwargs):
        key = kwargs["execution_key"]
        existing = self.invocations.get(key)
        if existing is not None:
            return existing
        amount = kwargs["maximum_micro_usd"]
        if self.reserved + amount > 650_000:
            return None
        self.reserved += amount
        row = {"id": key, "status": "reserved", "provider_run_receipt": None}
        self.invocations[key] = row
        return row

    async def claim_owned_highlight_provider_invocation(self, *_args):
        key = _args[-1]
        row = self.invocations[key]
        if row["status"] != "reserved":
            return None
        row["status"] = "started"
        return row

    async def mark_owned_highlight_provider_invocation_dispatched(self, *_args):
        key, receipt = _args[-2:]
        row = self.invocations[key]
        if row["status"] != "started":
            return None
        row.update(status="dispatched", provider_run_receipt=receipt)
        return row

    async def finish_owned_highlight_provider_invocation(self, *_args, **kwargs):
        key = _args[-1]
        row = self.invocations[key]
        if row["status"] not in {"started", "dispatched"}:
            return None
        row["status"] = kwargs["status"]
        if kwargs.get("receipt") is not None:
            row["provider_run_receipt"] = kwargs["receipt"]
        return row


class FakeActor:
    def __init__(self):
        self.started = []
        self.collected = []

    async def start_run(self, payload, *, maximum_micro_usd):
        self.started.append((payload, maximum_micro_usd))
        return "run_" + str(len(self.started))

    async def collect_run(self, receipt):
        self.collected.append(receipt)
        return COLLECTIONS if receipt == "run_1" else STORIES


@pytest.mark.asyncio
async def test_two_operation_canary_reserves_cumulative_cap_and_redelivery_does_not_start_again():
    repo = LedgerRepo()
    executor = HighlightCanaryExecutor(
        repo,
        {"id": "job", "user_id": "tenant"},
        "profile",
        HighlightCanaryExecutionPolicy(PROVIDER_NAMESPACE),
    )
    actor = FakeActor()
    assert await executor.invoke("collection_list", {"mode": "highlight_list"}, actor) == COLLECTIONS
    assert await executor.invoke("collection_items", {"mode": "highlight_posts"}, actor) == STORIES
    assert repo.reserved == CANARY_DISCOVERY_RESERVATION_MICRO_USD + CANARY_STORY_RESERVATION_MICRO_USD == 650_000
    # Replaying the first work after a completed receipt only fetches that
    # receipt's dataset; it cannot open a replacement Actor run.
    assert await executor.invoke("collection_list", {"ignored": True}, actor) == COLLECTIONS
    assert len(actor.started) == 2
    assert len(actor.collected) == 3


@pytest.mark.asyncio
async def test_dispatch_timeout_consumes_single_use_slot_and_cannot_start_a_second_run():
    class TimeoutActor(FakeActor):
        async def start_run(self, *_args, **_kwargs):
            self.started.append("attempt")
            raise ApifyCanaryIndeterminate("private failure")

    repo = LedgerRepo()
    executor = HighlightCanaryExecutor(
        repo, {"id": "job", "user_id": "tenant"}, "profile",
        HighlightCanaryExecutionPolicy(PROVIDER_NAMESPACE),
    )
    actor = TimeoutActor()
    with pytest.raises(HighlightCanaryExecutionFailure) as error:
        await executor.invoke("collection_list", {"mode": "highlight_list"}, actor)
    assert error.value.code == "highlight_canary_dispatch_indeterminate"
    with pytest.raises(HighlightCanaryExecutionFailure) as repeated:
        await executor.invoke("collection_list", {"mode": "highlight_list"}, actor)
    assert repeated.value.code == "highlight_canary_invocation_not_reusable"
    assert actor.started == ["attempt"]


@pytest.mark.asyncio
async def test_provider_discovers_then_enumerates_exact_story_without_persisting_descriptors():
    class Executor:
        def __init__(self):
            self.operations = []

        async def invoke(self, operation, payload, _client):
            self.operations.append((operation, payload))
            return COLLECTIONS if operation == "collection_list" else STORIES

    provider = ApifyHighlightCanaryMediaProvider(FakeActor())
    executor = Executor()
    descriptors = await provider.resolve_story_media(
        username="example_user",
        provider_namespace=PROVIDER_NAMESPACE,
        provider_collection_id="collection-a",
        provider_item_id="story-a",
        execution=executor,
    )
    assert [(item.position, item.asset_type) for item in descriptors] == [(0, "video")]
    assert [name for name, _ in executor.operations] == ["collection_list", "collection_items"]
    assert "descriptor.invalid" not in repr(executor.operations)


@pytest.mark.asyncio
async def test_canary_provider_uses_one_bounded_request_for_complete_25_story_collection():
    collection = [{**COLLECTIONS[0], "post_count": 25}]
    stories = _stories(25)

    class Executor:
        def __init__(self):
            self.operations = []

        async def invoke(self, operation, payload, _client):
            self.operations.append((operation, payload))
            return collection if operation == "collection_list" else stories

    executor = Executor()
    provider = ApifyHighlightCanaryMediaProvider(FakeActor())
    descriptors = await provider.resolve_story_media(
        username="example_user", provider_namespace=PROVIDER_NAMESPACE,
        provider_collection_id="collection-a", provider_item_id="story-24", execution=executor,
    )
    assert descriptors[0].asset_type == "video"
    assert executor.operations[1][1]["postsPerPage"] == 30


@pytest.mark.asyncio
async def test_canary_provider_rejects_incomplete_or_oversized_collection_without_using_story_as_complete():
    class Executor:
        def __init__(self, collection_count, stories):
            self.collection_count = collection_count
            self.stories = stories
            self.operations = []

        async def invoke(self, operation, payload, _client):
            self.operations.append(operation)
            if operation == "collection_list":
                return [{**COLLECTIONS[0], "post_count": self.collection_count}]
            return self.stories

    provider = ApifyHighlightCanaryMediaProvider(FakeActor())
    incomplete = Executor(25, _stories(24))
    with pytest.raises(HighlightIncompleteResponse):
        await provider.resolve_story_media(
            username="example_user", provider_namespace=PROVIDER_NAMESPACE,
            provider_collection_id="collection-a", provider_item_id="story-0", execution=incomplete,
        )
    assert incomplete.operations == ["collection_list", "collection_items"]

    oversized = Executor(31, _stories(31))
    with pytest.raises(HighlightIncompleteResponse):
        await provider.resolve_story_media(
            username="example_user", provider_namespace=PROVIDER_NAMESPACE,
            provider_collection_id="collection-a", provider_item_id="story-0", execution=oversized,
        )
    # Fail before a bounded request could be incorrectly treated as complete.
    assert oversized.operations == ["collection_list"]
