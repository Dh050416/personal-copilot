"""Durable bill import queue, review and atomic finalization (PostgreSQL)."""
import asyncio
import csv
import hashlib
import io
import json
import logging
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select, text

from app.data.models import Bill, BillCategoryRule, BillImportJob, BillImportRow, ImportBatch, now_utc
from app.services.bill_pipeline import CORE_FIELDS, RAW_FIELDS, is_missing, rule_key, stage_file
from app.services.classification import BillClassifier, has_classification_evidence

log = logging.getLogger(__name__)
MAX_BYTES = 20 * 1024 * 1024
ACTIVE = ('queued', 'processing')


def dump(value):
    return json.dumps(value, ensure_ascii=False)


async def owned(session, job_id, uid, lock=False):
    job = await session.scalar(select(BillImportJob).where(
        BillImportJob.id == job_id, BillImportJob.user_id == uid).with_for_update() if lock else
        select(BillImportJob).where(BillImportJob.id == job_id, BillImportJob.user_id == uid))
    if job is None:
        raise HTTPException(404, '导入任务不存在')
    return job


def job_view(job):
    return {'id': job.id, 'filename': job.filename, 'account': job.account, 'status': job.status,
            'progress': job.progress, 'error': job.error, 'attempts': job.attempts,
            'created_at': job.created_at.isoformat(), 'result': json.loads(job.result_json)}


async def enqueue(db, uid, filename, content, account):
    if not filename.lower().endswith(('.csv', '.xlsx')):
        raise HTTPException(400, '只支持 CSV / XLSX')
    if not content or len(content) > MAX_BYTES:
        raise HTTPException(413, '账单文件须非空且不超过 20MB')
    account = account.strip()
    if not account or len(account) > 80:
        raise HTTPException(400, '请输入不超过 80 字的账户标记；同一账户请保持一致')
    digest = hashlib.sha256(content).hexdigest()
    async with db.sessions() as session, session.begin():
        await session.execute(text('SELECT pg_advisory_xact_lock(7315, :uid)'), {'uid': uid})
        existing = await session.scalar(select(BillImportJob).where(
            BillImportJob.user_id == uid, BillImportJob.account == account, BillImportJob.file_hash == digest))
        if existing:
            return job_view(existing)
        count = len(list(await session.scalars(select(BillImportJob.id).where(
            BillImportJob.user_id == uid, BillImportJob.status.in_(ACTIVE)))))
        if count >= 3:
            raise HTTPException(429, '最多同时处理 3 个账单文件')
        job = BillImportJob(id=uuid4().hex, user_id=uid, account=account, filename=filename[:255],
                            file_hash=digest, content=content)
        session.add(job)
        await session.flush()
        return job_view(job)


async def existing_for(session, uid, row):
    return await session.scalar(select(Bill).where(
        Bill.user_id == uid, Bill.source == row['source'], Bill.account == row['account'],
        Bill.tx_no == row['tx_no']))


async def assess(session, uid, row):
    if row.get('conflict'):
        return 'conflict', row['conflict']
    existing = await existing_for(session, uid, row)
    if existing:
        if any(getattr(existing, key) != row[key] for key in CORE_FIELDS):
            return 'conflict', '已有交易的日期、金额、方向或交易对方不一致'
        return 'duplicate', ''
    # Legacy source='xlsx' did not distinguish accounts/platforms. Require an explicit decision,
    # rather than silently adopting/deleting historical records or double-counting them.
    legacy = await session.scalar(select(Bill.id).where(Bill.user_id == uid, Bill.source.in_(['xlsx', 'csv']),
                                                        Bill.tx_no == row['tx_no']))
    if legacy:
        return 'conflict', '命中旧版未标记平台的账单，请先迁移历史来源或排除此行，避免重复入账'
    return ('duplicate_file', '') if row.get('duplicate') else ('new', '')


