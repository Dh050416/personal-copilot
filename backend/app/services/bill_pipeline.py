"""Deterministic facts and pandas staging; original transaction facts are retained."""
import json
import re
from collections import Counter

import pandas as pd

from app.services.importer import parse_amount, parse_file

GENERIC = {'', '/', '商品', '扫码付款', '收款方备注:二维码收款', '转账备注:微信转账',
           '费用', '微信支付', '先用后付', '支付', '二维码收款'}


def transaction_facts(row):
    kind, status = row.get('transaction_type', ''), row.get('transaction_status', '')
    if '退款' in kind or '退还' in kind:
        nature = 'refund'
    elif any(x in kind for x in ('零钱通', '理财')):
        nature = 'internal'
    elif '提现' in kind:
        nature = 'withdrawal'
    elif '充值' in kind:
        nature = 'recharge'
    elif '红包' in kind:
        nature = 'red_packet'
    elif any(x in kind for x in ('转账', '群收款', '二维码收款')):
        nature = 'transfer'
    elif any(x in kind for x in ('商户消费', '扫二维码付款', '亲属卡交易')):
        nature = 'consumption' if row['direction'] == 'expense' else 'unknown'
    else:
        nature = 'unknown'
    refund = 0
    if nature == 'consumption':
        if '全额退款' in status:
            refund = row['amount_cents']
        else:
            match = re.search(r'已退款\s*[（(]?\s*[¥￥]?\s*([\d,.]+)', status)
            if match:
                refund = min(parse_amount(match[1]), row['amount_cents'])
    return {'nature': nature, 'refund_cents': refund}


def rule_key(row):
    return tuple(row.get(k, '') for k in ('source', 'account', 'merchant', 'product', 'transaction_type'))


def group_key(row):
    import hashlib
    return hashlib.sha256(json.dumps([*rule_key(row), row.get('direction', ''), row.get('nature', '')],
                                     ensure_ascii=False).encode()).hexdigest()[:24]


def stage_file(content, filename, account):
    parsed = parse_file(content, filename, source='generic')
    # Detect the platform from the document signature, not its filename or extension.
    if filename.lower().endswith('.xlsx'):
        import io

        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
        try:
            prefix = ' '.join(str(c.value or '') for sheet in book for row in sheet.iter_rows(max_row=18)
                              for c in row)
        finally:
            book.close()
    else:
        try:
            prefix = content[:32768].decode('utf-8-sig')
        except UnicodeDecodeError:
            prefix = content[:32768].decode('gb18030', errors='ignore')
    platform = 'wechat' if '微信支付账单' in prefix else 'alipay' if '支付宝' in prefix else 'generic'
    if not parsed['rows']:
        return [], parsed['errors'], platform
    frame = pd.DataFrame(parsed['rows'])
    for column in ('merchant', 'product', 'note'):
        frame[column] = frame[column].fillna('').astype(str).str.strip()
    frame['source'], frame['account'] = platform, account
    frame['row_id'] = range(1, len(frame) + 1)
    frame['duplicate'] = frame.duplicated(subset=['source', 'account', 'tx_no'], keep='first')
    rows = frame.to_dict(orient='records')
    for row in rows:
        row.update(transaction_facts(row))
        row['category_status'] = 'suggested' if row['category_source'] == 'rule' else 'pending'
        if row['category_source'] == 'source':
            row['category_status'] = 'confirmed'
        row['category_reason'] = '原文件提供分类' if row['category_source'] == 'source' else (
            '商户或商品命中关键词规则' if row['category_source'] == 'rule' else '缺少明确消费用途')
        if row['nature'] != 'consumption':
            row.update(category='其他', category_source='unclassified', category_confidence=0.0,
                       category_status='not_applicable', category_reason='非明确消费记录，保留原始交易性质')
    # Same ID with different facts is a conflict, never silently deduplicate it.
    identities = {}
    for row in rows:
        key = row['tx_no']
        facts = tuple(row[k] for k in ('tx_time', 'amount_cents', 'direction', 'merchant', 'product',
                                       'transaction_type', 'transaction_status'))
        if key in identities and identities[key] != facts:
            for candidate in rows:
                if candidate['tx_no'] == key:
                    candidate['conflict'] = '文件内相同交易单号内容不一致'
        identities[key] = facts
    return rows, parsed['errors'], platform


def group_rows(rows):
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    frame['group_id'] = [group_key(row) for row in rows]
    result = []
    for gid, group in frame.groupby('group_id', sort=False):
        first = group.iloc[0].to_dict()
        result.append({'id': gid, 'merchant': first['merchant'], 'product': first['product'],
                       'nature': first['nature'], 'count': len(group),
                       'total_cents': int(group['amount_cents'].sum()),
                       'pending': int((group['category_status'].isin(['pending', 'suggested']) &
                                       ~group.get('excluded', pd.Series(False, index=group.index))).sum()),
                       'categories': dict(Counter(group['category'])),
                       'row_ids': [int(v) for v in group['row_id']],
                       'samples': group[['row_id', 'tx_time', 'amount_cents', 'category', 'category_status',
                                         'category_reason']].head(3).to_dict(orient='records')})
    return sorted(result, key=lambda g: (g['pending'] == 0, -g['count']))
