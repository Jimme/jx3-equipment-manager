"""Global creation intents also work before an account exists."""
import hashlib
import json
from fastapi import HTTPException
from .models import Account, Character, CreationReceipt, Cycle, Source


def begin_creation(session, operation, payload):
    key = payload.request_id
    fingerprint = hashlib.sha256(json.dumps([operation, payload.model_dump(exclude={'request_id'})], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    row = session.get(CreationReceipt, key) if key else None
    if row:
        if row.fingerprint != fingerprint:
            raise HTTPException(409, '此创建标识内容已改变，请先核对上一笔')
        result = json.loads(row.result)
        if result.get('_restored_request'):
            raise HTTPException(409, '此创建操作已因恢复或删除失效，请核对最新列表后新建')
        model = {'account': Account, 'character': Character, 'cycle': Cycle, 'source': Source}.get(operation.split(':', 1)[0])
        if model and session.get(model, result.get('id')) is None:
            raise HTTPException(409, '该创建记录已删除，旧请求不能重新创建，请核对列表')
        return fingerprint, result
    return fingerprint, None


def finish_creation(session, payload, fingerprint, result):
    if payload.request_id:
        session.add(CreationReceipt(request_id=payload.request_id, fingerprint=fingerprint,
                                    result=json.dumps(result, ensure_ascii=False)))
    session.commit()
    return result
