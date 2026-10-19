from datetime import datetime
from io import BytesIO

from openpyxl import Workbook

from app.services.importer import parse_amount, parse_date, parse_file


def make_workbook():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '账单'
    sheet.append(['交易单号', '交易时间', '交易对方', '金额', '收支', '分类'])
    sheet.append(['wx-1', datetime(2026, 9, 1, 12, 0), '咖啡店', 12.5, '支出', '餐饮'])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_xlsx_import_and_integer_cents():
    parsed = parse_file(make_workbook(), 'bill.xlsx', 'xlsx')
    assert parsed['errors'] == []
    assert parsed['rows'][0]['amount_cents'] == 1250
    assert parsed['rows'][0]['transaction_type'] == '未知'
    assert parsed['rows'][0]['category'] == '餐饮'


def test_wechat_transaction_type_does_not_become_category():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(['交易时间', '交易类型', '交易对方', '商品', '收/支', '金额(元)',
                  '支付方式', '当前状态', '交易单号', '商户单号', '备注'])
    sheet.append([datetime(2026, 9, 1, 12, 0), '商户消费', '咖啡店', '/', '支出', 12.5,
                  '零钱', '支付成功', 'wx-1', 'merchant-1', '/'])
    output = BytesIO()
    workbook.save(output)

    parsed = parse_file(output.getvalue(), 'wechat.xlsx', 'xlsx')
    row = parsed['rows'][0]
    assert row['transaction_type'] == '商户消费'
    assert row['category'] == '餐饮'
    assert row['category_source'] == 'rule'


def test_rejects_fractional_cents():
    try:
        parse_amount('1.001')
    except ValueError as exc:
        assert '两位小数' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_requires_transaction_number():
    try:
        parse_date('not-a-date')
    except ValueError:
        pass
