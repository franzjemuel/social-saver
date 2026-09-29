import hashlib, hmac, json
from urllib.parse import urlencode
import pytest
from core.telegram_webapp_auth import verify_telegram_init_data, TelegramWebAppAuthError

TOKEN='123456:ABC-test-token'
NOW=1_800_000_000

def signed(**overrides):
    fields={
      'auth_date': str(NOW),
      'query_id': 'AAE-test',
      'user': json.dumps({'id': 424242, 'first_name':'Franz', 'username':'franz'}, separators=(',',':')),
    }
    fields.update(overrides)
    check='\n'.join(f'{k}={fields[k]}' for k in sorted(fields))
    secret=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
    fields['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(fields)

def test_valid_init_data():
    i=verify_telegram_init_data(signed(),TOKEN,now=NOW)
    assert i.telegram_user_id==424242 and i.username=='franz'

def test_tamper_rejected():
    payload=signed().replace('Franz','Frank')
    with pytest.raises(TelegramWebAppAuthError,match='signature'):
        verify_telegram_init_data(payload,TOKEN,now=NOW)

def test_stale_rejected():
    with pytest.raises(TelegramWebAppAuthError,match='stale'):
        verify_telegram_init_data(signed(auth_date=str(NOW-301)),TOKEN,now=NOW)

def test_future_rejected():
    with pytest.raises(TelegramWebAppAuthError,match='stale'):
        verify_telegram_init_data(signed(auth_date=str(NOW+31)),TOKEN,now=NOW)