async def process_job(db, job_id, owner, settings):
    async with db.sessions() as session:
        job = await session.get(BillImportJob, job_id)
        content, filename, account, uid = job.content, job.filename, job.account, job.user_id
    rows, errors, platform = await asyncio.to_thread(stage_file, content, filename, account)
    # Persist parsing before inference, so a process crash does not lose reviewable records.
    async with db.sessions() as session, session.begin():
        job = await session.get(BillImportJob, job_id, with_for_update=True)
        if job is None or job.owner != owner or job.status != 'processing':
            return
        saved = list(await session.scalars(select(BillImportRow).where(BillImportRow.job_id == job_id)))
        if saved:
            rows = [json.loads(r.data_json) for r in saved if not r.error or r.disposition != 'invalid']
        else:
            rules = {rule_key({'source': r.platform, 'account': r.account, 'merchant': r.merchant,
                               'product': r.product, 'transaction_type': r.transaction_type}): r.category
                     for r in await session.scalars(select(BillCategoryRule).where(BillCategoryRule.user_id == uid))}
            for row in rows:
                category = rules.get(rule_key(row))
                if category and row['direction'] == 'expense' and row['nature'] in ('consumption', 'unknown', 'transfer'):
                    row.update(category=category, category_source='user_rule', category_status='confirmed',
                               category_confidence=1.0, category_reason='用户保存的同账户商户与商品规则',
                               nature='consumption')
                disposition, error = await assess(session, uid, row)
                session.add(BillImportRow(job_id=job_id, row_id=row['row_id'], data_json=dump(row),
                                          error=error, disposition=disposition))
            for i, error in enumerate(errors, len(rows) + 1):
                session.add(BillImportRow(job_id=job_id, row_id=i, data_json=dump(error),
                                          error=error['message'], disposition='invalid'))
        job.progress, job.updated_at = 30, now_utc()
        job.result_json = dump({'platform': platform, 'rows': len(rows), 'invalid': len(errors)})
    classifier = BillClassifier(settings)
    # Small durable batches. Each batch has a bounded model timeout; budget is explicit.
    attempted, failed = 0, 0
    pending = [r for r in rows if r['category_status'] == 'pending' and r['nature'] == 'consumption'
               and not r.get('model_evaluated') and has_classification_evidence(r)]
    signatures = {}
    for row in pending:
        signatures.setdefault(rule_key(row), []).append(row)
    groups = list(signatures.values())
    for start in range(0, min(len(groups), settings.classification_max_batches * 12), 12):
        batch = [r for group in groups[start:start + 12] for r in group]
        report = await classifier.classify(batch)
        for row in batch:
            row['model_attempts'] = row.get('model_attempts', 0) + 1
            row['model_evaluated'] = report['failed_batches'] == 0
        attempted += report['model_batches']
        failed += report['failed_batches']
        async with db.sessions() as session, session.begin():
            job = await session.get(BillImportJob, job_id, with_for_update=True)
            if job is None or job.owner != owner or job.status != 'processing':
                return
            for row in batch:
                staged = await session.get(BillImportRow, (job_id, row['row_id']))
                staged.data_json = dump(row)
            job.progress = min(95, 30 + int(65 * min(start + 12, len(groups)) / max(1, len(groups))))
            job.updated_at = now_utc()
        if failed:
            break
    async with db.sessions() as session, session.begin():
        job = await session.get(BillImportJob, job_id, with_for_update=True)
        if job is not None and job.owner == owner and job.status == 'processing':
            job.status, job.progress, job.updated_at = 'review', 100, now_utc()
            job.result_json = dump({**json.loads(job.result_json), 'model_batches': attempted,
                                    'failed_batches': failed,
                                    'classification_note': '模型建议需核实；预算外、缺少依据或失败的记录保留待确认'})


async def claim_job(db):
    async with db.sessions() as session, session.begin():
        job = await session.scalar(select(BillImportJob).where(or_(
            BillImportJob.status == 'queued',
            (BillImportJob.status == 'processing') & (BillImportJob.updated_at < now_utc() - timedelta(seconds=90))
        )).order_by(BillImportJob.created_at).with_for_update(skip_locked=True).limit(1))
        if job is None:
            return None
        if job.attempts >= 3:
            job.status, job.error = 'failed', '处理连续中断，请点击重试'
            return None
        job.status, job.owner = 'processing', uuid4().hex
        job.attempts += 1
        job.updated_at = now_utc()
        return job.id, job.owner


