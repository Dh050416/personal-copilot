from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.models import Base


class Database:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        database_path = Path(path).resolve().as_posix()
        self.engine = create_async_engine(f'sqlite+aiosqlite:///{database_path}')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.engine.sync_engine, 'connect')
        def pragmas(connection, _):
            cursor = connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.execute('PRAGMA busy_timeout=5000')
            cursor.close()

    async def initialize(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if not await self._has_column(conn, 'bills', 'transaction_type'):
                await conn.exec_driver_sql("ALTER TABLE bills ADD COLUMN transaction_type VARCHAR(64) NOT NULL DEFAULT '未知'")
                await conn.exec_driver_sql(
                    """UPDATE bills
                       SET transaction_type = CASE
                           WHEN category IN ('餐饮', '交通', '订阅', '购物', '其他') THEN '未知'
                           ELSE category
                       END,
                       category = CASE
                           WHEN category IN ('餐饮', '交通', '订阅', '购物', '其他') THEN category
                           ELSE '其他'
                       END"""
                )
            for column, definition in (
                ('payment_method', "VARCHAR(64) NOT NULL DEFAULT '未知'"),
                ('transaction_status', "VARCHAR(64) NOT NULL DEFAULT '未知'"),
                ('merchant_tx_no', "VARCHAR(128) NOT NULL DEFAULT ''"),
                ('note', "TEXT NOT NULL DEFAULT ''"),
                ('category_source', "VARCHAR(32) NOT NULL DEFAULT 'auto'"),
                ('category_confidence', "FLOAT NOT NULL DEFAULT 0.0"),
                ('category_checked_at', "VARCHAR(40) NOT NULL DEFAULT ''"),
            ):
                if not await self._has_column(conn, 'bills', column):
                    await conn.exec_driver_sql(f'ALTER TABLE bills ADD COLUMN {column} {definition}')
                    if column == 'category_source':
                        await conn.exec_driver_sql("UPDATE bills SET category_source='legacy' WHERE category <> '其他'")
            for column, definition in (
                ('direction', "VARCHAR(16) NOT NULL DEFAULT 'expense'"),
                ('transaction_type', "VARCHAR(64) NOT NULL DEFAULT '其他'"),
                ('counterparty', "VARCHAR(255) NOT NULL DEFAULT ''"),
                ('amount_cents', 'INTEGER'),
                ('occurred_at', "VARCHAR(32) NOT NULL DEFAULT ''"),
            ):
                if not await self._has_column(conn, 'tracking_cards', column):
                    await conn.exec_driver_sql(f'ALTER TABLE tracking_cards ADD COLUMN {column} {definition}')

    @staticmethod
    async def _has_column(conn, table, column):
        result = await conn.exec_driver_sql(f'PRAGMA table_info({table})')
        return any(row[1] == column for row in result.fetchall())

    async def close(self):
        await self.engine.dispose()
