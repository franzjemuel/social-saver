import json
from cryptography.fernet import Fernet, InvalidToken

class SessionVault:
    def __init__(self,key: str | None):
        if not key: raise RuntimeError("SESSION_MASTER_KEY is required for authenticated provider sessions")
        self.fernet=Fernet(key.encode())

    def seal(self,settings: dict) -> bytes:
        return self.fernet.encrypt(json.dumps(settings,separators=(",",":")).encode())

    def open(self,ciphertext: bytes) -> dict:
        try:
            return json.loads(self.fernet.decrypt(bytes(ciphertext)).decode())
        except InvalidToken as exc:
            raise RuntimeError("Provider session ciphertext could not be decrypted") from exc
