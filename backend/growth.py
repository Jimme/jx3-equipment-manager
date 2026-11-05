"""Character-owned growth and conservative, explainable constitution calculations."""
import hashlib
import json
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import SLOTS
from .models import (Account, Character, CharacterCalculation, Cycle, CycleBuffThreshold,
                     CycleEnchantStat, CycleRainbowStat, CycleRefineStat, CycleStoneStat,
                     EquipmentConfig, SlotSetting, Template, Workspace, WornEquipment)
from .schemas import GrowthInput
from .services import audit, dump, require, template_view


def thresholds_for(session, cycle_id):
    return [{'skill': r.skill, 'stacks': r.stacks, 'threshold': r.threshold} for r in session.scalars(
        select(CycleBuffThreshold).where(CycleBuffThreshold.cycle_id == cycle_id)
        .order_by(CycleBuffThreshold.skill, CycleBuffThreshold.stacks))] if cycle_id else []


def threshold_signature(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def component(value=None, source='', applicable=True):
    return {'value': value, 'status': 'not_applicable' if not applicable else 'unknown' if value is None else 'known', 'source': source}


def attachment(session, template, config, prefix):
    state = getattr(config, prefix + '_state')
    if state == 'not_applicable':
        return component(applicable=False)
    if state == 'none':
        return component(0, '确认没有')
    if state == 'unknown':
        return component(source='装备尚未录入')
    override = getattr(config, prefix + '_constitution')
    if override is not None:
        return component(override, '装备手填覆盖')
    name = getattr(config, prefix + '_name')
    model, key = (CycleEnchantStat, (template.cycle_origin_id, template.equipment_type, name)) if prefix == 'enchant' else (CycleRainbowStat, (template.cycle_origin_id, name))
    stat = session.get(model, key)
    return component(stat.constitution if stat else None, f'原周期配置：{name}')


def rule_error(rows):
    positive = [r for r in rows if r['stacks'] > 0]
    if not positive or any(r['threshold'] is None for r in rows):
        return '阈值未录完整'
    if any(r['stacks'] != index for index, r in enumerate(positive, 1)):
        return '请从 1 层起连续配置阈值'
    if any(r['stacks'] == 0 and r['threshold'] != 0 for r in rows):
        return '若配置 0 层，其最低阈值必须为 0'
    if any(a['threshold'] > b['threshold'] for a, b in zip(rows, rows[1:])):
        return '阈值必须随层数递增或相等'
    return ''


def growth_report(session, character):
    workspace = session.get(Workspace, 1)
    cycle = session.get(Cycle, workspace.active_cycle_id) if workspace.active_cycle_id else None
    thresholds = thresholds_for(session, cycle.id if cycle else None)
    calculation = require(session, CharacterCalculation, character.id)
    confirmation_valid = (calculation.scope_confirmed and cycle is not None and
                          calculation.threshold_cycle_id == cycle.id and
                          calculation.threshold_signature == threshold_signature(thresholds))
    settings = {r.slot: r for r in session.scalars(select(SlotSetting).where(SlotSetting.character_id == character.id))}
    records = {w.slot: (w, t, config) for w, t, config in session.execute(
        select(WornEquipment, Template, EquipmentConfig)
        .outerjoin(EquipmentConfig, EquipmentConfig.id == WornEquipment.config_id)
        .outerjoin(Template, Template.id == EquipmentConfig.template_id)
        .where(WornEquipment.character_id == character.id))}
    rows, missing, known = [], [], 0
    titles = {'base': '基础体质', 'refine': '精炼加成', 'stone': '五行石', 'enchant': '附魔', 'rainbow': '五彩石'}
    cycles = {r.id: r.name for r in session.scalars(select(Cycle))}
    for slot, name in SLOTS:
        worn, template, config = records[slot]
        setting = settings[slot]
        if not template:
            parts = {key: component(applicable=False) for key in titles}
            missing.append(f'{name}：未装备')
        else:
            base = template_view(session, template)['base_stat']
            parts = {'base': component(base['value'], '单件覆盖' if base['source'] == 'override' else '原周期默认')}
            refine = session.get(CycleRefineStat, (template.cycle_origin_id, template.actual_grade, template.equipment_type, setting.refine_level)) if setting.refine_level is not None else None
            parts['refine'] = component(refine.constitution if refine else None, '精炼等级未录入' if setting.refine_level is None else f'原周期 {template.actual_grade}品 / 精炼{setting.refine_level}')
            if not template.has_constitution_socket:
                parts['stone'] = component(applicable=False, source='装备无体质孔，等级配置仍保留')
            elif setting.stone_state == 'none':
                parts['stone'] = component(0, '明确未镶嵌')
            elif setting.stone_state == 'unknown':
                parts['stone'] = component(source='镶嵌状态未录入')
            else:
                stat = session.get(CycleStoneStat, (template.cycle_origin_id, setting.stone_level))
                parts['stone'] = component(stat.constitution if stat else None, f'原周期 五行石{setting.stone_level}级')
            parts['enchant'] = attachment(session, template, config, 'enchant')
            parts['rainbow'] = attachment(session, template, config, 'rainbow') if slot == 'weapon' else component(applicable=False)
            for key, part in parts.items():
                if part['status'] == 'unknown':
                    missing.append(f'{name}：{titles[key]}未完善（{part["source"]}）')
                elif part['status'] == 'known':
                    known += part['value']
        rows.append({'slot': slot, 'name': name, 'settings': dump(setting), 'equipped': template is not None,
                     'grade': template.actual_grade if template else None,
                     'cycle_name': cycles[template.cycle_origin_id] if template else None,
                     'components': parts})
    equipment_total = known if not missing else None
    verified_total = equipment_total + calculation.additional_constitution if equipment_total is not None and calculation.additional_constitution is not None and confirmation_valid else None
    if not confirmation_valid:
        gate = '计算口径或当前周期阈值尚未核实；已核实后修改阈值或切换周期也需重新确认'
    elif calculation.additional_constitution is None:
        gate = '角色额外修正未录入'
    elif missing:
        gate = '装备体质数据未完善'
    else:
        gate = ''
    groups = defaultdict(list)
    for row in thresholds:
        groups[row['skill']].append(row)
    buffs = []
    for skill, rules in groups.items():
        reason = rule_error(rules) or gate
        result = {'skill': skill, 'stacks': None, 'next_stacks': None, 'next_threshold': None, 'difference': None, 'configured_max': max(r['stacks'] for r in rules), 'message': reason}
        if not reason:
            reached = [r['stacks'] for r in rules if verified_total >= r['threshold']]
            result['stacks'] = max(reached, default=0)
            following = next((r for r in rules if r['stacks'] > result['stacks']), None)
            if following:
                result.update(next_stacks=following['stacks'], next_threshold=following['threshold'], difference=following['threshold'] - verified_total)
            else:
                result['message'] = '已达到当前配置的最高层；不代表游戏层数上限'
        buffs.append(result)
    return {'character': dump(character), 'cycle': dump(cycle) if cycle else None, 'slots': rows,
            'calculation': {**dump(calculation), 'confirmation_valid': bool(confirmation_valid)},
            'known_subtotal': known, 'equipment_total': equipment_total, 'verified_total': verified_total,
            'missing': missing, 'buffs': buffs, 'buff_message': gate or ('当前周期尚未配置技能阈值' if not buffs else '')}


def make_growth_router(engine, read_database):
    router = APIRouter(prefix='/api/accounts/{account_id}/characters/{character_id}')

    def write_database():
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            yield session

    def character_for(session, account_id, character_id, write=False):
        account = require(session, Account, account_id)
        character = require(session, Character, character_id)
        if character.account_id != account_id:
            raise HTTPException(404, '此账号下没有该角色')
        if write and (account.archived or character.archived):
            raise HTTPException(409, '请先恢复账号和角色')
        return character

    @router.get('/growth')
    def get_growth(account_id: str, character_id: str, session: Session = Depends(read_database)):
        return growth_report(session, character_for(session, account_id, character_id))

    @router.put('/growth')
    def save_growth(account_id: str, character_id: str, payload: GrowthInput, session: Session = Depends(write_database)):
        character = character_for(session, account_id, character_id, True)
        current_cycle_id = session.get(Workspace, 1).active_cycle_id
        cycle = session.get(Cycle, current_cycle_id) if current_cycle_id else None
        if current_cycle_id != payload.expected_cycle_id or (cycle.version if cycle else None) != payload.expected_cycle_version:
            raise HTTPException(409, '当前周期或配置已变化，请重新读取后再保存养成')
        calculation = require(session, CharacterCalculation, character_id)
        if calculation.version != payload.expected_version:
            raise HTTPException(409, '角色计算设置已变化，请重新读取后再保存')
        settings = {r.slot: r for r in session.scalars(select(SlotSetting).where(SlotSetting.character_id == character_id))}
        if any(settings[r.slot].version != r.expected_version for r in payload.slots):
            raise HTTPException(409, '部位养成已在其他页面修改，请重新读取后再保存')
        before = {'slots': [dump(s) for s in settings.values()], 'calculation': dump(calculation)}
        changed = False
        for row in payload.slots:
            setting = settings[row.slot]
            values = row.model_dump(exclude={'slot', 'expected_version'})
            if any(getattr(setting, k) != v for k, v in values.items()):
                for key, value in values.items():
                    setattr(setting, key, value)
                setting.version += 1
                changed = True
        values = {'additional_constitution': payload.additional_constitution, 'scope_confirmed': payload.scope_confirmed,
                  'threshold_cycle_id': current_cycle_id if payload.scope_confirmed else None,
                  'threshold_signature': threshold_signature(thresholds_for(session, current_cycle_id)) if payload.scope_confirmed else ''}
        if any(getattr(calculation, k) != v for k, v in values.items()):
            for key, value in values.items():
                setattr(calculation, key, value)
            calculation.version += 1
            changed = True
        if changed:
            audit(session, 'character_growth', character_id, before,
                  {'slots': [dump(s) for s in settings.values()], 'calculation': dump(calculation)}, account_id)
        session.commit()
        return growth_report(session, character)

    return router
