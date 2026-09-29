import asyncio
from core.config import settings
from core.database import Database
from core.payments import PaymentService

async def main():
    db=Database(settings.database_url); await db.connect()
    rows=await PaymentService(db.pool).expire_due()
    print(f"Expired {len(rows)} subscription(s).")
    await db.close()

if __name__=="__main__": asyncio.run(main())
