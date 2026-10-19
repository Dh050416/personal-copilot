from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.data.models import Bill
from app.data.views import bill_view
from app.schemas import ClassifyBillsRequest, ConfirmBillCategory
from app.services.bill_service import classify_existing, import_rows
from app.services.classification import BillClassifier
from app.services.importer import parse_file

router = APIRouter(prefix='/api/bills', tags=['bills'])


@router.post('/preview')
async def preview(file: UploadFile = File(...)):
    content = await file.read()
    try:
        return parse_file(content, file.filename or 'upload.xlsx', source='preview')
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/import')
async def import_bills(request: Request, file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or 'upload.xlsx'
    if not filename.lower().endswith(('.xlsx', '.csv')):
        raise HTTPException(400, '只支持 .xlsx 或 .csv')
    try:
        parsed = parse_file(content, filename, source='xlsx')
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if parsed['errors']:
        return {**parsed, 'status': 'needs_review', 'message': '导入包含错误，请修正后重新导入'}
    try:
        return await import_rows(request.app.state.db, parsed['rows'], filename,
                                 BillClassifier(request.app.state.settings))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/classify')
async def classify_bills(payload: ClassifyBillsRequest, request: Request):
    return await classify_existing(request.app.state.db, BillClassifier(request.app.state.settings), payload.limit)


@router.post('/{bill_id}/category')
async def confirm_category(bill_id: int, payload: ConfirmBillCategory, request: Request):
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            bill = await session.get(Bill, bill_id)
            if bill is None:
                raise HTTPException(404, '账单不存在')
            bill.category = payload.category
            bill.category_source = 'user'
            bill.category_confidence = 1.0
        return bill_view(bill)
