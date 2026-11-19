"""Account-scoped bill identity and durable import review workflow."""
import sqlalchemy as sa

from alembic import op

revision = '45b230ab0021'
down_revision = '34a120ef0011'
branch_labels = depends_on = None


def upgrade():
    for name, kind, default in (
        ('account', sa.String(80), 'default'), ('nature', sa.String(32), 'unknown'),
        ('category_status', sa.String(24), 'pending'), ('category_reason', sa.Text(), ''),
        ('refund_cents', sa.BigInteger(), '0'),
    ):
        op.add_column('bills', sa.Column(name, kind, nullable=False, server_default=default))
    for constraint in sa.inspect(op.get_bind()).get_unique_constraints('bills'):
        if set(constraint['column_names']) == {'user_id', 'source', 'tx_no'}:
            op.drop_constraint(constraint['name'], 'bills', type_='unique')
    op.create_unique_constraint('uq_bill_identity', 'bills', ['user_id', 'source', 'account', 'tx_no'])
    op.execute("UPDATE bills SET category_status = CASE WHEN category_source IN ('user','manual','source','legacy') "
               "THEN 'confirmed' WHEN category_source IN ('llm','rule') THEN 'suggested' ELSE 'pending' END")
    op.execute("""UPDATE bills SET nature = CASE
        WHEN transaction_type LIKE '%退款%' OR transaction_type LIKE '%退还%' THEN 'refund'
        WHEN transaction_type LIKE '%零钱通%' OR transaction_type LIKE '%理财%' THEN 'internal'
        WHEN transaction_type LIKE '%提现%' THEN 'withdrawal'
        WHEN transaction_type LIKE '%充值%' THEN 'recharge'
        WHEN transaction_type LIKE '%红包%' THEN 'red_packet'
        WHEN transaction_type LIKE '%转账%' OR transaction_type LIKE '%群收款%'
            OR transaction_type LIKE '%二维码收款%' THEN 'transfer'
        WHEN direction = 'expense' AND (transaction_type IN ('商户消费','扫二维码付款','亲属卡交易')
            OR source = 'manual') THEN 'consumption' ELSE 'unknown' END""")
    op.execute("""UPDATE bills SET refund_cents = CASE
        WHEN transaction_status LIKE '%全额退款%' THEN amount_cents
        WHEN transaction_status ~ '已退款[（(]?[¥￥]?[0-9]+([.][0-9]{1,2})?'
        THEN LEAST(amount_cents, (substring(transaction_status FROM
            '已退款[（(]?[¥￥]?([0-9]+[.]?[0-9]*)')::numeric * 100)::bigint)
        ELSE 0 END WHERE nature = 'consumption'""")
    op.create_table('bill_import_jobs',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('account', sa.String(80), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('file_hash', sa.String(64), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('progress', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('owner', sa.String(32), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'account', 'file_hash', name='uq_bill_import_file'))
    op.create_index('ix_bill_import_jobs_user_id', 'bill_import_jobs', ['user_id'])
    op.create_index('ix_bill_import_jobs_status', 'bill_import_jobs', ['status'])
    op.create_table('bill_import_rows',
        sa.Column('job_id', sa.String(32), sa.ForeignKey('bill_import_jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('row_id', sa.Integer(), primary_key=True),
        sa.Column('data_json', sa.Text(), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('disposition', sa.String(24), nullable=False),
        sa.Column('excluded', sa.Boolean(), nullable=False))
    op.create_table('bill_category_rules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('platform', sa.String(32), nullable=False),
        sa.Column('account', sa.String(80), nullable=False),
        sa.Column('merchant', sa.String(255), nullable=False),
        sa.Column('product', sa.String(500), nullable=False),
        sa.Column('transaction_type', sa.String(64), nullable=False),
        sa.Column('category', sa.String(64), nullable=False),
        sa.UniqueConstraint('user_id', 'platform', 'account', 'merchant', 'product', 'transaction_type',
                            name='uq_bill_category_rule'))
    op.create_index('ix_bill_category_rules_user_id', 'bill_category_rules', ['user_id'])
    op.create_table('bill_refund_links',
        sa.Column('refund_bill_id', sa.Integer(), sa.ForeignKey('bills.id'), primary_key=True),
        sa.Column('original_bill_id', sa.Integer(), sa.ForeignKey('bills.id'), nullable=False),
        sa.Column('amount_cents', sa.BigInteger(), nullable=False),
        sa.Column('basis', sa.Text(), nullable=False))
    op.create_index('ix_bill_refund_links_original_bill_id', 'bill_refund_links', ['original_bill_id'])


def downgrade():
    # Restoring the old key can fail if separate accounts share transaction IDs.
    for table in ('bill_refund_links', 'bill_category_rules', 'bill_import_rows', 'bill_import_jobs'):
        op.drop_table(table)
    op.drop_constraint('uq_bill_identity', 'bills', type_='unique')
    op.create_unique_constraint('bills_user_id_source_tx_no_key', 'bills', ['user_id', 'source', 'tx_no'])
    for column in ('refund_cents', 'category_reason', 'category_status', 'nature', 'account'):
        op.drop_column('bills', column)
