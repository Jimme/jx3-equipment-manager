"""Preview and atomically apply one character's equipment changes."""
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import backup_database
from .domain import SLOTS, equipment_type, label, socket_applicable, socket_required
from .inventory import check_version, finish_operation, owned, start_operation
from .models import Character, Cycle, DataChangePreview, EquipmentConfig, Template, WarehouseStack
from .schemas import BatchEquipmentApply, BatchEquipmentInput, EquipmentChoices
from .services import active_account, choices_for, create_config, create_template, dump, equipment_details, template_fields, new_id, replace_worn, require, writable_slot


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prepare(session, account_id, character_id, payload):
    account = active_account(session, account_id)
    character = require(session, Character, character_id)
    if character.account_id != account_id or character.archived:
        raise HTTPException(409, '角色已移除或不属于当前账号')
    state = {'account': {'id': account.id, 'archived': account.archived},
             'character': {'id': character.id, 'account_id': character.account_id, 'archived': character.archived},
             'worn': [], 'stock': {}, 'cycles': {}, 'sources': {}}
    rows, changes, needed = [], [], Counter()
    for entry in payload.rows:
        worn = writable_slot(session, account_id, character_id, entry.slot)
        if worn.version != entry.expected_version:
            raise HTTPException(409, f'{dict(SLOTS)[entry.slot]}已变化，请读取最新记录核对，整批未保存')
        config = session.get(EquipmentConfig, worn.config_id) if worn.config_id else None
        previous = session.get(Template, config.template_id) if config else None
        state['worn'].append(dump(worn))
        stock = None
        source = None
        if entry.action == 'warehouse':
            stock = owned(session, WarehouseStack, account_id, entry.warehouse_id)
            check_version(stock.version, entry.expected_stock_version)
            needed[stock.id] += 1
            state['stock'][stock.id] = dump(stock)
            new_config = require(session, EquipmentConfig, stock.config_id)
            template = require(session, Template, new_config.template_id)
            choices = entry.choices or EquipmentChoices.model_validate(choices_for(new_config))
            values = dump(template)
            source = template.source
        elif entry.action in ('edit', 'new'):
            if entry.action == 'edit' and previous is None:
                raise HTTPException(422, f'{dict(SLOTS)[entry.slot]}没有当前装备，不能纠错')
            values, source = template_fields(session, entry.equipment, previous.source_id if previous and entry.action == 'edit' else None)
            choices = entry.choices or (EquipmentChoices.model_validate(choices_for(config)) if entry.action == 'edit' else EquipmentChoices())
        else:
            values, choices = None, None
        if values is not None:
            if values['equipment_type'] != equipment_type(entry.slot):
                raise HTTPException(422, f'{dict(SLOTS)[entry.slot]}装备部位不匹配')
            state['sources'][source.id] = dump(source)
            cycle = require(session, Cycle, source.cycle_id)
            state['cycles'][cycle.id] = dump(cycle)
            applicable = socket_required(values['equipment_type'], cycle.socket_exempt_types)
            if not socket_applicable(values['equipment_type']):
                values['has_constitution_socket'] = False
            if values['equipment_type'] != 'weapon' and choices.rainbow.state != 'unknown':
                raise HTTPException(422, '只有武器可以记录五彩石')
            after_label = f"{label(source.short_label, values['has_constitution_socket'], values['green_stats_ok'], applicable)} · {source.actual_grade} 品"
        else:
            after_label = '清除穿戴'
        if previous:
            state['sources'][previous.source_id] = dump(previous.source)
            cycle = require(session, Cycle, previous.cycle_origin_id)
            state['cycles'][cycle.id] = dump(cycle)
            applicable = socket_required(previous.equipment_type, cycle.socket_exempt_types)
            before_label = f"{label(previous.short_label, previous.has_constitution_socket, previous.green_stats_ok, applicable)} · {previous.actual_grade} 品"
        else:
            before_label = '未装备'
        unchanged = entry.action == 'clear' and previous is None
        if entry.action in ('edit', 'new') and previous:
            previous_values = dump(previous)
            previous_values['has_constitution_socket'] = socket_applicable(previous.equipment_type) and previous.has_constitution_socket
            unchanged = all(previous_values[key] == value for key, value in values.items() if key != 'id') and choices.model_dump() == choices_for(config)
        if not unchanged:
            rows.append((entry, worn, stock, choices, previous))
            changes.append({'slot': entry.slot, 'name': dict(SLOTS)[entry.slot], 'action': entry.action,
                            'before': before_label, 'after': after_label, 'warehouse_id': stock.id if stock else None,
                            'before_equipment': equipment_details(dump(previous), previous.source) if previous else None,
                            'after_equipment': equipment_details(values, source) if source else None,
                            'before_choices': choices_for(config) if config else None,
                            'after_choices': choices.model_dump() if choices else None})
    for stock_id, quantity in needed.items():
        if state['stock'][stock_id]['quantity'] < quantity:
            raise HTTPException(409, f'同一库存需要 {quantity} 件，当前不足；整批未保存')
    return rows, changes, dict(needed), signature(state)


