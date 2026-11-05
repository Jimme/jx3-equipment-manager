"""Cycle configuration with preview, version checks and consistent backups."""
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import backup_database
from .domain import SLOTS
from .models import (Account, Character, Cycle, CycleBaseStat, CycleBuffThreshold, CycleChangePreview,
                     CycleEnchantStat, CycleRainbowStat, CycleRefineStat, CycleStoneStat,
                     EquipmentConfig, RaidDrop, Source, Template, WarehouseStack, Workspace, WornEquipment)
from .schemas import (BaseStatInput, BuffThresholdInput, CycleArchiveInput, CycleCopyInput,
                      CycleCsvPreviewInput, CycleTablePreviewInput, EnchantStatInput,
                      RainbowStatInput, RefineStatInput, StoneStatInput)
from .services import audit, dump, new_id, require

SPECS = {
    'base': (CycleBaseStat, BaseStatInput, ('actual_grade', 'equipment_type'), ('actual_grade', 'equipment_type', 'constitution')),
    'refine': (CycleRefineStat, RefineStatInput, ('actual_grade', 'equipment_type', 'refine_level'), ('actual_grade', 'equipment_type', 'refine_level', 'constitution')),
    'stone': (CycleStoneStat, StoneStatInput, ('stone_level',), ('stone_level', 'constitution')),
    'enchant': (CycleEnchantStat, EnchantStatInput, ('equipment_type', 'name'), ('equipment_type', 'name', 'constitution')),
    'rainbow': (CycleRainbowStat, RainbowStatInput, ('name',), ('name', 'constitution')),
    'buff': (CycleBuffThreshold, BuffThresholdInput, ('skill', 'stacks'), ('skill', 'stacks', 'threshold')),
}
HEADERS = {'actual_grade': '真实品级', 'equipment_type': '部位', 'constitution': '体质', 'refine_level': '精炼等级', 'stone_level': '五行石等级', 'name': '名称', 'skill': '技能名称', 'stacks': '层数', 'threshold': '体质阈值'}
TYPE_NAMES = {**{key: name for key, name in SLOTS if not key.startswith('ring')}, 'ring': '戒指'}
TYPE_KEYS = {name: key for key, name in TYPE_NAMES.items()}


def spec_for(kind):
    if kind not in SPECS:
        raise HTTPException(404, '数值表不存在')
    return SPECS[kind]


def rows_for(session, cycle_id, kind):
    model, _, keys, columns = spec_for(kind)
    return [{key: getattr(row, key) for key in columns} for row in session.scalars(select(model).where(model.cycle_id == cycle_id).order_by(*(getattr(model, key) for key in keys)))]


def validate_rows(kind, rows, line_numbers=None):
    _, schema, keys, _ = spec_for(kind)
    if len(rows) > 5000:
        raise HTTPException(422, '单次数值表最多 5000 行')
    result, seen, errors = [], set(), []
    for index, raw in enumerate(rows):
        line = line_numbers[index] if line_numbers else index + 1
        try:
            row = schema.model_validate(raw).model_dump()
            key = tuple(row[field] for field in keys)
            if key in seen:
                errors.append(f'第{line}行：相同查询维度重复，请合并后导入')
            seen.add(key)
            result.append(row)
        except ValidationError as error:
            for item in error.errors():
                field = str(item['loc'][0]) if item['loc'] else ''
                messages = {'int_type': '请输入整数', 'missing': '必填项未填写', 'literal_error': '部位无效，请使用本表提供的部位名称', 'string_too_short': '请填写名称', 'string_too_long': '名称最多 100 个字符', 'extra_forbidden': '出现多余字段，请检查表头', 'greater_than': '必须大于 0', 'greater_than_equal': '不能小于 0', 'less_than_equal': '数值超出允许范围'}
                message = messages.get(item['type'], item['msg'].removeprefix('Value error, '))
                errors.append(f"第{line}行【{HEADERS.get(field, field)}】：{message}")
    if not errors and kind == 'buff':
        for skill in {r['skill'] for r in result}:
            known = sorted((r for r in result if r['skill'] == skill and r['threshold'] is not None), key=lambda r: r['stacks'])
            for lower, higher in zip(known, known[1:]):
                if lower['threshold'] > higher['threshold']:
                    index = result.index(higher)
                    line = line_numbers[index] if line_numbers else index + 1
                    errors.append(f'第{line}行【体质阈值】：技能“{skill}”高层阈值不能低于低层；未知项可以留空')
    if errors:
        raise HTTPException(422, '\n'.join(errors[:30]))
    return sorted(result, key=lambda row: tuple(row[key] for key in keys))


