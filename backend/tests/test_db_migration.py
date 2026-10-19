import sqlite3

import pytest
from sqlalchemy import select

from app.data.db import Database
from app.data.models import Bill


@pytest.mark.asyncio
async def test_existing_bill_table_migrates_category_to_transaction_type(tmp_path):
    path = tmp_path / 'legacy.db'
    connection = sqlite3.connect(path)
    connection.executescript(
        """CREATE TABLE bills (
            id INTEGER PRIMARY KEY,
            tx_no VARCHAR(128) NOT NULL,
            tx_time VARCHAR(32) NOT NULL,
            merchant VARCHAR(255) NOT NULL,
            product VARCHAR(500) NOT NULL,
            amount_cents INTEGER NOT NULL,
            direction VARCHAR(16) NOT NULL,
            category VARCHAR(64) NOT NULL,
            source VARCHAR(32) NOT NULL,
            import_batch_id VARCHAR(64) NOT NULL,
            source_sheet VARCHAR(128) NOT NULL,
            source_row INTEGER NOT NULL
        );
        INSERT INTO bills VALUES
            (1, 'legacy-1', '2026-09-01T10:00:00', '店铺', '/', 100, 'expense', '商户消费', 'xlsx', 'b', 'Sheet1', 19);
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    await database.initialize()
    async with database.sessions() as session:
        bill = await session.scalar(select(Bill).where(Bill.id == 1))
        assert bill.transaction_type == '商户消费'
        assert bill.category == '其他'
    await database.close()