def make_batch_router(engine):
    router = APIRouter(prefix='/api/accounts/{account_id}/characters/{character_id}/equipment-batch')

    def database():
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            yield session

    @router.post('/preview')
    def preview(account_id: str, character_id: str, payload: BatchEquipmentInput, session: Session = Depends(database)):
        _, changes, needed, fingerprint = prepare(session, account_id, character_id, payload)
        if not changes:
            return {'unchanged': True, 'changes': [], 'deductions': {}}
        now = datetime.now(timezone.utc)
        for token in session.scalars(select(DataChangePreview).where(DataChangePreview.kind == 'equipment_batch')):
            if now - datetime.fromisoformat(token.created_at) > timedelta(minutes=30):
                session.delete(token)
        token = DataChangePreview(id=new_id(), kind='equipment_batch', expected_fingerprint=fingerprint,
                                  payload_json=json.dumps({'account_id': account_id, 'character_id': character_id, 'input': payload.model_dump()}, ensure_ascii=False),
                                  created_at=now.isoformat(), result_json=None)
        session.add(token)
        session.commit()
        return {'preview_id': token.id, 'unchanged': False, 'changes': changes, 'deductions': needed}

    @router.post('/apply')
    def apply(account_id: str, character_id: str, payload: BatchEquipmentApply, session: Session = Depends(database)):
        fingerprint, replay = start_operation(session, account_id, 'equipment_batch:' + character_id, payload)
        if replay is not None:
            return replay
        token = require(session, DataChangePreview, payload.preview_id)
        if token.kind != 'equipment_batch':
            raise HTTPException(404, '批量预览不存在')
        data = json.loads(token.payload_json)
        if data['account_id'] != account_id or data['character_id'] != character_id:
            raise HTTPException(404, '批量预览不属于此角色')
        if token.result_json:
            return json.loads(token.result_json)
        if datetime.now(timezone.utc) - datetime.fromisoformat(token.created_at) > timedelta(minutes=30):
            raise HTTPException(409, '批量预览已过期，请返回重新预览')
        entries = BatchEquipmentInput.model_validate(data['input'])
        rows, changes, needed, state = prepare(session, account_id, character_id, entries)
        if state != token.expected_fingerprint:
            raise HTTPException(409, '装备、库存或周期规则已变化，请读取最新状态核对，整批未保存')
        try:
            backup = backup_database(Path(engine.url.database), 'before-equipment-batch')
        except (OSError, sqlite3.Error, RuntimeError):
            raise HTTPException(503, '备份失败，整批未保存；请检查目录权限和空间')
        before, after = [], []
        for entry, worn, stock, choices, previous in rows:
            if entry.action == 'clear':
                config_id = None
            else:
                template = require(session, Template, require(session, EquipmentConfig, stock.config_id).template_id) if stock else create_template(session, entry.equipment, previous.source_id if previous and entry.action == 'edit' else None)
                config_id = create_config(session, template, choices).id
            old, new = replace_worn(session, worn, config_id, entry.expected_version)
            before.append(old)
            after.append(new)
        for stock_id, quantity in needed.items():
            stock = require(session, WarehouseStack, stock_id)
            stock.quantity -= quantity
            stock.version += 1
        result = {'changed': True, 'rows': len(changes), 'backup': backup.name}
        token.result_json = json.dumps(result)
        return finish_operation(session, account_id, 'equipment_batch', character_id, payload, fingerprint,
                                {'worn': before}, {'worn': after, 'deductions': needed, 'backup': backup.name},
                                f"批量更新 {len(changes)} 个部位，仓库扣减 {sum(needed.values())} 件；旧装备不回库", result)

    return router
