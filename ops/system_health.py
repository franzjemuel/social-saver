import asyncio, json
from core.config import settings
from core.database import Database
from core.health import system_health

async def main():
    db=Database(settings.database_url); await db.connect()
    print(json.dumps(await system_health(db.pool,settings.queue_name),indent=2,default=str))
    await db.close()
if __name__=="__main__": asyncio.run(main())
