import asyncio
from core.config import settings
from core.database import Database
from providers.instagram.session import InstagramSessionManager

async def main():
    db=Database(settings.database_url); await db.connect()
    cl=await InstagramSessionManager(db.pool).client()
    if not cl:
        print("Instagram authenticated session is unavailable or requires manual attention.")
    else:
        print(f"Instagram session healthy for @{cl.username}.")
    await db.close()

if __name__=="__main__": asyncio.run(main())
