from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.services.classification import BillClassifier, ClassificationItem, rule_classify


@pytest.mark.asyncio
async def test_classifier_keeps_transfer_unclassified_and_accepts_valid_model_labels(monkeypatch):
    config = Settings(_env_file=None)
    classifier = BillClassifier(config)

    async def fake_predict(items):
        return [ClassificationItem(key=item['key'], category='购物', confidence=.92) for item in items]

    monkeypatch.setattr(classifier, 'predict_batch', fake_predict)
    rows = [
        {'merchant': '某人', 'product': '转账', 'transaction_type': '转账', 'direction': 'expense',
         'category': '其他', 'category_source': 'unclassified', 'category_confidence': 0},
        {'merchant': '未知商户', 'product': '商品', 'transaction_type': '商户消费', 'direction': 'expense',
         'category': '其他', 'category_source': 'unclassified', 'category_confidence': 0},
        {'merchant': '用户确认', 'product': '', 'transaction_type': '商户消费', 'direction': 'expense',
         'category': '餐饮', 'category_source': 'user', 'category_confidence': 1},
    ]
    report = await classifier.classify(rows)
    assert rows[0]['category'] == '其他'
    assert rows[0]['category_source'] == 'unclassified'
    assert rows[1]['category'] == '购物'
    assert rows[1]['category_source'] == 'llm'
    assert rows[2]['category'] == '餐饮'
    assert report['failed_batches'] == 0


def raw_row(**changes):
    return dict({'merchant': '未知店', 'product': '商品', 'transaction_type': '商户消费',
                 'direction': 'expense', 'category': '其他', 'category_source': 'unclassified',
                 'category_confidence': 0}, **changes)


def test_conflicting_rule_evidence_abstains():
    assert rule_classify(raw_row(merchant='超市', product='咖啡'))['category_source'] == 'unclassified'
    assert rule_classify(raw_row(merchant='咖啡店', direction='income'))['category'] == '其他'


async def test_low_confidence_and_failure_do_not_invent_categories(monkeypatch):
    classifier = BillClassifier(Settings(_env_file=None))
    low = AsyncMock(return_value=[ClassificationItem(key=0, category='餐饮', confidence=.5)])
    monkeypatch.setattr(classifier, 'predict_batch', low)
    rows = [raw_row(), raw_row()]
    await classifier.classify(rows)
    assert len(low.await_args.args[0]) == 1  # identical evidence is classified once
    assert all(row['category_source'] == 'unclassified' for row in rows)
    low.side_effect = TimeoutError()
    report = await classifier.classify([raw_row(merchant=str(i)) for i in range(30)])
    assert report['failed_batches'] == 1 and report['model_batches'] == 1


async def test_model_call_budget(monkeypatch):
    classifier = BillClassifier(Settings(_env_file=None, classification_max_batches=1))
    async def predict(items):
        return [ClassificationItem(key=x['key'], category='购物', confidence=.9) for x in items]
    mock = AsyncMock(side_effect=predict)
    monkeypatch.setattr(classifier, 'predict_batch', mock)
    rows = [raw_row(merchant=str(i)) for i in range(25)]
    report = await classifier.classify(rows)
    mock.assert_awaited_once()
    assert report['unclassified'] == 13


@pytest.mark.parametrize('arguments', [
    '{broken', '{"items": [{"key":0,"category":"invented","confidence":1}]}',
    '{"items": [{"key":9,"category":"餐饮","confidence":1}]}',
    '{"items": []}',
    '{"items": [{"key":0,"category":"餐饮","confidence":1}, {"key":0,"category":"购物","confidence":1}]}',
])
async def test_bad_model_batch_is_rejected_before_applying(monkeypatch, arguments):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        tool_calls=[SimpleNamespace(function=SimpleNamespace(name='classify_bills', arguments=arguments))]
    ))])
    monkeypatch.setattr('app.services.classification.AsyncOpenAI', lambda **kwargs: client)
    row = raw_row()
    report = await BillClassifier(Settings(_env_file=None)).classify([row])
    assert report['failed_batches'] == 1 and row['category_source'] == 'unclassified'
