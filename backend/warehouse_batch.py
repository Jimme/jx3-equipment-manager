"""Preview and apply inventory corrections against the original stock quantities."""
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import backup_database
from .equipment_batch import signature
from .inventory import finish_operation, start_operation
from .models import Cycle, DataChangePreview, EquipmentConfig, Template, WarehouseStack
from .schemas import BatchEquipmentApply, WarehouseBatchInput
from .services import active_account, choices_for, create_config, create_template, dump, equipment_details, new_id, require


def config_snapshot(session, config, template=None):
    template = template or require(session, Template, config.template_id)
    equipment = equipment_details(dump(template), template.source)
    equipment.pop('id')
    return {'equipment': equipment, 'choices': choices_for(config)}


def prepare(session, account_id, payload):
    active_account(session, account_id)
    stacks = list(session.scalars(select(WarehouseStack).where(WarehouseStack.account_id == account_id)))
    by_id = {stack.id: stack for stack in stacks}
    by_config = {stack.config_id: stack for stack in stacks}
    visible = {stack.id: stack.version for stack in stacks if stack.quantity > 0}
    if payload.all_stock is not None and visible != payload.all_stock:
        raise HTTPException(409, '仓库范围或数量已变化，请读取最新库存并重新选择全仓')

    changes, deltas, touched, destinations, cycles = [], Counter(), set(), set(), {}
    added = 0
    for index, row in enumerate(payload.rows, start=1):
        old = by_id.get(row.item_id)
        if row.item_id is not None and (old is None or old.version != row.expected_version or old.quantity < row.quantity):
            raise HTTPException(409, f'第 {index} 行库存或件数已变化，请读取最新库存')
        old_config = require(session, EquipmentConfig, old.config_id) if old is not None else None
        prior_template = require(session, Template, old_config.template_id) if old_config else None
        try:
            template = create_template(session, row.equipment, prior_template.source_id if prior_template else None)
            config = create_config(session, template, row.choices)
        except HTTPException as error:
            raise HTTPException(error.status_code, f'第 {index} 行：{error.detail}') from error
        cycles[template.cycle_origin_id] = require(session, Cycle, template.cycle_origin_id).version
        touched.add(config.id)
        previous = None
        if old is not None:
            touched.add(old.config_id)
            previous = config_snapshot(session, old_config, prior_template)
            if previous['equipment']['equipment_type'] != template.equipment_type:
                raise HTTPException(422, f'第 {index} 行不能更改已有库存的部位')
            if config.id == old.config_id:
                continue
            deltas[old.config_id] -= row.quantity
        else:
            added += row.quantity
        deltas[config.id] += row.quantity
        changes.append({'row': index, 'quantity': row.quantity, 'before': previous,
                        'remaining': old.quantity - row.quantity if old is not None else None,
                        'after': config_snapshot(session, config, template), 'destination': config.id,
                        'merged': config.id in destinations or bool(by_config.get(config.id) and by_config[config.id].quantity)})
        destinations.add(config.id)

    final = {}
    for config_id, delta in deltas.items():
        old = by_config.get(config_id)
        quantity = (old.quantity if old else 0) + delta
        if not 0 <= quantity <= 2147483647:
            related = next((change['row'] for change in changes if change['destination'] == config_id), None)
            raise HTTPException(422, f'第 {related} 行的合并目标库存数量超出范围')
        final[config_id] = quantity
    for change in changes:
        change['final_quantity'] = final[change.pop('destination')]
    state = signature({'stocks': [dump(by_config[key]) for key in sorted(touched & by_config.keys())],
                       'cycles': cycles, 'changes': changes})
    return changes, final, by_config, state, added


def make_warehouse_batch_router(engine):
    router = APIRouter(prefix='/api/accounts/{account_id}/warehouse-batch')
    kind = 'warehouse_batch'

    def database():
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            yield session

    @router.post('/preview')
    def preview(account_id: str, payload: WarehouseBatchInput, session: Session = Depends(database)):
        # The normal configuration resolver can create rows. Keep only the preview token.
        with session.begin_nested() as draft:
            changes, _, _, state, added = prepare(session, account_id, payload)
            draft.rollback()
        if not changes:
            return {'unchanged': True, 'changes': [], 'added': 0}
        now = datetime.now(timezone.utc)
        for token in session.scalars(select(DataChangePreview).where(DataChangePreview.kind == kind)):
            if now - datetime.fromisoformat(token.created_at) > timedelta(minutes=30):
                session.delete(token)
        token = DataChangePreview(id=new_id(), kind=kind, expected_fingerprint=state,
                                  payload_json=json.dumps({'account_id': account_id, 'input': payload.model_dump()}, ensure_ascii=False),
                                  created_at=now.isoformat(), result_json=None)
        session.add(token)
        session.commit()
        return {'preview_id': token.id, 'unchanged': False, 'changes': changes, 'added': added}

    @router.post('/apply')
    def apply(account_id: str, payload: BatchEquipmentApply, session: Session = Depends(database)):
        fingerprint, replay = start_operation(session, account_id, kind, payload)
        if replay is not None:
            return replay
        token = require(session, DataChangePreview, payload.preview_id)
        data = json.loads(token.payload_json)
        if token.kind != kind or data.get('account_id') != account_id:
            raise HTTPException(404, '批量预览不属于此仓库')
        if token.result_json:
            return json.loads(token.result_json)
        if datetime.now(timezone.utc) - datetime.fromisoformat(token.created_at) > timedelta(minutes=30):
            raise HTTPException(409, '批量预览已过期，请返回重新预览')
        changes, final, by_config, state, added = prepare(session, account_id, WarehouseBatchInput.model_validate(data['input']))
        if state != token.expected_fingerprint:
            raise HTTPException(409, '库存、来源或周期规则已变化，请核对后重新预览')
        try:
            backup = backup_database(Path(engine.url.database), 'before-warehouse-batch')
        except (OSError, sqlite3.Error, RuntimeError) as error:
            raise HTTPException(503, '备份失败，整批未保存；请检查目录权限和空间') from error
        before, after = [], []
        for config_id, quantity in final.items():
            stack = by_config.get(config_id)
            if stack is None:
                stack = WarehouseStack(id=new_id(), account_id=account_id, config_id=config_id, quantity=quantity, version=1)
                session.add(stack)
            elif stack.quantity != quantity:
                before.append(dump(stack))
                stack.quantity = quantity
                stack.version += 1
            else:
                continue
            after.append(dump(stack))
        result = {'changed': True, 'rows': len(changes), 'added': added, 'backup': backup.name}
        token.result_json = json.dumps(result)
        return finish_operation(session, account_id, kind, account_id, payload, fingerprint,
                                {'stacks': before}, {'stacks': after, 'changes': changes, 'backup': backup.name},
                                f'整批处理 {len(changes)} 行仓库配置，新增 {added} 件', result)

    return router
