from core.payments import PaymentService, SUBSCRIPTION_PERIOD
def test_period_is_30_days(): assert SUBSCRIPTION_PERIOD==2592000
def test_payload_round_trip():
    p=PaymentService(None)
    uid="00000000-0000-0000-0000-000000000000"
    assert p.parse_payload(p.payload(uid,"plus"))==(uid,"plus")