def parse_csv(kind, content):
    _, _, _, columns = spec_for(kind)
    if '\ufffd' in content:
        raise HTTPException(422, 'CSV 编码无法识别，请另存为 CSV UTF-8 后导入')
    reader = csv.reader(io.StringIO(content.removeprefix('\ufeff')), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        raise HTTPException(422, 'CSV 缺少表头，请先导出本表的 CSV')
    reverse = {value: key for key, value in HEADERS.items()}
    fields = [reverse.get(value.strip(), value.strip()) for value in header]
    if len(set(fields)) != len(fields) or set(fields) != set(columns):
        raise HTTPException(422, 'CSV 表头应为：' + '、'.join(HEADERS[c] for c in columns))
    rows, lines, errors = [], [], []
    try:
        while True:
            line = reader.line_num + 1
            try:
                values = next(reader)
            except StopIteration:
                break
            if not any(value.strip() for value in values):
                continue
            if len(values) != len(fields):
                errors.append(f'第{line}行：列数与表头不一致')
                continue
            row = dict(zip(fields, (value.strip() for value in values)))
            if 'equipment_type' in row:
                row['equipment_type'] = TYPE_KEYS.get(row['equipment_type'], row['equipment_type'])
            for field in fields:
                if field in ('actual_grade', 'constitution', 'refine_level', 'stone_level', 'stacks', 'threshold'):
                    raw = row[field]
                    if raw == '' and field in ('constitution', 'threshold'):
                        row[field] = None
                    elif raw.isascii() and raw.isdecimal() and len(raw) <= 10:
                        row[field] = int(raw)
                    else:
                        errors.append(f'第{line}行【{HEADERS[field]}】：请填写非负整数' + ('，未知可留空' if field in ('constitution', 'threshold') else ''))
            rows.append(row)
            lines.append(line)
            if len(rows) > 5000:
                raise HTTPException(422, '单次数值表最多 5000 行')
    except csv.Error as error:
        raise HTTPException(422, f'第{reader.line_num}行：CSV 格式错误，{error}')
    if errors:
        raise HTTPException(422, '\n'.join(errors[:30]))
    return validate_rows(kind, rows, lines)


def differences(kind, before, after):
    keys = spec_for(kind)[2]
    key_for = lambda row: tuple(row[k] for k in keys)
    old, new = {key_for(r): r for r in before}, {key_for(r): r for r in after}
    result = []
    for key in sorted(old.keys() | new.keys(), key=str):
        if old.get(key) != new.get(key):
            result.append({'action': 'added' if key not in old else 'deleted' if key not in new else 'changed', 'before': old.get(key), 'after': new.get(key)})
    return result


def impact_for(session, cycle_id, kind, changes):
    if kind != 'base':
        return {'worn': [], 'warehouse': [], 'drops': [], 'worn_count': 0, 'warehouse_count': 0, 'drop_count': 0, 'message': '阈值变更会使该周期的口径确认失效，需重新核实；其他数值变更会重新计算使用本周期配置的角色贡献。不改变角色等级、穿戴或库存。'}
    affected = {}
    for change in changes:
        row = change['after'] or change['before']
        before = change['before']['constitution'] if change['before'] else None
        after = change['after']['constitution'] if change['after'] else None
        if before != after:
            affected[(row['actual_grade'], row['equipment_type'])] = (before, after)
    templates = {t.id: t for t in session.scalars(select(Template).where(Template.cycle_origin_id == cycle_id, Template.base_constitution_override.is_(None))) if (t.actual_grade, t.equipment_type) in affected}
    worn, warehouse, drops = [], [], []
    if templates:
        records = session.execute(select(WornEquipment, Character, EquipmentConfig).join(Character, Character.id == WornEquipment.character_id).join(EquipmentConfig, EquipmentConfig.id == WornEquipment.config_id).where(EquipmentConfig.template_id.in_(templates))).all()
        for item, character, config in records:
            template = templates[config.template_id]
            before, after = affected[(template.actual_grade, template.equipment_type)]
            worn.append({'character': character.name, 'slot': dict(SLOTS)[item.slot], 'grade': template.actual_grade, 'before': before, 'after': after})
        records = session.execute(select(WarehouseStack, Account, EquipmentConfig).join(Account, Account.id == WarehouseStack.account_id).join(EquipmentConfig, EquipmentConfig.id == WarehouseStack.config_id).where(WarehouseStack.quantity > 0, EquipmentConfig.template_id.in_(templates))).all()
        for item, account, config in records:
            template = templates[config.template_id]
            before, after = affected[(template.actual_grade, template.equipment_type)]
            warehouse.append({'account': account.name, 'slot': TYPE_NAMES[template.equipment_type], 'grade': template.actual_grade, 'quantity': item.quantity, 'before': before, 'after': after})
        records = session.execute(select(RaidDrop, Account, EquipmentConfig).join(Account, Account.id == RaidDrop.account_id).join(EquipmentConfig, EquipmentConfig.id == RaidDrop.config_id).where(EquipmentConfig.template_id.in_(templates))).all()
        for item, account, config in records:
            template = templates[config.template_id]
            before, after = affected[(template.actual_grade, template.equipment_type)]
            drops.append({'account': account.name, 'slot': TYPE_NAMES[template.equipment_type], 'grade': template.actual_grade, 'quantity': item.quantity, 'before': before, 'after': after})
    return {'worn': worn[:30], 'warehouse': warehouse[:30], 'drops': drops[:30], 'worn_count': len(worn), 'warehouse_count': len(warehouse), 'drop_count': len(drops), 'message': '只改变同周期、同查询维度的默认基础体质，不改真实品级、模板、穿戴或数量；单件覆盖值优先。'}


def make_cycle_data_router(engine, read_database):
    router = APIRouter(prefix='/api/cycles')

    def write_database():
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            yield session

    READ, WRITE = Depends(read_database), Depends(write_database)

    def editable(session, cycle_id):
        cycle = require(session, Cycle, cycle_id)
        if cycle.archived:
            raise HTTPException(409, '历史周期已归档，只能查看或复制；需要编辑请先恢复')
        return cycle

    def check_version(cycle, expected):
        if cycle.version != expected:
            raise HTTPException(409, '周期配置已变化，请重新读取并生成预览')

    def preview(session, cycle, kind, rows):
        before = rows_for(session, cycle.id, kind)
        changes = differences(kind, before, rows)
        impact = impact_for(session, cycle.id, kind, changes)
        token = CycleChangePreview(id=new_id(), cycle_id=cycle.id, expected_version=cycle.version, kind=kind, rows_json=json.dumps(rows, ensure_ascii=False), created_at=datetime.now(timezone.utc).isoformat(), result_json=None)
        session.add(token)
        session.commit()
        return {'preview_id': token.id, 'kind': kind, 'version': cycle.version, 'changes': changes, 'impact': impact,
                'counts': {action: sum(r['action'] == action for r in changes) for action in ('added', 'changed', 'deleted')}}

    @router.get('/{cycle_id}/data')
    def cycle_data(cycle_id: str, session: Session = READ):
        cycle = require(session, Cycle, cycle_id)
        return {'cycle': dump(cycle), 'sources': [dump(s) for s in session.scalars(select(Source).where(Source.cycle_id == cycle_id).order_by(Source.name, Source.id))], 'tables': {kind: rows_for(session, cycle_id, kind) for kind in SPECS}}

    @router.get('/{cycle_id}/base-stat')
    def base_stat(cycle_id: str, actual_grade: int = Query(gt=0, le=2147483647), equipment_type: str = Query(), session: Session = READ):
        require(session, Cycle, cycle_id)
        if equipment_type not in TYPE_NAMES:
            raise HTTPException(422, '装备部位无效')
        stat = session.get(CycleBaseStat, (cycle_id, actual_grade, equipment_type))
        value = stat.constitution if stat else None
        return {'value': value, 'status': 'unknown' if value is None else 'known'}

    @router.post('/{cycle_id}/data/{kind}/preview')
    def table_preview(cycle_id: str, kind: str, payload: CycleTablePreviewInput, session: Session = WRITE):
        cycle = editable(session, cycle_id)
        check_version(cycle, payload.expected_version)
        return preview(session, cycle, kind, validate_rows(kind, payload.rows))

    @router.post('/{cycle_id}/data/{kind}/csv-preview')
    def csv_preview(cycle_id: str, kind: str, payload: CycleCsvPreviewInput, session: Session = WRITE):
        cycle = editable(session, cycle_id)
        check_version(cycle, payload.expected_version)
        return preview(session, cycle, kind, parse_csv(kind, payload.csv_text))

    @router.get('/{cycle_id}/data/{kind}/csv')
    def export_csv(cycle_id: str, kind: str, session: Session = READ):
        require(session, Cycle, cycle_id)
        columns = spec_for(kind)[3]
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow([HEADERS[c] for c in columns])
        for row in rows_for(session, cycle_id, kind):
            writer.writerow([TYPE_NAMES[row[c]] if c == 'equipment_type' else '' if row[c] is None else row[c] for c in columns])
        return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{kind}.csv"'})

    @router.post('/{cycle_id}/copy-preview')
    def copy_preview(cycle_id: str, payload: CycleCopyInput, session: Session = WRITE):
        cycle = require(session, Cycle, cycle_id)
        check_version(cycle, payload.expected_version)
        counts = {kind: len(rows_for(session, cycle_id, kind)) for kind in SPECS}
        sources = len(list(session.scalars(select(Source).where(Source.cycle_id == cycle_id))))
        token = CycleChangePreview(id=new_id(), cycle_id=cycle_id, expected_version=cycle.version, kind='copy', rows_json=json.dumps({'name': payload.name}, ensure_ascii=False), created_at=datetime.now(timezone.utc).isoformat(), result_json=None)
        session.add(token)
        session.commit()
        return {'preview_id': token.id, 'kind': 'copy', 'name': payload.name, 'sources': sources, 'table_counts': counts, 'message': '复制来源默认值和全部数值配置，并切换到新周期。旧周期、旧装备和库存保持原样。'}

    @router.post('/{cycle_id}/changes/{preview_id}/apply')
    def apply_preview(cycle_id: str, preview_id: str, session: Session = WRITE):
        cycle = require(session, Cycle, cycle_id)
        token = require(session, CycleChangePreview, preview_id)
        if token.cycle_id != cycle_id:
            raise HTTPException(404, '此周期下没有该预览')
        if token.result_json:
            return json.loads(token.result_json)
        check_version(cycle, token.expected_version)
        if datetime.now(timezone.utc) - datetime.fromisoformat(token.created_at) > timedelta(minutes=30):
            raise HTTPException(409, '预览已超过 30 分钟，请重新生成')
        if token.kind != 'copy' and cycle.archived:
            raise HTTPException(409, '此周期已归档，不能应用修改')
        rows = json.loads(token.rows_json)
        before = rows_for(session, cycle_id, token.kind) if token.kind != 'copy' else None
        if token.kind != 'copy' and before == rows:
            result = {'changed': False, 'cycle_id': cycle_id, 'version': cycle.version, 'backup': None}
            token.result_json = json.dumps(result)
            session.commit()
            return result
        # 独立连接备份已提交的旧状态；此时还没有修改任何业务数据。
        backup = backup_database(Path(engine.url.database), 'before-cycle-change')
        if token.kind == 'copy':
            copied = Cycle(id=new_id(), name=rows['name'], archived=False, version=0, copied_from_id=cycle_id)
            session.add(copied)
            session.flush()
            for source in session.scalars(select(Source).where(Source.cycle_id == cycle_id)):
                values = dump(source)
                values.update(id=new_id(), cycle_id=copied.id)
                session.add(Source(**values))
            for kind, (model, _, _, _) in SPECS.items():
                for row in rows_for(session, cycle_id, kind):
                    session.add(model(cycle_id=copied.id, **row))
            session.get(Workspace, 1).active_cycle_id = copied.id
            audit(session, 'copy_cycle', copied.id, {'copied_from_id': cycle_id}, dump(copied))
            result = {'changed': True, 'cycle_id': copied.id, 'version': 0, 'backup': backup.name}
        else:
            model = spec_for(token.kind)[0]
            session.execute(delete(model).where(model.cycle_id == cycle_id))
            for row in rows:
                session.add(model(cycle_id=cycle_id, **row))
            cycle.version += 1
            audit(session, 'cycle_data_apply', cycle_id, {'kind': token.kind, 'rows': before}, {'kind': token.kind, 'rows': rows, 'backup': backup.name})
            result = {'changed': True, 'cycle_id': cycle_id, 'version': cycle.version, 'backup': backup.name}
        token.result_json = json.dumps(result)
        session.commit()
        return result

    @router.put('/{cycle_id}/archive')
    def archive_cycle(cycle_id: str, payload: CycleArchiveInput, session: Session = WRITE):
        cycle = require(session, Cycle, cycle_id)
        check_version(cycle, payload.expected_version)
        if payload.archived and session.get(Workspace, 1).active_cycle_id == cycle_id:
            raise HTTPException(409, '请先切换到另一个当前周期，再归档旧周期')
        before = dump(cycle)
        cycle.archived = payload.archived
        cycle.version += 1
        audit(session, 'archive_cycle', cycle_id, before, dump(cycle))
        session.commit()
        return dump(cycle)

    @router.delete('/{cycle_id}')
    def delete_empty_cycle(cycle_id: str, expected_version: int = Query(ge=0), session: Session = WRITE):
        cycle = require(session, Cycle, cycle_id)
        check_version(cycle, expected_version)
        workspace = session.get(Workspace, 1)
        if workspace.active_cycle_id == cycle_id:
            workspace.active_cycle_id = session.scalar(select(Cycle.id).where(
                Cycle.id != cycle_id, Cycle.archived.is_(False),
            ).order_by(Cycle.name, Cycle.id).limit(1))
            session.flush()
        # Previews are temporary. Foreign keys protect sources, data and history links.
        session.execute(delete(CycleChangePreview).where(CycleChangePreview.cycle_id == cycle_id))
        audit(session, 'delete_cycle', cycle_id, dump(cycle), None)
        session.delete(cycle)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, '此周期已有来源、装备、数值或其他记录引用，只能归档') from None
        return {'active_cycle_id': workspace.active_cycle_id}

    return router
