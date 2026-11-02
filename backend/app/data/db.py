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
            # 多用户隔离迁移：给存量表补 user_id 列（新表已由 create_all 建好含 user_id）。
            # 存量数据无归属用户，无法安全归属到某个账号，故清空（部署时按需从零开始）。
            for table in ('bills', 'tracking_cards', 'schedule_events', 'import_batches',
                          'agent_memory', 'idempotency_records'):
                if not await self._has_column(conn, table, 'user_id'):
                    await conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN user_id INTEGER')
                    if table in ('bills', 'tracking_cards', 'schedule_events', 'import_batches'):
                        await conn.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)')
            # agent_memory / idempotency_records 主键从单列 key 升级为 (user_id, key)，
            # SQLite 无法就地改主键，且存量数据需清空，直接重建这两张表保证结构一致。
            for table in ('agent_memory', 'idempotency_records'):
                if not await self._is_composite_pk(conn, table):
                    await conn.exec_driver_sql(f'DROP TABLE {table}')
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
                # 泳道（看板栏目）：仅用户明确指定时写入；NULL = 沿用分类/用途推导
                ('lane', 'VARCHAR(32)'),
            ):
                if not await self._has_column(conn, 'tracking_cards', column):
                    await conn.exec_driver_sql(f'ALTER TABLE tracking_cards ADD COLUMN {column} {definition}')

    @staticmethod
    async def _has_column(conn, table, column):
        result = await conn.exec_driver_sql(f'PRAGMA table_info({table})')
        return any(row[1] == column for row in result.fetchall())

    @staticmethod
    async def _is_composite_pk(conn, table):
        """判断表是否已有复合主键（含 user_id 与 key 两列主键）。"""
        result = await conn.exec_driver_sql(f'PRAGMA table_info({table})')
        pk_columns = [row[1] for row in result.fetchall() if row[5] > 0]
        return 'user_id' in pk_columns and 'key' in pk_columns and len(pk_columns) >= 2

    async def close(self):
        await self.engine.dispose()
