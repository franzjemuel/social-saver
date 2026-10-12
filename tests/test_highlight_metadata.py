from datetime import UTC, datetime

import pytest

from core.repository import HighlightSnapshotValidationError, _validate_highlight_snapshot
from providers.base import ArchivedHighlightCollectionSnapshot, ArchivedHighlightStoryMembership


def _membership(item_id="story-1", position=0):
    return ArchivedHighlightStoryMembership(
        provider_namespace="apify:tiktok-highlights",
        provider_item_id=item_id,
        provider_position=position,
        media_type="video",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_seconds=4.0,
        has_media_descriptor=True,
        has_cover_descriptor=True,
    )


def _snapshot(*memberships, complete=True, reported_count=None, verified=False):
    return ArchivedHighlightCollectionSnapshot(
        provider_namespace="apify:tiktok-highlights",
        provider_collection_id="provider-collection-1",
        title="Observed collection",
        reported_item_count=len(memberships) if reported_count is None else reported_count,
        has_cover_descriptor=True,
        observation_complete=complete,
        memberships=tuple(memberships),
        upstream_completeness_verified=verified,
    )


def test_valid_snapshot_accepts_empty_collection_and_preserves_provider_positions():
    _validate_highlight_snapshot(_snapshot())
    _validate_highlight_snapshot(_snapshot(_membership("later", 9), _membership("earlier", 2)))


@pytest.mark.parametrize(
    "snapshot",
    [
        _snapshot(_membership("duplicate", 0), _membership("duplicate", 1)),
        _snapshot(_membership("one", 0), _membership("two", 0)),
        ArchivedHighlightCollectionSnapshot(
            provider_namespace="apify:tiktok-highlights",
            provider_collection_id="provider-collection-1",
            title=None,
            reported_item_count=-1,
            has_cover_descriptor=False,
            observation_complete=False,
            memberships=(),
        ),
        _snapshot(ArchivedHighlightStoryMembership(
            provider_namespace="other:provider",
            provider_item_id="wrong-namespace",
            provider_position=0,
            media_type="video",
        )),
    ],
)
def test_snapshot_rejects_unsafe_duplicate_or_malformed_memberships(snapshot):
    with pytest.raises(HighlightSnapshotValidationError):
        _validate_highlight_snapshot(snapshot)


def test_snapshot_rejects_untyped_snapshot_memberships():
    with pytest.raises(HighlightSnapshotValidationError):
        _validate_highlight_snapshot(object())

    with pytest.raises(HighlightSnapshotValidationError):
        _validate_highlight_snapshot(_snapshot(object()))


def test_snapshot_keeps_provider_response_and_upstream_verification_distinct():
    snapshot = _snapshot(_membership(), complete=True, verified=False)
    _validate_highlight_snapshot(snapshot)
    assert snapshot.observation_complete is True
    assert snapshot.upstream_completeness_verified is False
