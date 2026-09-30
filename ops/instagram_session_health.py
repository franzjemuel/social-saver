"""Worker-only session check; --recover permits one operator-directed login."""
import argparse
import asyncio

from core.config import settings
from core.database import Database
from providers.instagram.session import InstagramSessionManager


async def main(recover=False):
    db = Database(settings.database_url)
    await db.connect()
    try:
        manager = InstagramSessionManager(db.pool)
        cl = await manager.client(recover=recover)
        if not cl:
            print("Instagram session unavailable; check its status and complete manual verification.")
            return 1
        try:
            await asyncio.to_thread(cl.account_info)
        except Exception as exc:
            await manager.record_failure(exc)
            print("Instagram session validation failed; operator attention required.")
            return 1
        print("Instagram session validated successfully.")
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recover", action="store_true",
                        help="one login attempt after manual verification; active cooldowns remain enforced")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(recover=args.recover)))