async def heartbeat(db, job_id, owner):
    while True:
        await asyncio.sleep(10)
        async with db.sessions() as session, session.begin():
            job = await session.get(BillImportJob, job_id, with_for_update=True)
            if job is None or job.owner != owner or job.status != 'processing':
                return
            job.updated_at = now_utc()


async def run_worker(db, settings):
    while True:
        try:
            claim = await claim_job(db)
            if claim:
                job_id, owner = claim
                pulse = asyncio.create_task(heartbeat(db, job_id, owner))
                try:
                    await asyncio.wait_for(process_job(db, job_id, owner, settings), timeout=600)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception('bill_import.failed', extra={'job_id': job_id})
                    async with db.sessions() as session, session.begin():
                        job = await session.get(BillImportJob, job_id, with_for_update=True)
                        if job is not None and job.owner == owner and job.status == 'processing':
                            job.status, job.error = 'failed', '解析或处理失败，请查看文件格式后重试'
                finally:
                    pulse.cancel()
                    await asyncio.gather(pulse, return_exceptions=True)
            else:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('bill_import.poll_failed')
            await asyncio.sleep(3)


async def confirm_job(db, uid, job_id, exclude_errors):
    async with db.sessions() as session, session.begin():
        job = await owned(session, job_id, uid, lock=True)
        if job.status == 'imported':
            return job_view(job)
        if job.status != 'review':
            raise HTTPException(409, '任务尚未完成预览')
        # Serialize all imports for this user, including different files with overlapping transactions.
        await session.execute(text('SELECT pg_advisory_xact_lock(7315, :uid)'), {'uid': uid})
        staged = list(await session.scalars(select(BillImportRow).where(BillImportRow.job_id == job_id)))
        inserted = skipped = enriched = excluded = 0
        for item in staged:
            if item.excluded:
                excluded += 1
                continue
            row = json.loads(item.data_json)
            disposition, error = ('invalid', item.error) if item.disposition == 'invalid' else await assess(session, uid, row)
            if error:
                if exclude_errors:
                    item.excluded = True
                    excluded += 1
                    continue
                raise HTTPException(409, '存在异常或冲突记录；请修正、排除或明确选择跳过异常')
            if disposition == 'duplicate_file':
                skipped += 1
                continue
            existing = await existing_for(session, uid, row)
            if existing:
                # A later export may contain a newer refund status. Monotonically retain refund amount.
                changed = False
                for key in RAW_FIELDS:
                    if is_missing(getattr(existing, key)) and not is_missing(row[key]):
                        setattr(existing, key, row[key])
                        changed = True
                if row['refund_cents'] > existing.refund_cents:
                    existing.refund_cents = row['refund_cents']
                    existing.transaction_status = row['transaction_status']
                    changed = True
                if existing.category_source not in {'user', 'manual'} and row['category_source'] == 'user':
                    for key in ('category', 'category_source', 'category_confidence', 'category_status', 'category_reason'):
                        setattr(existing, key, row[key])
                    existing.nature = row['nature']
                    changed = True
                enriched += int(changed)
                skipped += 1
                continue
            fields = {c.name for c in Bill.__table__.columns} - {'id', 'user_id', 'import_batch_id', 'category_checked_at'}
            bill = Bill(user_id=uid, import_batch_id=job_id, category_checked_at=now_utc(),
                        **{k: v for k, v in row.items() if k in fields})
            session.add(bill)
            await session.flush()
            inserted += 1
        session.add(ImportBatch(id=job_id, user_id=uid, filename=job.filename,
                                report_json=dump({'inserted': inserted, 'skipped': skipped, 'excluded': excluded})))
        job.status, job.updated_at = 'imported', now_utc()
        job.result_json = dump({**json.loads(job.result_json), 'inserted': inserted, 'skipped': skipped,
                                'enriched': enriched, 'excluded': excluded})
        return job_view(job)


def error_csv(rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['暂存行号', '原始工作表', '原始行号', '问题'])
    for r in rows:
        if r.error:
            data = json.loads(r.data_json)
            # Prefix cells to prevent spreadsheet formula injection in exported text.
            writer.writerow([r.row_id, "'" + str(data.get('source_sheet', data.get('sheet', ''))),
                             data.get('source_row', data.get('row', '')), "'" + r.error])
    return '\ufeff' + out.getvalue()
