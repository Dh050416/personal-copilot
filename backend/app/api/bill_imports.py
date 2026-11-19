"""Account-isolated import review API; finalization reads trusted server-side staging."""
import json

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from app.data.models import Bill, BillCategoryRule, BillImportJob, BillImportRow, now_utc
from app.services.bill_imports import (
    MAX_BYTES,
    assess,
    confirm_job,
    dump,
    enqueue,
    error_csv,
    job_view,
    owned,
)
from app.services.bill_pipeline import CORE_FIELDS, group_rows, rule_key
from app.services.classification import CATEGORIES

router = APIRouter(prefix='/api/bills/imports', tags=['bill imports'])


class RowEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    row_ids: list[int] = Field(min_length=1, max_length=10000)
    category: str | None = Field(default=None, max_length=64)
    excluded: bool | None = None
    save_rule: bool = False
    adopt_legacy: bool = False


class ConfirmImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    exclude_errors: bool = False


@router.post('', status_code=202)
async def upload(request: Request, file: UploadFile = File(...), account: str = Form('default')):
    content = await file.read(MAX_BYTES + 1)
    return await enqueue(request.app.state.db, request.state.user_id, file.filename or 'bill.xlsx', content, account)


@router.get('')
async def list_jobs(request: Request):
    async with request.app.state.db.sessions() as session:
        jobs = await session.scalars(select(BillImportJob).where(BillImportJob.user_id == request.state.user_id)
                                     .order_by(BillImportJob.created_at.desc()).limit(30))
        return [job_view(j) for j in jobs]


@router.get('/{job_id}')
async def detail(job_id: str, request: Request):
    async with request.app.state.db.sessions() as session:
        job = await owned(session, job_id, request.state.user_id)
        staged = list(await session.scalars(select(BillImportRow).where(BillImportRow.job_id == job_id)
                                            .order_by(BillImportRow.row_id)))
        valid = []
        counts = {'new': 0, 'duplicate': 0, 'duplicate_file': 0, 'conflict': 0, 'invalid': 0, 'excluded': 0}
        for item in staged:
            counts[item.disposition] = counts.get(item.disposition, 0) + 1
            counts['excluded'] += int(item.excluded)
            if item.disposition != 'invalid':
                row = json.loads(item.data_json)
                row['excluded'], row['error'] = item.excluded, item.error
                valid.append(row)
        return {**job_view(job), 'counts': counts, 'groups': group_rows(valid),
                'pending': sum(r['category_status'] in ('pending', 'suggested') and not r['excluded'] for r in valid)}


@router.get('/{job_id}/rows')
async def rows(job_id: str, request: Request, offset: int = Query(0, ge=0),
               limit: int = Query(100, ge=1, le=200), errors_only: bool = False):
    async with request.app.state.db.sessions() as session:
        await owned(session, job_id, request.state.user_id)
        query = select(BillImportRow).where(BillImportRow.job_id == job_id)
        if errors_only:
            query = query.where(BillImportRow.error != '')
        items = await session.scalars(query.order_by(BillImportRow.row_id).offset(offset).limit(limit))
        return [{'row_id': r.row_id, 'data': json.loads(r.data_json), 'error': r.error,
                 'disposition': r.disposition, 'excluded': r.excluded} for r in items]


@router.post('/{job_id}/rows')
async def edit_rows(job_id: str, payload: RowEdit, request: Request):
    if payload.category is not None and payload.category not in CATEGORIES:
        raise HTTPException(400, '不支持的消费分类')
    if payload.save_rule and payload.category is None:
        raise HTTPException(400, '保存规则前请选择分类')
    uid = request.state.user_id
    async with request.app.state.db.sessions() as session, session.begin():
        job = await owned(session, job_id, uid, lock=True)
        if job.status != 'review':
            raise HTTPException(409, '只有待确认任务可以修改')
        await session.execute(text('SELECT pg_advisory_xact_lock(7315, :uid)'), {'uid': uid})
        items = list(await session.scalars(select(BillImportRow).where(
            BillImportRow.job_id == job_id, BillImportRow.row_id.in_(set(payload.row_ids)))))
        if len(items) != len(set(payload.row_ids)):
            raise HTTPException(404, '部分暂存行不存在')
        for item in items:
            row = json.loads(item.data_json)
            if payload.excluded is not None:
                item.excluded = payload.excluded
            if item.disposition == 'invalid':
                if payload.category is not None or payload.adopt_legacy:
                    raise HTTPException(400, '无效记录只能排除，或修正源文件后重新上传')
                continue
            if payload.adopt_legacy:
                candidates = list(await session.scalars(select(Bill).where(
                    Bill.user_id == uid, Bill.source.in_(['xlsx', 'csv']), Bill.tx_no == row['tx_no']
                ).with_for_update()))
                if len(candidates) != 1 or any(getattr(candidates[0], k) != row[k] for k in CORE_FIELDS):
                    raise HTTPException(409, '无法唯一匹配旧账单或核心字段冲突，请人工核对')
                legacy = candidates[0]
                legacy.source, legacy.account = row['source'], row['account']
                legacy.nature = row['nature']
                legacy.refund_cents = max(legacy.refund_cents, row['refund_cents'])
                await session.flush()
                item.disposition, item.error = await assess(session, uid, row)
            if payload.category is not None:
                row.update(category=payload.category, category_source='user', category_confidence=1.0,
                           category_status='confirmed', category_reason='用户在导入预览中确认')
                # Explicitly categorized outgoing transfers can be declared consumption by the user.
                if row['direction'] == 'expense' and row['nature'] in ('unknown', 'transfer'):
                    row['nature'] = 'consumption'
            if payload.save_rule:
                fields = dict(zip(('platform', 'account', 'merchant', 'product', 'transaction_type'), rule_key(row)))
                rule = await session.scalar(select(BillCategoryRule).where(
                    BillCategoryRule.user_id == uid, *[getattr(BillCategoryRule, k) == v for k, v in fields.items()]))
                if rule:
                    rule.category = payload.category
                else:
                    session.add(BillCategoryRule(user_id=uid, **fields, category=payload.category))
                    await session.flush()
            item.data_json = dump(row)
        job.updated_at = now_utc()
    return {'updated': len(items)}


@router.post('/{job_id}/confirm')
async def confirm(job_id: str, payload: ConfirmImport, request: Request):
    return await confirm_job(request.app.state.db, request.state.user_id, job_id, payload.exclude_errors)


@router.post('/{job_id}/retry')
async def retry(job_id: str, request: Request):
    async with request.app.state.db.sessions() as session, session.begin():
        job = await owned(session, job_id, request.state.user_id, lock=True)
        if job.status not in ('failed', 'review'):
            raise HTTPException(409, '当前任务无法重试')
        job.status, job.attempts, job.error, job.owner = 'queued', 0, '', ''
        job.updated_at = now_utc()
        return job_view(job)


@router.get('/{job_id}/errors')
async def errors(job_id: str, request: Request):
    async with request.app.state.db.sessions() as session:
        await owned(session, job_id, request.state.user_id)
        items = list(await session.scalars(select(BillImportRow).where(BillImportRow.job_id == job_id)))
        return Response(error_csv(items), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="bill-import-errors.csv"'})


@router.delete('/{job_id}')
async def discard(job_id: str, request: Request):
    async with request.app.state.db.sessions() as session, session.begin():
        job = await owned(session, job_id, request.state.user_id, lock=True)
        if job.status == 'imported':
            raise HTTPException(409, '已导入任务不能删除；不会连带删除账单')
        await session.delete(job)
    return {'deleted': True}
