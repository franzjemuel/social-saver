from core.payments import PaymentService
def test_payload_roundtrip_still_stable():
    p=PaymentService(None); uid="00000000-0000-0000-0000-000000000000"
    assert p.parse_payload(p.payload(uid,"pro"))==(uid,"pro")
