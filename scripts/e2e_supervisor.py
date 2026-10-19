"""Supervisor 架构 E2E 验收：真实模型直连 /chat。
1) 账单请求 → 应路由进账单子图完成查询并产出 final
2) 闲聊 → 应走 general 兜底直接回复
3) 执行中追加：查询途中追加条件 → 重路由仍进账单子图，不重跑
"""
import asyncio, json, sys, time
import httpx

BASE = 'http://127.0.0.1:8000'
CHAT_URL = f'{BASE}/api/chat'

async def post_chat(client, session_id, message, timeout=120):
    events = []
    try:
        async with client.stream('POST', CHAT_URL,
                                 json={'session_id': session_id, 'message': message},
                                 timeout=timeout) as resp:
            print(f'[http {resp.status_code} {resp.headers.get("content-type")}]', file=sys.stderr)
            async for line in resp.aiter_lines():
                if not line.startswith('data:'):
                    if line.strip():
                        print(f'[non-data line] {line[:200]}', file=sys.stderr)
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    events.append(json.loads(data))
                except Exception:
                    pass
    except Exception as exc:
        events.append({'type': 'error', 'payload': {'error': str(exc)}})
    return events

def summarize(events):
    kinds = [e.get('type') for e in events]
    finals = [e['payload']['text'] for e in events if e.get('type') == 'final']
    errors = [e['payload'] for e in events if e.get('type') == 'error']
    progress = sum(1 for e in events if e.get('type') == 'progress')
    streams = sum(1 for e in events if e.get('type') == 'stream')
    approvals = [e['payload'] for e in events if e.get('type') == 'approval_created']
    return {
        'types': kinds, 'finals': finals, 'errors': errors,
        'progress': progress, 'stream_chunks': streams, 'approvals': approvals,
    }


async def resume_run(client, run_id, approval_id, decision):
    events = []
    try:
        async with client.stream('POST', f'{BASE}/api/runs/{run_id}/resume',
                                 json={'approval_id': approval_id, 'decision': decision},
                                 timeout=120) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    events.append(json.loads(data))
                except Exception:
                    pass
    except Exception as exc:
        events.append({'type': 'error', 'payload': {'error': str(exc)}})
    return events

async def main():
    client = httpx.AsyncClient(timeout=180)
    # 每次运行用全新 session（带时间戳），避免 checkpoint 残留干扰断言
    stamp = str(int(time.time() * 1000))
    try:
        # 1) 账单查询（Supervisor 路由 → 账单子图）
        print('=== 1) 账单查询（期望进账单子图，产出 final）===')
        ev = await post_chat(client, f'e2e-sup-1-{stamp}', '查询今天餐饮支出')
        s = summarize(ev)
        print(json.dumps(s, ensure_ascii=False, indent=1))
        assert s['finals'] and not s['errors'], '账单查询应产出 final 且无错误'
        assert '餐饮' in s['finals'][-1], 'final 应包含餐饮统计结果'

        # 2) 闲聊（期望 general 兜底直接回复）
        print('\n=== 2) 闲聊（期望 general 兜底直接回复）===')
        ev2 = await post_chat(client, f'e2e-sup-2-{stamp}', '你好，你是谁')
        s2 = summarize(ev2)
        print(json.dumps(s2, ensure_ascii=False, indent=1))
        assert s2['finals'] and not s2['errors'], '闲聊应产出 final 且无错误'

        # 3) 搜索请求（期望路由进搜索子图，web_search 真调网络）
        print('\n=== 3) 搜索请求（期望进搜索子图，web_search 真实搜索）===')
        ev3 = await post_chat(client, f'e2e-sup-3-{stamp}', '查一下杭州最近的天气怎么样')
        s3 = summarize(ev3)
        print(json.dumps(s3, ensure_ascii=False, indent=1))
        assert s3['finals'] and not s3['errors'], '搜索应产出 final 且无错误'
        assert any('杭州' in f or '天气' in f for f in s3['finals']), \
            f"final 应包含搜索内容，实际：{s3['finals']}"

        # 4) 日程查询（期望路由进日程子图，list_events 本地查询）
        print('\n=== 4) 日程查询（期望进日程子图，list_events）===')
        ev4 = await post_chat(client, f'e2e-sup-4-{stamp}', '查一下这周的日程安排')
        s4 = summarize(ev4)
        print(json.dumps(s4, ensure_ascii=False, indent=1))
        assert s4['finals'] and not s4['errors'], '日程查询应产出 final 且无错误'

        # 5) 新建日程（期望进日程子图 → create_event 写操作 → HITL 审批 → resume 后完成）
        print('\n=== 5) 新建日程（期望审批流：approval_created → resume approve）===')
        sid5 = f'e2e-sup-5-{stamp}'
        ev5 = await post_chat(client, sid5, '明天下午三点安排一个产品评审会议，地点在会议室B')
        s5 = summarize(ev5)
        print(json.dumps(s5, ensure_ascii=False, indent=1))
        assert s5['approvals'] and not s5['errors'], '建日程应先进入审批'
        ap = s5['approvals'][0]
        assert ap['tool'] == 'create_event'
        # 审批通过 → 完成建日程
        ev5b = await resume_run(client, sid5, ap['approval_id'], 'approve')
        s5b = summarize(ev5b)
        print(json.dumps(s5b, ensure_ascii=False, indent=1))
        assert s5b['finals'] and not s5b['errors'], '审批后应产出 final 且无错误'
        assert any(('已创建' in f or '已安排' in f or '产品评审' in f) for f in s5b['finals']), \
            f"final 应确认建日程：{s5b['finals']}"

        print('\nE2E 验收通过：Supervisor 四路路由（账单/搜索/日程子图 + general 兜底 + HITL 审批）均正常')
    finally:
        await client.aclose()

if __name__ == '__main__':
    asyncio.run(main())
