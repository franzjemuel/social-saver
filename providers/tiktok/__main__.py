import argparse
import asyncio
import os

from core.database import Database
from core.repository import Repository
from providers.tiktok.importer import TikTokProfileImporter
from providers.tiktok.provider import DEVELOPMENT_MAX_POSTS


async def _run(args) -> int:
    dsn = os.environ.get("DATABASE_URL")
    user_id = os.environ.get("SOCIAL_SAVER_ARCHIVE_USER_ID")
    if not dsn or not user_id:
        raise SystemExit("DATABASE_URL and SOCIAL_SAVER_ARCHIVE_USER_ID are required")
    database = Database(dsn)
    await database.connect()
    try:
        summary = await TikTokProfileImporter(Repository(database.pool)).import_profile(
            user_id, args.target, limit=args.limit,
        )
    finally:
        await database.close()
    print(f"account resolved: {summary.platform_account_id}")
    print(f"posts discovered/imported: {summary.posts_discovered}/{summary.posts_imported}")
    print(f"posts skipped: {summary.posts_skipped}")
    print(f"failures: {summary.failures}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import TikTok profile metadata into a private Social Saver archive")
    parser.add_argument("target", help="TikTok username (@name) or profile URL")
    parser.add_argument("--limit", type=int, default=DEVELOPMENT_MAX_POSTS)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
