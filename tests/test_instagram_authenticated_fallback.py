from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from instagrapi.exceptions import (
    BadCredentials, BadPassword, ChallengeRequired, LoginRequired,
    PleaseWaitFewMinutes, TwoFactorRequired, UnknownError,
)
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


def manager_for(monkeypatch, status='needs_login', failures=0, cooldown=None, saved=None):
    row = {'id': 'session-id', 'status': status, 'consecutive_failures': failures,
           'cooldown_until': cooldown, 'encrypted_settings': b'sealed' if saved else None}
    store = SimpleNamespace(get_or_create=AsyncMock(return_value=row), usable=AsyncMock(return_value=None),
                            settings_from_row=Mock(return_value=saved), save_settings=AsyncMock(),
                            save_healthy=AsyncMock(), mark=AsyncMock())
    monkeypatch.setattr(session, 'ProviderSessionStore', Mock(return_value=store))
    monkeypatch.setattr(session, 'settings', SimpleNamespace(session_master_key='unused',
                        instagram_session_username='test', instagram_session_password='not-real'))
    client = Mock()
    client.get_settings.return_value = saved or {'fake': 'session'}
    client.login.return_value = True
    client.user_id = 123
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
    (BadPassword(), 'needs_login', 'CREDENTIALS_REJECTED', None),
    (BadCredentials(), 'needs_login', 'CREDENTIALS_REJECTED', None),
    (TwoFactorRequired(), 'challenge', 'TWO_FACTOR_REQUIRED', None),
    (PleaseWaitFewMinutes(), 'cooldown', 'RATE_OR_FEEDBACK_BLOCK', 60),
])
async def test_failed_requests_persist_stop_state(monkeypatch, error, status, code, minutes):
    manager, store, _ = manager_for(monkeypatch)
    await manager.record_failure(error)
    args = ('session-id', status, code) + (() if minutes is None else (minutes,))
    store.mark.assert_awaited_once_with(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize('login_result,user_id', [(False, 123), (None, 123), (True, None)])
async def test_login_without_verified_authentication_is_not_healthy(monkeypatch, login_result, user_id):
    manager, store, client = manager_for(monkeypatch)
    client.login.return_value = login_result
    client.user_id = user_id
    assert await manager.client() is None
    store.save_healthy.assert_not_awaited()
    store.mark.assert_awaited_once_with('session-id', 'needs_login', 'LOGIN_REQUIRED')


@pytest.mark.asyncio
async def test_unknown_login_failure_requires_attention_and_preserves_identity(monkeypatch, capsys):
    manager, store, client = manager_for(monkeypatch)
    before = {'uuids': {'uuid': 'stable-device'}, 'cookies': {}}
    after = {'uuids': {'uuid': 'stable-device'}, 'cookies': {'csrftoken': 'private-cookie'}}
    client.get_settings.side_effect = [before, after]
    error = UnknownError('private-upstream-response', error_type='unrecognized-private-value')
    client.login.side_effect = error
    assert await manager.client() is None
    assert store.save_settings.await_args_list[0].args == ('session-id', before)
    assert store.save_settings.await_args_list[-1].args == ('session-id', after)
    store.save_healthy.assert_not_awaited()
    store.mark.assert_awaited_once_with('session-id', 'needs_login', 'LOGIN_FAILED')
    assert capsys.readouterr() == ('', '')


@pytest.mark.asyncio
async def test_identity_is_saved_before_login_can_fail(monkeypatch):
    manager, store, client = manager_for(monkeypatch)

    def login(*args, **kwargs):
        store.save_settings.assert_awaited_once_with('session-id', {'fake': 'session'})
        raise ChallengeRequired('private challenge response')

    client.login.side_effect = login
    assert await manager.client() is None
    store.save_healthy.assert_not_awaited()
    store.mark.assert_awaited_once_with('session-id', 'challenge', 'CHALLENGE_REQUIRED')


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['challenge', 'needs_login'])
async def test_explicit_recovery_reuses_saved_device_identity(monkeypatch, status):
    saved = {'uuids': {'uuid': 'stable-device'}, 'cookies': {'csrftoken': 'private-cookie'}}
    manager, store, client = manager_for(monkeypatch, status=status, failures=1, saved=saved)

    def login(*args, **kwargs):
        client.set_settings.assert_called_once_with(saved)
        return True

    client.login.side_effect = login
    assert await manager.client(recover=True) is client
    store.settings_from_row.assert_called_once()
    store.save_healthy.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,cooldown', [
    ('disabled', None),
    ('cooldown', datetime.now(timezone.utc) + timedelta(hours=1)),
    ('needs_login', datetime.now(timezone.utc) + timedelta(hours=1)),
    ('challenge', datetime.now(timezone.utc) + timedelta(hours=1)),
])
async def test_explicit_recovery_does_not_override_disablement_or_active_cooldown(monkeypatch, status, cooldown):
    manager, store, client = manager_for(monkeypatch, status=status, failures=1, cooldown=cooldown)
    assert await manager.client(recover=True) is None
    client.login.assert_not_called()
    store.save_settings.assert_not_awaited()
    store.save_healthy.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_cooldown_restores_saved_identity_before_revalidation(monkeypatch):
    saved = {'uuids': {'uuid': 'stable-device'}, 'authorization_data': {'ds_user_id': '123'}}
    manager, store, client = manager_for(monkeypatch, status='cooldown', failures=1,
        cooldown=datetime.now(timezone.utc) - timedelta(minutes=1), saved=saved)
    def login(*args, **kwargs):
        client.set_settings.assert_called_once_with(saved)
        return True

    client.login.side_effect = login
    assert await manager.client() is client
    client.login.assert_called_once()


@pytest.mark.asyncio
async def test_recovery_callbacks_cannot_prompt_or_expose_codes(monkeypatch, capsys):
    manager, _, client = manager_for(monkeypatch)
    monkeypatch.setattr('builtins.input', Mock(side_effect=AssertionError('must not prompt')))
    assert await manager.client() is client
    for handler, args in [
        (client.challenge_code_handler, ('private-username', 'email')),
        (client.change_password_handler, ('private-username',)),
    ]:
        with pytest.raises(ChallengeRequired) as caught:
            handler(*args)
        assert 'private-username' not in str(caught.value)
    error = ChallengeRequired('private challenge response')
    with pytest.raises(ChallengeRequired) as caught:
        client.handle_exception(client, error)
    assert caught.value is error
    assert capsys.readouterr() == ('', '')


@pytest.mark.asyncio
async def test_authenticated_unknown_request_still_uses_bounded_cooldown(monkeypatch):
    manager, store, _ = manager_for(monkeypatch)
    await manager.record_failure(UnknownError('private upstream response'))
    store.mark.assert_awaited_once_with('session-id', 'cooldown', 'UnknownError', 30)


@pytest.mark.asyncio
async def test_healthy_saved_session_does_not_login_again(monkeypatch):
    saved = {'uuids': {'uuid': 'stable-device'}, 'authorization_data': {'ds_user_id': '123'}}
    manager, store, client = manager_for(monkeypatch, status='healthy', saved=saved)
    assert await manager.client() is client
    client.set_settings.assert_called_once_with(saved)
    client.login.assert_not_called()
    store.save_healthy.assert_not_awaited()
