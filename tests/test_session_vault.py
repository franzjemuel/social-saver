from cryptography.fernet import Fernet
from core.session_vault import SessionVault
def test_session_settings_encrypt_roundtrip():
    v=SessionVault(Fernet.generate_key().decode())
    original={"uuids":{"device_id":"android-test"},"authorization_data":{"sessionid":"secret"}}
    ciphertext=v.seal(original)
    assert b"secret" not in ciphertext
    assert v.open(ciphertext)==original
