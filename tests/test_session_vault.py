from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from core.provider_sessions import ProviderSessionStore
from cryptography.fernet import Fernet
from core.session_vault import SessionVault
def test_session_settings_encrypt_roundtrip():
    v=SessionVault(Fernet.generate_key().decode())
    original={"uuids":{"device_id":"android-test"},"authorization_data":{"sessionid":"secret"}}
    ciphertext=v.seal(original)
    assert b"secret" not in ciphertext
    assert v.open(ciphertext)==original


@pytest.mark.asyncio
async def test_checkpoint_encrypts_material_without_clearing_stop_state():
    pool = SimpleNamespace(execute=AsyncMock())
    store = ProviderSessionStore(pool, Fernet.generate_key().decode())
    settings = {'uuids': {'uuid': 'stable-device'}, 'cookies': {'sessionid': 'private-cookie'}}
    await store.save_settings('session-id', settings)
    pool.execute.assert_awaited_once()
    sql, session_id, encrypted = pool.execute.await_args.args
    assert session_id == 'session-id'
    assert b'private-cookie' not in encrypted
    assert store.settings_from_row({'encrypted_settings': encrypted}) == settings
    assert 'status=' not in sql.replace(' ', '')
    assert 'consecutive_failures' not in sql
    assert 'cooldown_until' not in sql
    assert 'last_error' not in sql


def test_empty_checkpoint_has_no_restorable_identity():
    store = ProviderSessionStore(object(), Fernet.generate_key().decode())
    assert store.settings_from_row({'encrypted_settings': None}) is None
