import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, select, update

from .domain import SLOT_KEYS, equipment_type, socket_applicable
from .models import Account, AuditEvent, Character, Cycle, CycleBaseStat, EquipmentConfig, Source, Template, WornEquipment
from .schemas import EquipmentChoices, TemplateInput

BASE_STAT_MATCH = and_(
    CycleBaseStat.cycle_id == Template.cycle_origin_id,
    CycleBaseStat.actual_grade == Template.actual_grade,
    CycleBaseStat.equipment_type == Template.equipment_type,
)


def new_id():
    return str(uuid4())


def dump(entity):
    return {column.name: getattr(entity, column.name) for column in entity.__table__.columns}


def require(session, cls, entity_id):
    entity = session.get(cls, entity_id)
    if entity is None:
        raise HTTPException(404, "记录不存在，请刷新后重试")
    return entity


def audit(session, operation, entity_id, before, after, account_id=None):
    session.add(AuditEvent(
        id=new_id(), created_at=datetime.now(timezone.utc).isoformat(), account_id=account_id,
        operation=operation, entity_id=entity_id,
        snapshots=json.dumps({"before": before, "after": after}, ensure_ascii=False),
    ))


def create_template(session, payload: TemplateInput):
    cycle = require(session, Cycle, payload.cycle_origin_id)
    values = payload.model_dump()
    if not socket_applicable(payload.equipment_type, cycle.has_constitution_sockets):
        values['has_constitution_socket'] = False
    if payload.source_id:
        source = require(session, Source, payload.source_id)
        if source.cycle_id != payload.cycle_origin_id:
            raise HTTPException(422, "来源不属于装备所选周期")
        values.update(source_type=source.source_type, source_name=source.name, short_label=source.short_label)
        if values["actual_grade"] is None:
            values["actual_grade"] = source.default_grade
    if values["actual_grade"] is None:
        raise HTTPException(422, "请填写真实品级，或先设置来源默认品级")
    # 来源 ID 只是预设链接；同一来源快照从已有模板复制时也应复用模板。
    identity = {key: value for key, value in values.items() if key != 'source_id'}
    existing = session.scalar(select(Template).filter_by(**identity).order_by(Template.id).limit(1))
    if existing:
        return existing
    template = Template(id=new_id(), **values)
    session.add(template)
    session.flush()
    return template


def choices_for(config):
    result = {}
    for prefix in ('enchant', 'rainbow'):
        state = getattr(config, f'{prefix}_state')
        result[prefix] = {'state': 'unknown' if state == 'not_applicable' else state,
                          'name': getattr(config, f'{prefix}_name'),
                          'constitution': getattr(config, f'{prefix}_constitution')}
    return result


def template_view(session, template):
    value = None
    if template.base_constitution_override is None:
        row = session.get(CycleBaseStat, (template.cycle_origin_id, template.actual_grade, template.equipment_type))
        value = row.constitution if row else None
    cycle = require(session, Cycle, template.cycle_origin_id)
    return template_snapshot(template, value, cycle.has_constitution_sockets)


def template_snapshot(template, default_constitution, socket_available):
    override = template.base_constitution_override
    value = default_constitution if override is None else override
    stat = {'value': value, 'status': 'unknown' if value is None else 'known',
            'source': 'cycle' if override is None else 'override'}
    applicable = socket_applicable(template.equipment_type, socket_available)
    return {**dump(template), 'base_stat': stat, 'socket_applicable': applicable,
            'has_constitution_socket': applicable and template.has_constitution_socket}


def create_config(session, template, choices: EquipmentChoices | None = None):
    choices = choices or EquipmentChoices()
    if template.equipment_type != 'weapon' and choices.rainbow.state != 'unknown':
        raise HTTPException(422, '只有武器可以记录五彩石')
    values = {'template_id': template.id}
    for prefix in ('enchant', 'rainbow'):
        choice = getattr(choices, prefix)
        values.update({f'{prefix}_state': choice.state, f'{prefix}_name': choice.name,
                       f'{prefix}_constitution': choice.constitution})
    if template.equipment_type != 'weapon':
        values['rainbow_state'] = 'not_applicable'
    key = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    config = session.scalar(select(EquipmentConfig).where(EquipmentConfig.config_key == key))
    if config is None:
        config = EquipmentConfig(id=new_id(), config_key=key, **values)
        session.add(config)
        session.flush()
    return config


def active_account(session, account_id):
    account = require(session, Account, account_id)
    if account.archived:
        raise HTTPException(409, '请先恢复账号')
    return account


def writable_slot(session, account_id, character_id, slot):
    active_account(session, account_id)
    character = require(session, Character, character_id)
    if character.account_id != account_id:
        raise HTTPException(404, '此账号下没有该角色')
    if character.archived:
        raise HTTPException(409, '请先恢复角色')
    if slot not in SLOT_KEYS:
        raise HTTPException(422, '装备部位无效')
    return require(session, WornEquipment, (character_id, slot))


def replace_worn(session, worn, config_id, expected_version):
    before = dump(worn)
    result = session.execute(update(WornEquipment).where(
        WornEquipment.character_id == worn.character_id, WornEquipment.slot == worn.slot,
        WornEquipment.version == expected_version,
    ).values(config_id=config_id, version=WornEquipment.version + 1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(409, '此格装备已在其他页面修改，请关闭编辑并刷新后重试')
    session.expire(worn)
    return before, dump(worn)


