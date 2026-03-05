import asyncio
from app.db.database import spider_async_engine
from sqlalchemy import text

async def main():
    async with spider_async_engine.connect() as conn:
        res = await conn.execute(text("SELECT _created_at FROM news ORDER BY _id DESC LIMIT 5"))
        print("Latest timestamps from the Spider database (news table):")
        for r in res.fetchall():
            print(r[0])

asyncio.run(main())
