def parse_admin_telegram_ids(value: str) -> frozenset[int]:
    ids = set()
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            continue
        if not token.isdigit() or int(token) <= 0:
            raise ValueError("ADMIN_TELEGRAM_USER_IDS must contain positive numeric IDs")
        ids.add(int(token))
    return frozenset(ids)
