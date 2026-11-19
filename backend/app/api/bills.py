from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.data.models import Bill
from app.data.views import bill_view
from app.schemas import AddBillArgs, ClassifyBillsRequest, ConfirmBillCategory
from app.services.bill_imports import MAX_BYTES, enqueue
from app.services.bill_service import add_bill, classify_existing
from app.services.classification import BillClassifier
from app.services.importer import parse_file

router = APIRouter(prefix='/api/bills', tags=['bills'])


@router.post('')
async def create_bill(payload: AddBillArgs, request: Request):
    """随手记：直接写入一笔账单（source='manual'），不走文件导入、不经 Agent/模型。

    确定性录入动作：金额/方向/分类/对方/备注均由用户显式填写，无需语义理解，
    因此直连库写入（与 confirm_category/complete_card 等端点同属「直写」模式）。
    """
    return await add_bill(request.app.state.db, request.state.user_id, payload)


@router.post('/preview')
async def preview(file: UploadFile = File(...)):
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, '账单文件不能超过 20MB')
    try:
        return parse_file(content, file.filename or 'upload.xlsx', source='preview')
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/import', status_code=202)
async def import_bills(request: Request, file: UploadFile = File(...)):
    return await enqueue(request.app.state.db, request.state.user_id, file.filename or 'bill.xlsx',
                         await file.read(MAX_BYTES + 1), 'default')


@router.post('/classify')
async def classify_bills(payload: ClassifyBillsRequest, request: Request):
    return await classify_existing(request.app.state.db, request.state.user_id,
                                   BillClassifier(request.app.state.settings), payload.limit)


@router.post('/{bill_id}/category')
async def confirm_category(bill_id: int, payload: ConfirmBillCategory, request: Request):
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            bill = await session.get(Bill, bill_id)
            if bill is None or bill.user_id != request.state.user_id:
                raise HTTPException(404, '账单不存在')
            bill.category = payload.category
            bill.category_source = 'user'
            bill.category_confidence = 1.0
            bill.category_status = 'confirmed'
            bill.category_reason = '用户手动确认'
            if bill.direction == 'expense' and bill.nature in ('transfer', 'unknown'):
                bill.nature = 'consumption'
        return bill_view(bill)
