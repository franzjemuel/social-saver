from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException, Response

import apps.api.main as api


PROFILE = UUID("11111111-1111-1111-1111-111111111111")
POST = UUID("22222222-2222-2222-2222-222222222222")


class PhotoRepo:
    def __init__(self, *, owned=True):
        self.owned = owned
        self.list_calls = []
        self.asset_calls = []

    async def list_owned_archived_post_photo_assets(self, user_id, profile_id, post_id, *, limit, offset):
        self.list_calls.append((user_id, profile_id, post_id, limit, offset))
        if not self.owned:
            return None
        return [
            {"position": 0, "state": "available", "content_type": "image/jpeg"},
            {"position": 1, "state": "pending", "content_type": None},
            {"position": 2, "state": "unavailable", "content_type": None},
        ]

    async def get_owned_archived_post_photo_asset(self, user_id, profile_id, post_id, position):
        self.asset_calls.append((user_id, profile_id, post_id, position))
        if not self.owned:
            return None
        return {
            0: {"state": "available", "storage_key": "private/photo", "content_type": "image/jpeg"},
            1: {"state": "pending", "storage_key": None, "content_type": None},
            2: {"state": "unavailable", "storage_key": None, "content_type": None},
            3: {"state": "missing", "storage_key": None, "content_type": None},
        }[position]


@pytest.mark.asyncio
async def test_photo_asset_list_is_owned_ordered_safe_and_private(monkeypatch):
    repo = PhotoRepo()
    monkeypatch.setattr(api, "_repo", repo)
    response = Response()

    body = await api.profile_archive_photo_assets(
        str(PROFILE), str(POST), response, limit=50, offset=0,
        identity={"app_user_id": "tenant"},
    )

    assert repo.list_calls == [("tenant", PROFILE, POST, 50, 0)]
    assert [item.position for item in body.items] == [0, 1, 2]
    assert [item.state for item in body.items] == ["available", "pending", "unavailable"]
    assert response.headers["cache-control"] == "private, no-store"
    assert "source_url" not in body.model_dump_json()
    assert "storage_key" not in body.model_dump_json()


@pytest.mark.asyncio
async def test_photo_asset_list_is_bounded(monkeypatch):
    repo = PhotoRepo()
    monkeypatch.setattr(api, "_repo", repo)
    response = await api.profile_archive_photo_assets(
        str(PROFILE), str(POST), Response(), limit=2, offset=4,
        identity={"app_user_id": "tenant"},
    )
    assert repo.list_calls == [("tenant", PROFILE, POST, 2, 4)]
    assert (response.limit, response.offset) == (2, 4)


@pytest.mark.asyncio
async def test_foreign_and_missing_photo_assets_share_not_found(monkeypatch):
    monkeypatch.setattr(api, "_repo", PhotoRepo(owned=False))

    for profile_id, post_id in ((str(PROFILE), str(POST)), (str(UUID(int=3)), str(UUID(int=4)))):
        with pytest.raises(HTTPException) as error:
            await api.profile_archive_photo_assets(
                profile_id, post_id, Response(), limit=50, offset=0,
                identity={"app_user_id": "other"},
            )
        assert (error.value.status_code, error.value.detail) == (404, "profile_archive_not_found")


@pytest.mark.asyncio
async def test_photo_playback_signs_only_available_owned_asset_and_never_leaks_key(monkeypatch):
    repo = PhotoRepo()
    signed = []

    class Storage:
        async def presigned_get(self, key, expiry):
            signed.append((key, expiry))
            return "https://signed.invalid/ephemeral"

    monkeypatch.setattr(api, "_repo", repo)
    monkeypatch.setattr(api, "_r2", Storage())
    response = Response()

    body = await api.profile_archive_photo_asset_playback(
        str(PROFILE), str(POST), response=response, position=0, identity={"app_user_id": "tenant"},
    )

    assert body.available is True and body.state == "available"
    assert body.content_type == "image/jpeg"
    assert signed == [("private/photo", api.settings.archive_presign_seconds)]
    assert response.headers["cache-control"] == "private, no-store"
    assert "private/photo" not in body.model_dump_json()


@pytest.mark.asyncio
async def test_photo_playback_reports_pending_unavailable_and_missing_without_signing(monkeypatch):
    repo = PhotoRepo()

    class Storage:
        async def presigned_get(self, *_):
            raise AssertionError("pending assets must not be signed")

    monkeypatch.setattr(api, "_repo", repo)
    monkeypatch.setattr(api, "_r2", Storage())
    for position, state in ((1, "pending"), (2, "unavailable"), (3, "missing")):
        body = await api.profile_archive_photo_asset_playback(
            str(PROFILE), str(POST), response=Response(), position=position,
            identity={"app_user_id": "tenant"},
        )
        assert body.available is False and body.state == state


@pytest.mark.asyncio
async def test_photo_playback_foreign_and_missing_share_not_found(monkeypatch):
    monkeypatch.setattr(api, "_repo", PhotoRepo(owned=False))
    monkeypatch.setattr(api, "_r2", SimpleNamespace())
    with pytest.raises(HTTPException) as error:
        await api.profile_archive_photo_asset_playback(
            str(PROFILE), str(POST), response=Response(), position=0,
            identity={"app_user_id": "other"},
        )
    assert (error.value.status_code, error.value.detail) == (404, "profile_archive_not_found")


def test_photo_backup_status_is_derived_from_asset_counts_not_media_type():
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    base = {
        "id": POST, "platform_post_id": "post", "original_url": "https://example.invalid/post",
        "media_type": "carousel", "caption": None, "published_at": None, "thumbnail_url": None,
        "is_present_on_original": True, "first_archived_at": now, "last_observed_at": now,
        "has_archived_media": True, "engagement_observed_at": None,
        "view_count": None, "like_count": None, "comment_count": None, "repost_count": None,
        "share_count": None, "save_count": None,
    }
    expected = ((0, 0, 0, "not_started"), (4, 0, 4, "not_started"), (4, 1, 3, "partial"), (4, 4, 0, "complete"))
    for total, persisted, pending, status in expected:
        row = {**base, "total_photo_assets": total, "persisted_photo_assets": persisted,
               "pending_photo_assets": pending, "photo_backup_status": status if total else None}
        projected = api._profile_archive_post(row)
        assert (projected.total_photo_assets, projected.persisted_photo_assets,
                projected.pending_photo_assets) == (total, persisted, pending)
        assert projected.photo_backup_status == (status if total else None)


def test_photo_asset_routes_bound_collection_and_position_input():
    paths = api.app.openapi()["paths"]
    listing = paths["/v1/profile-archives/{profile_id}/posts/{post_id}/photo-assets"]["get"]
    playback = paths[
        "/v1/profile-archives/{profile_id}/posts/{post_id}/photo-assets/{position}/playback"
    ]["post"]
    list_params = {item["name"]: item["schema"] for item in listing["parameters"]}
    playback_params = {item["name"]: item["schema"] for item in playback["parameters"]}
    assert list_params["limit"]["maximum"] == 100
    assert list_params["offset"]["minimum"] == 0
    assert playback_params["position"] == {"type": "integer", "maximum": 9999, "minimum": 0, "title": "Position"}
