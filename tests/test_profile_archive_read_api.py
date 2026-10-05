from datetime import datetime, timezone
import inspect
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

import apps.api.main as api
from core.repository import Repository


PROFILE_ID = UUID("11111111-1111-1111-1111-111111111111")
NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def profile_row():
    return {
        "id": PROFILE_ID, "platform": "tiktok", "platform_account_id": "7055967621082039297",
        "username": "aliachin11", "display_name": None, "bio": None, "avatar_url": None,
        "first_archived_at": NOW, "last_observed_at": NOW, "post_count": 2,
        "present_post_count": 1, "removed_post_count": 1, "latest_post_at": NOW,
        # These deliberately must not cross the explicit response projection.
        "metadata": {"sec_uid": "private"},
    }


def post_row(*, engagement=True, removed=False):
    row = {
        "id": UUID("22222222-2222-2222-2222-222222222222"), "platform_post_id": "7176363825556376859",
        "original_url": "https://www.tiktok.com/@aliachin11/video/7176363825556376859",
        "media_type": "video", "caption": "caption", "published_at": NOW,
        "thumbnail_url": "https://example.invalid/thumbnail.jpg", "is_present_on_original": not removed,
        "first_archived_at": NOW, "last_observed_at": NOW,
        "engagement_observed_at": NOW if engagement else None,
        "view_count": 281 if engagement else None, "like_count": 25 if engagement else None,
        "comment_count": 1 if engagement else None, "repost_count": 0 if engagement else None,
        "share_count": None, "save_count": None,
        "source_url": "https://example.invalid/private-media.mp4",
    }
    return row


class FakeProfileArchiveRepository:
    def __init__(self, *, owned=True, rows=None):
        self.owned = owned
        self.rows = rows if rows is not None else [post_row()]
        self.calls = []

    async def list_owned_archived_profiles(self, user_id, *, limit, offset):
        self.calls.append(("list", user_id, limit, offset))
        return [profile_row()]

    async def get_owned_archived_profile(self, user_id, profile_id):
        self.calls.append(("detail", user_id, profile_id))
        return profile_row() if self.owned else None

    async def list_owned_archived_profile_posts(self, user_id, profile_id, *, limit, offset):
        self.calls.append(("posts", user_id, profile_id, limit, offset))
        return self.rows if self.owned else None


@pytest.mark.asyncio
async def test_profile_list_is_tenant_scoped_and_supports_pagination(monkeypatch):
    repo = FakeProfileArchiveRepository()
    monkeypatch.setattr(api, "_repo", repo)

    response = await api.profile_archives(limit=10, offset=5, identity={"app_user_id": "tenant-a"})

    assert repo.calls == [("list", "tenant-a", 10, 5)]
    assert response.limit == 10 and response.offset == 5
    assert response.items[0].id == str(PROFILE_ID)
    assert "metadata" not in response.items[0].model_dump()


@pytest.mark.asyncio
async def test_foreign_and_missing_profile_use_the_same_404(monkeypatch):
    repo = FakeProfileArchiveRepository(owned=False)
    monkeypatch.setattr(api, "_repo", repo)

    for profile_id in (str(PROFILE_ID), "33333333-3333-3333-3333-333333333333"):
        with pytest.raises(HTTPException) as error:
            await api.profile_archive_detail(profile_id, identity={"app_user_id": "tenant-a"})
        assert (error.value.status_code, error.value.detail) == (404, "profile_archive_not_found")


@pytest.mark.asyncio
async def test_posts_require_owned_profile_and_return_safe_latest_engagement(monkeypatch):
    repo = FakeProfileArchiveRepository(rows=[post_row(removed=True), post_row(engagement=False)])
    monkeypatch.setattr(api, "_repo", repo)

    response = await api.profile_archive_posts(str(PROFILE_ID), limit=30, offset=0, identity={"app_user_id": "tenant-a"})

    assert repo.calls == [("posts", "tenant-a", PROFILE_ID, 30, 0)]
    assert response.items[0].is_present_on_original is False
    assert response.items[0].engagement.view_count == 281
    assert response.items[1].engagement is None
    assert "source_url" not in response.items[0].model_dump()


@pytest.mark.asyncio
async def test_foreign_profile_posts_use_the_same_404(monkeypatch):
    monkeypatch.setattr(api, "_repo", FakeProfileArchiveRepository(owned=False))

    with pytest.raises(HTTPException) as error:
        await api.profile_archive_posts(str(PROFILE_ID), identity={"app_user_id": "tenant-a"})
    assert (error.value.status_code, error.value.detail) == (404, "profile_archive_not_found")


def test_repository_queries_are_tenant_scoped_ordered_and_do_not_select_internal_metadata():
    listing = inspect.getsource(Repository.list_owned_archived_profiles).lower()
    detail = inspect.getsource(Repository.get_owned_archived_profile).lower()
    posts = inspect.getsource(Repository.list_owned_archived_profile_posts).lower()

    assert "profile.user_id=$1" in listing
    assert "profile.user_id=$1 and profile.id=$2" in detail
    assert "id=$1 and user_id=$2" in posts
    assert "order by post.published_at desc nulls last" in posts
    assert "order by observed_at desc" in posts
    assert "profile.metadata" not in listing + detail
    assert "source_url" not in posts


def test_existing_media_archive_routes_are_unchanged_and_profile_routes_require_telegram_identity():
    routes = {route.path: route for route in api.app.routes if isinstance(route, APIRoute)}
    assert "/v1/archive" in routes
    for path in ("/v1/profile-archives", "/v1/profile-archives/{profile_id}", "/v1/profile-archives/{profile_id}/posts"):
        assert api.current_identity in [dependency.call for dependency in routes[path].dependant.dependencies]
