import hashlib
import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.api.knowledge_uploads import router


@pytest.fixture
async def client(db):
    app = FastAPI()
    app.state.db = db
    app.include_router(router)

    @app.middleware('http')
    async def identify(request, call_next):
        request.state.user_id = int(request.headers.get('X-Test-User', '1'))
        return await call_next(request)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as c:
        yield c


def digest(source):
    return hashlib.sha256(source).hexdigest()


async def test_large_file_resume_idempotence_and_isolation(client):
    source = b'x' * (21 * 1024 * 1024 + 7)
    metadata = {'filename': 'large.txt', 'size': len(source), 'sha256': digest(source)}
    started = await client.post('/uploads', json=metadata)
    assert started.status_code == 200
    upload = started.json()
    uid, size = upload['upload_id'], upload['chunk_size']
    assert (await client.get(f'/uploads/{uid}', headers={'X-Test-User': '2'})).status_code == 404
    assert (await client.post(f'/uploads/{uid}/complete')).status_code == 409
    first = source[:size]
    assert (await client.put(f'/uploads/{uid}/parts/0', content=first,
                            headers={'X-Chunk-SHA256': '0' * 64})).status_code == 400
    assert (await client.put(f'/uploads/{uid}/parts/0', content=first,
                            headers={'X-Chunk-SHA256': digest(first)})).status_code == 200
    resumed = (await client.post('/uploads', json=metadata)).json()
    assert resumed['upload_id'] == uid and resumed['received'] == [0]
    for part in range(1, (len(source) + size - 1) // size):
        chunk = source[part * size:(part + 1) * size]
        assert (await client.put(f'/uploads/{uid}/parts/{part}', content=chunk,
                                headers={'X-Chunk-SHA256': digest(chunk)})).status_code == 200
    completed = await client.post(f'/uploads/{uid}/complete')
    assert completed.status_code == 200
    doc_id = completed.json()['doc_id']
    assert (await client.post(f'/uploads/{uid}/complete')).json()['doc_id'] == doc_id
    duplicate = (await client.post('/uploads', json=metadata)).json()
    assert duplicate['doc_id'] == doc_id and duplicate['existing']


async def test_upload_quota_and_cancel(client):
    uploads = []
    for index in range(3):
        response = await client.post('/uploads', json={'filename': 'a.txt', 'size': 1,
                                                       'sha256': digest(str(index).encode())})
        assert response.status_code == 200
        uploads.append(response.json()['upload_id'])
    metadata = {'filename': 'a.txt', 'size': 1, 'sha256': digest(b'fourth')}
    assert (await client.post('/uploads', json=metadata)).status_code == 429
    assert (await client.delete(f'/uploads/{uploads[0]}')).status_code == 200
    assert (await client.post('/uploads', json=metadata)).status_code == 200


async def test_disconnect_rolls_back_part_and_concurrent_init_is_idempotent(client):
    source = b'resume after interruption'
    metadata = {'filename': 'a.txt', 'size': len(source), 'sha256': digest(source)}
    first, second = await asyncio.gather(client.post('/uploads', json=metadata),
                                         client.post('/uploads', json=metadata))
    uid = first.json()['upload_id']
    assert second.json()['upload_id'] == uid

    async def interrupted():
        yield source[:5]
        raise RuntimeError('simulated disconnect')

    with pytest.raises(Exception):
        await client.put(f'/uploads/{uid}/parts/0', content=interrupted(),
                         headers={'X-Chunk-SHA256': digest(source)})
    assert (await client.get(f'/uploads/{uid}')).json()['received'] == []
    assert (await client.put(f'/uploads/{uid}/parts/0', content=source,
                            headers={'X-Chunk-SHA256': digest(source)})).status_code == 200
    first, second = await asyncio.gather(client.post(f'/uploads/{uid}/complete'),
                                         client.post(f'/uploads/{uid}/complete'))
    assert first.json()['doc_id'] == second.json()['doc_id']
