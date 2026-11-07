"""Account-local inventory transactions and read-only upgrade candidates."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .domain import socket_applicable, SLOTS, compare, equipment_type, label
from .models import AuditEvent, Character, Cycle, CycleBaseStat, EquipmentConfig, MutationReceipt, RaidDrop, RaidMemoState, Template, WarehouseStack, WornEquipment
from .schemas import DropClear, DropTransfer, EquipmentChoices, InventoryAdd, InventoryEdit, QuantityChange, WarehouseEquip
from .services import (BASE_STAT_MATCH, active_account, choices_for, create_config, create_template, dump,
                       new_id, replace_worn, require, template_snapshot, template_view, writable_slot)


def describe_item(session, row):
    config = require(session, EquipmentConfig, row.config_id)
    template = require(session, Template, config.template_id)
    return item_view(row, config, template_view(session, template))


def item_view(row, config, equipment):
    return {**dump(row), 'equipment': equipment, 'choices': choices_for(config),
            'label': label(equipment['short_label'], equipment['has_constitution_socket'], equipment['green_stats_ok'], equipment['socket_applicable'])}


def inventory_rows(model):
    return (select(model, EquipmentConfig, Template, CycleBaseStat.constitution, Cycle.has_constitution_sockets).select_from(model)
            .join(EquipmentConfig, EquipmentConfig.id == model.config_id)
            .join(Template, Template.id == EquipmentConfig.template_id)
            .join(Cycle, Cycle.id == Template.cycle_origin_id)
            .outerjoin(CycleBaseStat, BASE_STAT_MATCH))


def owned(session, cls, account_id, item_id):
    item = require(session, cls, item_id)
    if item.account_id != account_id:
        raise HTTPException(404, '此账号下没有该记录')
    return item


def check_version(actual, expected):
    if actual != expected:
        raise HTTPException(409, '记录已变化，请刷新后重试，避免覆盖其他页面的操作')


def memo_state(session, account_id):
    state = session.get(RaidMemoState, account_id)
    if state is None:
        state = RaidMemoState(account_id=account_id, version=0)
        session.add(state)
        session.flush()
    return state


def add_stock(session, account_id, config_id, quantity):
    stack = session.scalar(select(WarehouseStack).where(WarehouseStack.account_id == account_id, WarehouseStack.config_id == config_id))
    if stack is None:
        stack = WarehouseStack(id=new_id(), account_id=account_id, config_id=config_id, quantity=0, version=0)
        session.add(stack)
        session.flush()
    before = dump(stack)
    if stack.quantity + quantity > 2147483647:
        raise HTTPException(422, '库存数量超出可记录范围')
    stack.quantity += quantity
    stack.version += 1
    session.flush()
    return stack, before


def add_drop(session, account_id, config_id, quantity):
    note = session.scalar(select(RaidDrop).where(RaidDrop.account_id == account_id, RaidDrop.config_id == config_id))
    if note is None:
        note = RaidDrop(id=new_id(), account_id=account_id, config_id=config_id, quantity=quantity,
                        version=0, created_at=datetime.now(timezone.utc).isoformat())
        session.add(note)
    else:
        if note.quantity + quantity > 2147483647:
            raise HTTPException(422, '掉落合计数量超出可记录范围')
        note.quantity += quantity
        note.version += 1
    session.flush()
    return note


def start_operation(session, account_id, operation, payload):
    active_account(session, account_id)
    fingerprint = hashlib.sha256(json.dumps([operation, payload.model_dump()], sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    receipt = session.get(MutationReceipt, (account_id, payload.request_id))
    if receipt:
        if receipt.fingerprint != fingerprint:
            raise HTTPException(409, '这次操作的编号已被使用，请刷新后重新操作')
        result = json.loads(receipt.result)
        if result.get('_restored_request'):
            raise HTTPException(409, '工作空间已恢复，此旧请求不能再次执行，请刷新后重新操作')
        return fingerprint, result
    return fingerprint, None


def finish_operation(session, account_id, operation, entity_id, payload, fingerprint, before, after, summary, result):
    session.add(AuditEvent(id=new_id(), created_at=datetime.now(timezone.utc).isoformat(), account_id=account_id,
                           operation=operation, entity_id=entity_id,
                           snapshots=json.dumps({'before': before, 'after': after, 'summary': summary}, ensure_ascii=False)))
    session.add(MutationReceipt(account_id=account_id, request_id=payload.request_id, fingerprint=fingerprint,
                                result=json.dumps(result, ensure_ascii=False)))
    session.commit()
    return result


def make_inventory_router(engine, read_database):
    router = APIRouter(prefix='/api/accounts/{account_id}')

    def write_database():
        # 在读取库存前取得写锁，避免 WAL 读事务升级写事务造成忙快照。
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            yield session

    DB = Depends(read_database)
    WRITE = Depends(write_database)

    @router.get('/daily')
    def daily(account_id: str, skip_unconfigured_gaps: bool = False, session: Session = DB):
        account = active_account(session, account_id)
        warehouse = [item_view(row, config, template_snapshot(template, base, available)) for row, config, template, base, available in session.execute(
            inventory_rows(WarehouseStack).where(WarehouseStack.account_id == account_id, WarehouseStack.quantity > 0).order_by(WarehouseStack.id))]
        drops = [item_view(row, config, template_snapshot(template, base, available)) for row, config, template, base, available in session.execute(
            inventory_rows(RaidDrop).where(RaidDrop.account_id == account_id).order_by(RaidDrop.created_at, RaidDrop.id))]
        state = session.get(RaidMemoState, account_id)
        gaps = []
        result = {'warehouse': warehouse, 'drops': drops, 'drop_version': state.version if state else 0, 'gaps': gaps}
        if skip_unconfigured_gaps and account.target_grade is None:
            return result
        characters = list(session.scalars(select(Character).where(Character.account_id == account_id, Character.archived.is_(False)).order_by(Character.sort_order, Character.name, Character.id)))
        worn = {(row.character_id, row.slot): (row, template, available) for row, template, available in session.execute(
            select(WornEquipment, Template, Cycle.has_constitution_sockets).select_from(WornEquipment)
            .join(Character, Character.id == WornEquipment.character_id)
            .outerjoin(EquipmentConfig, EquipmentConfig.id == WornEquipment.config_id)
            .outerjoin(Template, Template.id == EquipmentConfig.template_id)
            .outerjoin(Cycle, Cycle.id == Template.cycle_origin_id)
            .where(Character.account_id == account_id, Character.archived.is_(False)))}

        def rank(grade, socket=False, green=False):
            return (grade,) if account.comparison_mode == 'grade' else (grade, socket, green)

        candidates_by_type = defaultdict(list)
        for item in warehouse:
            equipment = item['equipment']
            candidates_by_type[equipment['equipment_type']].append((
                item['id'], rank(equipment['actual_grade'], equipment['has_constitution_socket'] or not equipment['socket_applicable'], equipment['green_stats_ok']),
            ))

        for character in characters:
            for slot, slot_name in SLOTS:
                record, template, available = worn[(character.id, slot)]
                applicable = socket_applicable(slot, available)
                comparison = compare(template.actual_grade if template else None, template.has_constitution_socket if template else False,
                                 template.green_stats_ok if template else False, account.target_grade, account.comparison_mode, applicable)
                if comparison['status'] == 'met':
                    continue
                current_rank = rank(template.actual_grade, template.has_constitution_socket or not applicable, template.green_stats_ok) if template else rank(-1)
                candidates = [item_id for item_id, candidate_rank in candidates_by_type[equipment_type(slot)] if candidate_rank > current_rank]
                gaps.append({'character_id': character.id, 'character_name': character.name, 'slot': slot, 'slot_name': slot_name,
                             'current_label': label(template.short_label, template.has_constitution_socket, template.green_stats_ok, applicable) if template else '未装备',
                             'actual_grade': template.actual_grade if template else None, 'expected_version': record.version,
                             'target_grade': account.target_grade, **comparison, 'candidate_ids': candidates})
        return result

    @router.post('/warehouse')
    def warehouse_add(account_id: str, payload: InventoryAdd, session: Session = WRITE):
        fingerprint, replay = start_operation(session, account_id, 'warehouse_add', payload)
        if replay is not None:
            return replay
        template = create_template(session, payload.equipment)
        config = create_config(session, template, payload.choices)
        stack, before = add_stock(session, account_id, config.id, payload.quantity)
        item = describe_item(session, stack)
        return finish_operation(session, account_id, 'warehouse_add', stack.id, payload, fingerprint, before, dump(stack),
                                f"{item['label']} · {template.actual_grade}品：仓库 {before['quantity']} → {stack.quantity} 件", item)

    @router.post('/warehouse/{stack_id}/quantity')
    def warehouse_quantity(account_id: str, stack_id: str, payload: QuantityChange, session: Session = WRITE):
        fingerprint, replay = start_operation(session, account_id, f'warehouse_quantity:{stack_id}', payload)
        if replay is not None:
            return replay
        stack = owned(session, WarehouseStack, account_id, stack_id)
        check_version(stack.version, payload.expected_version)
        before = dump(stack)
        stack.quantity = payload.quantity
        stack.version += 1
        item = describe_item(session, stack)
        return finish_operation(session, account_id, 'warehouse_quantity', stack.id, payload, fingerprint, before, dump(stack),
                                f"{item['label']} · {item['equipment']['actual_grade']}品：仓库 {before['quantity']} → {stack.quantity} 件", item)

    @router.post('/{target}/{item_id}/edit')
    def edit_inventory(account_id: str, target: str, item_id: str, payload: InventoryEdit, session: Session = WRITE):
        if target not in ('warehouse', 'drops'):
            raise HTTPException(404, '记录不存在')
        fingerprint, replay = start_operation(session, account_id, f'{target}_edit:{item_id}', payload)
        if replay is not None:
            return replay
        model = WarehouseStack if target == 'warehouse' else RaidDrop
        item = owned(session, model, account_id, item_id)
        check_version(item.version, payload.expected_version)
        if payload.quantity > item.quantity or item.quantity < 1:
            raise HTTPException(409, '更正件数超过当前数量，请核对最新记录')
        before = dump(item)
        template = create_template(session, payload.equipment)
        config = create_config(session, template, payload.choices)
        if config.id == item.config_id:
            result = describe_item(session, item)
            session.add(MutationReceipt(account_id=account_id, request_id=payload.request_id,
                                        fingerprint=fingerprint, result=json.dumps(result, ensure_ascii=False)))
            session.commit()
            return result
        item.quantity -= payload.quantity
        item.version += 1
        if target == 'drops' and not item.quantity:
            session.delete(item)
        session.flush()
        if target == 'warehouse':
            changed, _ = add_stock(session, account_id, config.id, payload.quantity)
        else:
            changed = add_drop(session, account_id, config.id, payload.quantity)
            memo_state(session, account_id).version += 1
        result = describe_item(session, changed)
        return finish_operation(session, account_id, f'{target}_edit', item_id, payload, fingerprint,
                                before, {'remaining': None if target == 'drops' and item.quantity == 0 else dump(item),
                                         'corrected': dump(changed)},
                                f"更正 {payload.quantity} 件装备配置；其他记录和角色穿戴保持原样", result)

    @router.post('/warehouse/{stack_id}/equip')
    def warehouse_equip(account_id: str, stack_id: str, payload: WarehouseEquip, session: Session = WRITE):
        fingerprint, replay = start_operation(session, account_id, f'warehouse_equip:{stack_id}', payload)
        if replay is not None:
            return replay
        stack = owned(session, WarehouseStack, account_id, stack_id)
        check_version(stack.version, payload.expected_version)
        if stack.quantity < 1:
            raise HTTPException(409, '这件装备已没有可用库存，请刷新')
        worn = writable_slot(session, account_id, payload.character_id, payload.slot)
        config = require(session, EquipmentConfig, stack.config_id)
        template = require(session, Template, config.template_id)
        if template.equipment_type != equipment_type(payload.slot):
            raise HTTPException(422, '装备与角色部位不匹配')
        # 只修改取出的这一件，不改仓库余量的配置，也不继承旧穿戴附魔。
        new_config = create_config(session, template, payload.choices or EquipmentChoices.model_validate(choices_for(config)))
        before_stock = dump(stack)
        before_worn, after_worn = replace_worn(session, worn, new_config.id, payload.expected_equipment_version)
        stack.quantity -= 1
        stack.version += 1
        character = require(session, Character, payload.character_id)
        item = describe_item(session, stack)
        slot_name = dict(SLOTS)[payload.slot]
        return finish_operation(session, account_id, 'warehouse_equip', stack.id, payload, fingerprint,
                                {'warehouse': before_stock, 'worn': before_worn}, {'warehouse': dump(stack), 'worn': after_worn},
                                f"{item['label']} · {template.actual_grade}品 → {character.name}的{slot_name}；仓库 {before_stock['quantity']} → {stack.quantity} 件；旧装备不回库",
                                {'quantity': stack.quantity, 'version': stack.version, 'equipment_version': worn.version})

    @router.post('/drops')
    def drop_add(account_id: str, payload: InventoryAdd, session: Session = WRITE):
        fingerprint, replay = start_operation(session, account_id, 'drop_add', payload)
        if replay is not None:
            return replay
        template = create_template(session, payload.equipment)
        config = create_config(session, template, payload.choices)
        note = add_drop(session, account_id, config.id, payload.quantity)
        memo_state(session, account_id).version += 1
        item = describe_item(session, note)
        return finish_operation(session, account_id, 'drop_add', note.id, payload, fingerprint, None, dump(note),
                                f"记录掉落：{item['label']} · {template.actual_grade}品 × {note.quantity}；尚未计入库存", item)

    @router.post('/drops/{note_id}/quantity')
    def drop_quantity(account_id: str, note_id: str, payload: QuantityChange, session: Session = WRITE):
        fingerprint, replay = start_operation(session, account_id, f'drop_quantity:{note_id}', payload)
        if replay is not None:
            return replay
        note = owned(session, RaidDrop, account_id, note_id)
        check_version(note.version, payload.expected_version)
        before = dump(note)
        memo_state(session, account_id).version += 1
        if payload.quantity == 0:
            session.delete(note)
            after = None
        else:
            note.quantity = payload.quantity
            note.version += 1
            after = dump(note)
        return finish_operation(session, account_id, 'drop_quantity', note.id, payload, fingerprint, before, after,
                                f"掉落数量 {before['quantity']} → {payload.quantity}；仓库不变", {'quantity': payload.quantity})

    @router.post('/drops/{note_id}/transfer')
    def drop_transfer(account_id: str, note_id: str, payload: DropTransfer, session: Session = WRITE):
        fingerprint, replay = start_operation(session, account_id, f'drop_transfer:{note_id}', payload)
        if replay is not None:
            return replay
        note = owned(session, RaidDrop, account_id, note_id)
        check_version(note.version, payload.expected_version)
        if payload.quantity > note.quantity:
            raise HTTPException(409, '转入数量超过当前掉落记录，请刷新检查')
        before_note = dump(note)
        item = describe_item(session, note)
        stack, before_stock = add_stock(session, account_id, note.config_id, payload.quantity)
        note.quantity -= payload.quantity
        note.version += 1
        after_note = dump(note) if note.quantity else None
        if not note.quantity:
            session.delete(note)
        memo_state(session, account_id).version += 1
        return finish_operation(session, account_id, 'drop_transfer', note.id, payload, fingerprint,
                                {'drop': before_note, 'warehouse': before_stock}, {'drop': after_note, 'warehouse': dump(stack)},
                                f"确认获得 {item['label']} · {item['equipment']['actual_grade']}品 × {payload.quantity}；转入仓库，扣减对应掉落数量",
                                {'warehouse_id': stack.id, 'quantity': stack.quantity, 'remaining_drop': note.quantity})

    @router.post('/drops/clear')
    def drop_clear(account_id: str, payload: DropClear, session: Session = WRITE):
        fingerprint, replay = start_operation(session, account_id, 'drop_clear', payload)
        if replay is not None:
            return replay
        state = memo_state(session, account_id)
        check_version(state.version, payload.expected_version)
        before = [dump(row) for row in session.scalars(select(RaidDrop).where(RaidDrop.account_id == account_id))]
        session.execute(delete(RaidDrop).where(RaidDrop.account_id == account_id))
        state.version += 1
        return finish_operation(session, account_id, 'drop_clear', account_id, payload, fingerprint, before, [],
                                f'清空本账号的 {len(before)} 条掉落备忘；共享仓库不变', {'cleared': len(before), 'version': state.version})

    @router.get('/history')
    def history(account_id: str, session: Session = DB):
        active_account(session, account_id)
        titles = {'warehouse_add': '添加共享库存', 'warehouse_quantity': '修正仓库数量', 'warehouse_equip': '从仓库穿戴', 'warehouse_edit': '更正共享库存', 'drops_edit': '更正掉落配置', 'drop_add': '记录团本掉落', 'drop_quantity': '修改掉落数量', 'drop_transfer': '掉落转入仓库', 'drop_clear': '清空掉落', 'replace_equipment': '修改角色穿戴', 'character_growth': '修改角色养成', 'equipment_batch': '批量录入角色装备', 'drop_merge_migration': '归并相同掉落'}
        rows = list(session.scalars(select(AuditEvent).where(AuditEvent.account_id == account_id).order_by(AuditEvent.created_at.desc(), AuditEvent.id).limit(50)))
        cycle_sockets = dict(session.execute(select(Cycle.id, Cycle.has_constitution_sockets)).all())
        snapshots = {row.id: json.loads(row.snapshots) for row in rows}
        config_ids = set()
        for row in rows:
            data = snapshots[row.id]
            if row.operation in ('replace_equipment', 'warehouse_equip'):
                for side in ('before', 'after'):
                    worn = data[side]['worn'] if row.operation == 'warehouse_equip' else data[side]
                    if worn.get('config_id'):
                        config_ids.add(worn['config_id'])
        equipment_records = {config.id: (config, template) for config, template in session.execute(
            select(EquipmentConfig, Template).join(Template, Template.id == EquipmentConfig.template_id)
            .where(EquipmentConfig.id.in_(config_ids)))} if config_ids else {}
        names = dict(session.execute(select(Character.id, Character.name).where(Character.account_id == account_id)).all())
        result = []
        def equipment_name(config_id):
            if not config_id:
                return '未装备'
            pair = equipment_records.get(config_id)
            if pair is None:
                return '历史装备'
            config, template = pair
            applicable = socket_applicable(template.equipment_type, cycle_sockets[template.cycle_origin_id])
            parts = []
            for prefix, title in [('enchant', '附魔'), ('rainbow', '五彩石')]:
                state = getattr(config, f'{prefix}_state')
                if state == 'not_applicable':
                    continue
                name = getattr(config, f'{prefix}_name')
                value = getattr(config, f'{prefix}_constitution')
                text = '未录入' if state == 'unknown' else '确认无' if state == 'none' else (name or '手填数值') + ('（按名称查原周期配置）' if value is None else f'（体质{value}）')
                parts.append(f'{title}{text}')
            return f"{label(template.short_label, template.has_constitution_socket, template.green_stats_ok, applicable)} · {template.actual_grade}品（{'，'.join(parts)}）"
        for row in rows:
            snapshot = snapshots[row.id]
            summary = snapshot.get('summary', '')
            if row.operation == 'replace_equipment':
                before, after = snapshot['before'], snapshot['after']
                name = names.get(before['character_id'], '历史角色')
                summary = f"{name}的{dict(SLOTS)[before['slot']]}：{equipment_name(before['config_id'])} → {equipment_name(after['config_id'])}；旧装备不回库"
            elif row.operation == 'character_growth':
                summary = f'{names.get(row.entity_id, "历史角色")}：更新部位精炼、五行石或体质计算口径；穿戴与库存不变'
            elif row.operation == 'warehouse_equip':
                summary += f"；穿戴：{equipment_name(snapshot['before']['worn']['config_id'])} → {equipment_name(snapshot['after']['worn']['config_id'])}"
            if row.operation in titles:
                result.append({'id': row.id, 'created_at': row.created_at, 'title': titles[row.operation], 'summary': summary})
        return result

    return router
