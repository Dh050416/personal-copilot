"""SSE 慢请求探针：验证 progress 心跳 + 审批事件 + stream。"""
import json
import time
import uuid

import requests

URL = 'http://127.0.0.1:8000/api/chat'
SID = 'probe-slow-' + uuid.uuid4().hex[:8]
MSG = '查询与章鱼大王的所有转账记录，并把这些记录全部生成卡片'

t0 = time.time()
with requests.post(URL, json={'message': MSG, 'session_id': SID},
                   stream=True, timeout=300) as resp:
    print('status:', resp.status_code)
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith('data: '):
            continue
        data = line[len('data: '):]
        if data == '[DONE]':
            print(f'{(time.time()-t0):6.2f}s [DONE]')
            break
        ev = json.loads(data)
        p = ev.get('payload', {})
        if ev['type'] in ('progress', 'approval_created', 'final', 'error'):
            print(f'{(time.time()-t0):6.2f}s {ev["type"]:20s} {json.dumps(p, ensure_ascii=False)[:140]}')
        elif ev['type'] == 'stream':
            pass  # token 流已单独验证，这里静默
        else:
            print(f'{(time.time()-t0):6.2f}s {ev["type"]:20s} ...')
