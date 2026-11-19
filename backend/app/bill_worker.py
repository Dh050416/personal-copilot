"""Independent durable bill worker: python -m app.bill_worker."""
import asyncio
import sys

from app.config import Settings
from app.data.db import Database
from app.logging_config import setup_logging
from app.services.bill_imports import run_worker


async def main():
    setup_logging()
    settings = Settings()
    db = Database(settings.database_url)
    try:
        await db.initialize()
        await run_worker(db, settings)
    finally:
        await db.close()


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
