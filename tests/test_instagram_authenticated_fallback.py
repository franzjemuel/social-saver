from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from instagrapi.exceptions import ChallengeRequired, LoginRequired, PleaseWaitFewMinutes
from providers.base import SourceUnavailable
from providers.instagram import provider, session

URL = 'https://www.instagram.com/reel/ABC/'


def media():
    return SimpleNamespace(resources=[], media_type=2, video_url='https://example.com/video.mp4',
        product_type='clips', caption_text='caption', pk=123, user=SimpleNamespace(username='owner'),
        taken_at=datetime.now(timezone.utc), code='ABC')


@pytest.mark.asyncio
async def test_public_success_does_not_access_session(monkeypatch):
    p = provider.InstagramProvider(object())
    result = object()
    monkeypatch.setattr(p, '_resolve_sync', Mock(return_value=result))
    manager = Mock(side_effect=AssertionError('session must not be accessed'))
    monkeypatch.setattr(provider, 'InstagramSessionManager', manager)
    assert await p.resolve(URL) is result


@pytest.mark.asyncio
async def test_public_failure_uses_session_and_preserves_media_contract(monkeypatch):
    pool = object()
    p = provider.InstagramProvider(pool)
    monkeypatch.setattr(p, '_resolve_sync', Mock(side_effect=SourceUnavailable('public failure')))
    client = Mock()
    client.media_pk_from_url.return_value = '123'
    client.media_info_v1.return_value = media()
    manager = SimpleNamespace(client=AsyncMock(return_value=client), record_failure=AsyncMock())
    factory = Mock(return_value=manager)
    monkeypatch.setattr(provider, 'InstagramSessionManager', factory)
    result = await p.resolve(URL + '?tracking=removed')
    factory.assert_called_once_with(pool)
    client.media_info_v1.assert_called_once_with('123')
    assert result.canonical_url == URL
    assert result.media_type == 'reel'
    assert result.assets[0].asset_type == 'video'
    assert result.strategy == 'instagrapi_authenticated_v1'
    manager.record_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_pool_preserves_public_failure(monkeypatch):
    p = provider.InstagramProvider()
    monkeypatch.setattr(p, '_resolve_sync', Mock(side_effect=SourceUnavailable('public failure')))
    with pytest.raises(SourceUnavailable, match='public failure'):
        await p.resolve(URL)


@pytest.mark.asyncio
async def test_authenticated_failure_is_sanitized_and_recorded(monkeypatch):
    p = provider.InstagramProvider(object())
    monkeypatch.setattr(p, '_resolve_sync', Mock(side_effect=SourceUnavailable('public failure')))
    client = Mock()
    error = ChallengeRequired('sensitive upstream details')
    client.media_info_v1.side_effect = error
    manager = SimpleNamespace(client=AsyncMock(return_value=client), record_failure=AsyncMock())
    monkeypatch.setattr(provider, 'InstagramSessionManager', Mock(return_value=manager))
    with pytest.raises(SourceUnavailable) as caught:
        await p.resolve(URL)
    assert 'sensitive' not in str(caught.value)
    manager.record_failure.assert_awaited_once_with(error)


def manager_for(monkeypatch, status='needs_login', failures=0, cooldown=None):
    row = {'id': 'session-id', 'status': status, 'consecutive_failures': failures,
           'cooldown_until': cooldown}
    store = SimpleNamespace(get_or_create=AsyncMock(return_value=row), usable=AsyncMock(return_value=None),
                            save_healthy=AsyncMock(), mark=AsyncMock())
    monkeypatch.setattr(session, 'ProviderSessionStore', Mock(return_value=store))
    monkeypatch.setattr(session, 'settings', SimpleNamespace(session_master_key='unused',
                        instagram_session_username='test', instagram_session_password='not-real'))
    client = Mock()
    client.get_settings.return_value = {'fake': 'session'}
    monkeypatch.setattr(session, 'Client', Mock(return_value=client))
    return session.InstagramSessionManager(object()), store, client


@pytest.mark.asyncio
async def test_new_session_can_login_once(monkeypatch):
    manager, store, client = manager_for(monkeypatch)
    assert await manager.client() is client
    client.login.assert_called_once()
    store.save_healthy.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,failures,cooldown', [
    ('challenge', 1, None), ('disabled', 0, None), ('needs_login', 1, None),
    ('cooldown', 1, datetime.now(timezone.utc) + timedelta(hours=1)),
])
async def test_blocked_sessions_do_not_retry_login(monkeypatch, status, failures, cooldown):
    manager, store, client = manager_for(monkeypatch, status, failures, cooldown)
    assert await manager.client() is None
    client.login.assert_not_called()
    store.usable.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('error,status,code,minutes', [
    (ChallengeRequired(), 'challenge', 'CHALLENGE_REQUIRED', None),
    (LoginRequired(), 'needs_login', 'LOGIN_REQUIRED', None),
    (PleaseWaitFewMinutes(), 'cooldown', 'RATE_OR_FEEDBACK_BLOCK', 60),
])
async def test_failed_requests_persist_stop_state(monkeypatch, error, status, code, minutes):
    manager, store, _ = manager_for(monkeypatch)
    await manager.record_failure(error)
    args = ('session-id', status, code) + (() if minutes is None else (minutes,))
    store.mark.assert_awaited_once_with(*args)
