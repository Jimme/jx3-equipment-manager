"""Validated full-workspace export/import and transactional backup restore."""
import hashlib
import copy
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .cycle_data import SPECS, validate_rows
from .database import backup_database, make_engine, migrate
from .domain import SLOT_KEYS, equipment_type
from .drop_merge import merge_drop_rows
from .models import Base, DataChangePreview
from .schemas import AccountPreferences, AttachmentChoice, DataApplyInput, NamedInput, SourceInput, TemplateInput
from .services import audit, new_id, require

MAX_BYTES = 32 * 1024 * 1024
TRANSIENT = {'cycle_change_previews', 'data_change_previews'}
TABLES = [table for table in Base.metadata.sorted_tables if table.name not in TRANSIENT]
TABLE_MAP = {table.name: table for table in TABLES}
FORMAT = 'jx3-equipment-workspace'
REVISION = '0009'
RAID_COLUMNS = {'raid_10', 'raid_25pt', 'raid_25yx'}


def now():
    return datetime.now(timezone.utc)


def snapshot(session):
    return {table.name: [dict(row) for row in session.execute(select(table)).mappings()] for table in TABLES}


def canonical(tables):
    return json.dumps({name: sorted((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')) for row in rows)) for name, rows in sorted(tables.items())}, ensure_ascii=False, separators=(',', ':'))


def fingerprint(tables):
    return hashlib.sha256(canonical(tables).encode()).hexdigest()


def summary(tables):
    return {'游戏账号': len(tables['game_accounts']), '角色': len(tables['characters']), '周期': len(tables['equipment_cycles']),
            '来源': len(tables['equipment_sources']), '当前穿戴件数': sum(r['config_id'] is not None for r in tables['character_equipment']),
            '共享仓库件数': sum(r['quantity'] for r in tables['warehouse_stacks']), '团本掉落件数': sum(r['quantity'] for r in tables['raid_drop_notes']),
            '数值配置行数': sum(len(tables[model.__tablename__]) for model, *_ in SPECS.values())}


def change_summary(before, after):
    labels = {'game_accounts': '游戏账号', 'characters': '角色', 'equipment_cycles': '周期', 'equipment_sources': '来源',
              'equipment_templates': '装备模板', 'equipment_configs': '装备附魔/五彩石', 'character_equipment': '穿戴记录',
              'character_slot_settings': '部位养成', 'character_calculation_settings': '计算口径', 'warehouse_stacks': '共享库存',
              'raid_drop_notes': '团本掉落', 'raid_memo_states': '掉落状态', 'workspace_settings': '当前周期', 'audit_events': '操作历史',
              'cycle_base_stats': '基础体质', 'cycle_refine_stats': '精炼数值', 'cycle_stone_stats': '五行石数值',
              'cycle_enchants': '附魔数值', 'cycle_rainbow_stones': '五彩石数值', 'cycle_buff_thresholds': '技能阈值',
              'mutation_receipts': '操作重试记录'}
    result = []
    for table in TABLES:
        keys = [c.name for c in table.primary_key]
        old = {tuple(r[k] for k in keys): r for r in before[table.name]}
        new = {tuple(r[k] for k in keys): r for r in after[table.name]}
        added, deleted = len(new.keys() - old.keys()), len(old.keys() - new.keys())
        changed = sum(old[k] != new[k] for k in old.keys() & new.keys())
        if added or deleted or changed:
            result.append({'label': labels[table.name], 'added': added, 'changed': changed, 'deleted': deleted})
    return result


def rebase_versions(current, tables):
    versions = [row['version'] for state in (current, tables) for rows in state.values() for row in rows if 'version' in row]
    versions.extend(state['workspace_settings'][0]['version_seed'] for state in (current, tables))
    version = max(versions, default=0) + 1
    if version > 2147483647:
        reject('记录版本超出可用范围，无法安全替换')
    result = copy.deepcopy(tables)
    result['workspace_settings'][0]['version_seed'] = version
    for rows in result.values():
        for row in rows:
            if 'version' in row:
                row['version'] = version
    return result


def document(tables):
    return {'format': FORMAT, 'format_version': 1, 'schema_revision': REVISION, 'exported_at': now().isoformat(), 'tables': tables}


def reject(message):
    raise HTTPException(422, message)


def validate_types(tables):
    if not isinstance(tables, dict) or set(tables) != set(TABLE_MAP):
        reject('数据表不完整或含未知表，请使用本版本导出的完整 JSON')
    count = 0
    for name, table in TABLE_MAP.items():
        rows = tables[name]
        if not isinstance(rows, list):
            reject(f'{name}：应为记录列表')
        count += len(rows)
        if count > 200000:
            reject('导入记录合计最多 200000 行')
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict) or set(row) != {c.name for c in table.columns}:
                reject(f'{name} 第{index}行：字段缺失或含未知字段')
            for column in table.columns:
                value = row[column.name]
                if value is None:
                    if not column.nullable:
                        reject(f'{name} 第{index}行 {column.name}：不能为空')
                elif type(value) is not column.type.python_type:
                    reject(f'{name} 第{index}行 {column.name}：数据类型错误')
                elif type(value) is int and not -2147483648 <= value <= 2147483647:
                    reject(f'{name} 第{index}行 {column.name}：整数超出范围')
                elif column.name in ('version', 'version_seed') and value < 0:
                    reject(f'{name} 第{index}行：版本不能为负数')
                elif isinstance(value, str) and (len(value) > 2000000 or (column.primary_key and (not value or len(value) > 100))):
                    reject(f'{name} 第{index}行 {column.name}：文字长度无效')


def validate_business(tables):
    def check(schema, value, context):
        try:
            result = schema.model_validate(value).model_dump()
            if any(result[k] != v for k, v in value.items()):
                reject(f'{context}：名称或文字格式无效，请使用原始导出文件')
        except ValidationError as error:
            reject(f'{context}：' + '; '.join(e['msg'] for e in error.errors()[:5]))
    for row in tables['game_accounts']:
        check(NamedInput, {k: row[k] for k in ('name', 'sort_order', 'archived')}, '账号')
        check(AccountPreferences, {k: row[k] for k in ('target_grade', 'comparison_mode')}, '账号目标')
    for row in tables['characters']:
        check(NamedInput, {k: row[k] for k in ('name', 'sort_order', 'archived')}, '角色')
    for row in tables['equipment_cycles']:
        if not row['name'].strip() or len(row['name']) > 100:
            reject('周期名称无效')
    sources = {r['id']: r for r in tables['equipment_sources']}
    for row in sources.values():
        check(SourceInput, {k: v for k, v in row.items() if k not in ('id', 'cycle_id')}, '来源')
    templates = {r['id']: r for r in tables['equipment_templates']}
    for row in templates.values():
        check(TemplateInput, {k: v for k, v in row.items() if k != 'id'}, '装备模板')
        if row['source_id'] and (row['source_id'] not in sources or sources[row['source_id']]['cycle_id'] != row['cycle_origin_id']):
            reject('装备模板的来源不属于其原周期')
    configs = {r['id']: r for r in tables['equipment_configs']}
    for row in configs.values():
        template = templates.get(row['template_id'])
        if not template:
            reject('装备配置引用不存在的模板')
        if (template['equipment_type'] == 'weapon') == (row['rainbow_state'] == 'not_applicable'):
            reject('五彩石的适用部位不正确')
        for prefix in ('enchant', 'rainbow'):
            if row[prefix + '_state'] != 'not_applicable':
                check(AttachmentChoice, {key: row[prefix + '_' + key] for key in ('state', 'name', 'constitution')}, '附魔/五彩石')
        values = {k: v for k, v in row.items() if k not in ('id', 'config_key')}
        key = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if row['config_key'] != key:
            reject('装备配置标识与内容不一致，请勿直接编辑导出文件')
    for row in tables['character_equipment']:
        if row['config_id']:
            config = configs.get(row['config_id'])
            if not config or row['slot'] not in SLOT_KEYS or templates[config['template_id']]['equipment_type'] != equipment_type(row['slot']):
                reject('角色穿戴装备与部位不匹配')
    characters = {r['id'] for r in tables['characters']}
    for name in ('character_slot_settings', 'character_equipment'):
        by_character = {}
        for row in tables[name]:
            by_character.setdefault(row['character_id'], []).append(row['slot'])
        if set(by_character) != characters or any(len(slots) != 12 or set(slots) != set(SLOT_KEYS) for slots in by_character.values()):
            reject('每个角色必须完整包含 12 个不同的穿戴和养成部位')
    if len(tables['character_calculation_settings']) != len(characters) or {r['character_id'] for r in tables['character_calculation_settings']} != characters:
        reject('角色计算设置不完整')
    for row in tables['character_calculation_settings']:
        if row['scope_confirmed'] and (row['additional_constitution'] is None or row['threshold_cycle_id'] is None):
            reject('已确认的计算口径缺少必要修正或周期')
    if len(tables['workspace_settings']) != 1 or tables['workspace_settings'][0]['id'] != 1:
        reject('工作空间设置必须且只能有一条')
    active = tables['workspace_settings'][0]['active_cycle_id']
    if active is not None and not any(r['id'] == active and not r['archived'] for r in tables['equipment_cycles']):
        reject('当前周期不存在或已归档')
    cycles = {r['id'] for r in tables['equipment_cycles']}
    for kind, (model, _, _, _) in SPECS.items():
        for cycle_id in cycles:
            validate_rows(kind, [{k: v for k, v in r.items() if k != 'cycle_id'} for r in tables[model.__tablename__] if r['cycle_id'] == cycle_id])
    for row in tables['audit_events']:
        try:
            value = json.loads(row['snapshots'])
            if not isinstance(value, dict) or not {'before', 'after'} <= set(value):
                reject('操作记录格式无效')
            if row['operation'] == 'replace_equipment':
                for item in (value['before'], value['after']):
                    if not isinstance(item, dict) or item.get('character_id') not in characters or item.get('slot') not in SLOT_KEYS or (item.get('config_id') is not None and item['config_id'] not in configs):
                        reject('穿戴历史引用不完整')
            if row['operation'] == 'warehouse_equip':
                for item in (value['before']['worn'], value['after']['worn']):
                    if item.get('config_id') is not None and item['config_id'] not in configs:
                        reject('仓库穿戴历史引用不完整')
        except (ValueError, TypeError, KeyError):
            reject('操作记录格式无效')
    for row in tables['mutation_receipts']:
        try:
            if not re.fullmatch('[a-f0-9]{64}', row['fingerprint']) or not isinstance(json.loads(row['result']), dict):
                reject('操作重试记录无效')
        except (ValueError, TypeError):
            reject('操作重试记录无效')


def fill_tables(session, tables):
    session.connection().exec_driver_sql('PRAGMA defer_foreign_keys=ON')
    for table in reversed(TABLES):
        session.execute(delete(table))
    for table in TABLES:
        rows = tables[table.name]
        for start in range(0, len(rows), 500):
            session.execute(table.insert(), rows[start:start + 500])
    if session.connection().exec_driver_sql('PRAGMA foreign_key_check').fetchall():
        reject('存在无效的数据引用，当前数据未被替换')


def validate_document(payload, directory):
    if not isinstance(payload, dict) or set(payload) != {'format', 'format_version', 'schema_revision', 'exported_at', 'tables'}:
        reject('不是完整的工作空间 JSON 文件')
    if payload['format'] != FORMAT or type(payload['format_version']) is not int or payload['format_version'] != 1 or payload['schema_revision'] not in ('0006', '0007', '0008', REVISION):
        reject('文件版本不兼容，请使用当前版本导出文件；旧数据库备份请按停机恢复说明处理')
    if not isinstance(payload['exported_at'], str):
        reject('导出时间格式无效')
    tables = payload['tables']
    if payload['schema_revision'] in ('0006', '0007') and isinstance(tables, dict) and isinstance(tables.get('equipment_cycles'), list):
        old_columns = {column.name for column in TABLE_MAP['equipment_cycles'].columns} - {'has_constitution_sockets'}
        cycles = []
        for row in tables['equipment_cycles']:
            if not isinstance(row, dict) or set(row) != old_columns:
                reject('旧版周期数据字段不匹配')
            cycles.append({**row, 'has_constitution_sockets': True})
        tables = {**tables, 'equipment_cycles': cycles}
    if payload['schema_revision'] == '0006' and isinstance(tables, dict) and isinstance(tables.get('characters'), list):
        old_columns = {column.name for column in TABLE_MAP['characters'].columns} - RAID_COLUMNS
        characters = []
        for row in tables['characters']:
            if not isinstance(row, dict) or set(row) != old_columns:
                reject('旧版角色数据字段不匹配')
            characters.append({**row, **dict.fromkeys(RAID_COLUMNS, False)})
        tables = {**tables, 'characters': characters}
    validate_types(tables)
    validate_business(tables)
    if payload['schema_revision'] != REVISION:
        try:
            merged, changes = merge_drop_rows(tables['raid_drop_notes'])
        except ValueError as error:
            reject(str(error))
        if changes:
            tables = copy.deepcopy(tables)
            tables['raid_drop_notes'] = merged
            for before, after in changes:
                state = next((row for row in tables['raid_memo_states'] if row['account_id'] == after['account_id']), None)
                if state:
                    state['version'] += 1
                else:
                    tables['raid_memo_states'].append({'account_id': after['account_id'], 'version': 1})
                tables['audit_events'].append({
                    'id': new_id(), 'created_at': now().isoformat(), 'account_id': after['account_id'],
                    'operation': 'drop_merge_migration', 'entity_id': after['id'],
                    'snapshots': json.dumps({'before': before, 'after': after, 'summary': '导入时归并相同配置掉落，数量保持一致'}, ensure_ascii=False),
                })
    with tempfile.TemporaryDirectory(prefix='jx3-validate-') as temporary:
        engine = make_engine(Path(temporary))
        try:
            migrate(engine)
            with Session(engine) as session:
                fill_tables(session, tables)
                session.commit()
        except IntegrityError:
            reject('重复记录、非法数值或数据引用不完整，当前数据未被替换')
        finally:
            engine.dispose()
    return tables


def load_json(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('重复字段')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=object_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非法数值')))
    except (UnicodeDecodeError, ValueError, RecursionError):
        reject('JSON 格式无效，文件需为 UTF-8，不能含重复字段或非法数值')


def sqlite_document(raw, directory):
    if not raw.startswith(b'SQLite format 3\x00'):
        reject('不是有效的 SQLite 备份文件')
    with tempfile.TemporaryDirectory(prefix='jx3-restore-') as temporary:
        path = Path(temporary) / 'upload.sqlite3'
        path.write_bytes(raw)
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
                if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    reject('备份完整性检查失败')
                revisions = connection.execute('SELECT version_num FROM alembic_version').fetchall()
                if revisions not in ([(REVISION,)], [('0008',)], [('0007',)], [('0006',)]):
                    reject('此备份不属于当前数据库版本，请按停机恢复说明使用匹配版本再升级')
                objects = connection.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND type IN ('table','view','trigger')").fetchall()
                expected = set(Base.metadata.tables) | {'alembic_version'}
                if {r[0] for r in objects} != expected or any(kind != 'table' or 'VIRTUAL TABLE' in sql.upper() for _, kind, sql in objects):
                    reject('备份结构不匹配或包含非应用数据对象')
                tables = {}
                for table in TABLES:
                    columns = [c for c in table.columns if not (revisions == [('0006',)] and table.name == 'characters' and c.name in RAID_COLUMNS)]
                    if revisions in ([('0006',)], [('0007',)]) and table.name == 'equipment_cycles':
                        columns = [c for c in columns if c.name != 'has_constitution_sockets']
                    if {r[1] for r in connection.execute(f'PRAGMA table_info("{table.name}")')} != {c.name for c in columns}:
                        reject('备份字段与当前版本不一致')
                    values = connection.execute('SELECT ' + ','.join('"'+c.name+'"' for c in columns) + ' FROM "'+table.name+'"').fetchall()
                    rows = []
                    for values_row in values:
                        row = {}
                        for column, value in zip(columns, values_row):
                            row[column.name] = bool(value) if column.type.python_type is bool and type(value) is int and value in (0, 1) else value
                        rows.append(row)
                    tables[table.name] = rows
        except sqlite3.Error:
            reject('SQLite 备份无法读取或结构不完整')
    return validate_document({**document(tables), 'schema_revision': revisions[0][0]}, directory)


async def read_upload(request):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(413, '文件最多 32 MB')
        chunks.append(chunk)
    if not size:
        reject('请选择非空文件')
    return b''.join(chunks)


def make_data_router(engine, read_database):
    router = APIRouter(prefix='/api/data')
    directory = Path(engine.url.database).parent

    def write_database():
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            yield session

    def backup_path(filename):
        folder = directory.resolve() / 'backups'
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.sqlite3', filename):
            raise HTTPException(404, '备份不存在')
        path = folder / filename
        if path.resolve().parent != folder or not path.is_file() or path.is_symlink():
            raise HTTPException(404, '备份不存在')
        return path

    def safe_backup(prefix):
        try:
            return backup_database(Path(engine.url.database), prefix)
        except (OSError, sqlite3.Error, RuntimeError):
            raise HTTPException(503, '备份失败，操作未执行；请检查 data 目录权限和剩余空间')

    @router.get('/export')
    def export(session: Session = Depends(read_database)):
        content = json.dumps(document(snapshot(session)), ensure_ascii=False, indent=2)
        return Response(content, media_type='application/json', headers={'Content-Disposition': f'attachment; filename="jx3-workspace-{now():%Y%m%dT%H%M%SZ}.json"', 'Cache-Control': 'no-store'})

    @router.get('/backups')
    def backups():
        folder = directory / 'backups'
        result = []
        for candidate in folder.glob('*.sqlite3'):
            try:
                path = backup_path(candidate.name)
                info = path.stat()
            except (OSError, HTTPException):
                # File Station may remove/move a snapshot during this scan.
                continue
            result.append({'name': path.name, 'size': info.st_size, 'created_at': datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()})
        return sorted(result, key=lambda item: item['created_at'], reverse=True)

    @router.post('/backups', status_code=201)
    def create_backup():
        result = safe_backup('backup')
        return {'name': result.name}

    @router.get('/backups/{filename}')
    def download_backup(filename: str):
        return FileResponse(backup_path(filename), media_type='application/vnd.sqlite3', filename=filename, headers={'Cache-Control': 'no-store'})

    @router.delete('/backups/{filename}')
    def delete_backup(filename: str):
        path = backup_path(filename)
        try:
            path.unlink()
        except FileNotFoundError:
            raise HTTPException(404, '备份不存在')
        except OSError:
            raise HTTPException(503, '备份删除失败，请检查目录权限或稍后重试')
        return {'name': filename}

    def make_preview(tables, kind):
        with Session(engine.execution_options(sqlite_immediate=True)) as session:
            current = snapshot(session)
            for old in session.scalars(select(DataChangePreview)):
                age = now() - datetime.fromisoformat(old.created_at)
                if age > (timedelta(days=7) if old.result_json else timedelta(minutes=30)):
                    session.delete(old)
            token = DataChangePreview(id=new_id(), kind=kind, expected_fingerprint=fingerprint(current),
                                      payload_json=json.dumps(tables, ensure_ascii=False), created_at=now().isoformat(), result_json=None)
            session.add(token)
            session.commit()
            return {'preview_id': token.id, 'kind': kind, 'before': summary(current), 'after': summary(tables),
                    'changes': change_summary(current, tables),
                    'names': {label: {'before': sorted(r['name'] for r in current[name])[:20], 'after': sorted(r['name'] for r in tables[name])[:20]}
                              for label, name in [('账号', 'game_accounts'), ('角色', 'characters'), ('周期', 'equipment_cycles')]},
                    'unchanged': fingerprint(current) == fingerprint(tables)}

    @router.post('/import-preview')
    async def import_preview(request: Request):
        raw = await read_upload(request)
        tables = await run_in_threadpool(validate_document, load_json(raw), directory)
        return make_preview(tables, 'import')

    @router.post('/restore-preview')
    async def restore_preview(request: Request):
        raw = await read_upload(request)
        tables = await run_in_threadpool(sqlite_document, raw, directory)
        return make_preview(tables, 'restore')

    @router.post('/backups/{filename}/restore-preview')
    def stored_restore_preview(filename: str):
        path = backup_path(filename)
        try:
            with path.open('rb') as backup:
                raw = backup.read(MAX_BYTES + 1)
        except FileNotFoundError:
            raise HTTPException(404, '备份不存在')
        except OSError:
            raise HTTPException(503, '备份无法读取，请检查文件和目录权限')
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, '文件最多 32 MB；更大备份请按停机恢复说明操作')
        return make_preview(sqlite_document(raw, directory), 'restore')

    @router.post('/changes/{preview_id}/apply')
    def apply_preview(preview_id: str, payload: DataApplyInput, session: Session = Depends(write_database)):
        if not payload.confirm_replace:
            raise HTTPException(422, '请先确认整体替换当前工作空间')
        token = require(session, DataChangePreview, preview_id)
        if token.kind not in ('import', 'restore'):
            raise HTTPException(404, '工作空间替换预览不存在')
        if token.result_json:
            return json.loads(token.result_json)
        if now() - datetime.fromisoformat(token.created_at) > timedelta(minutes=30):
            raise HTTPException(409, '预览已过期，请重新选择文件并预览')
        current = snapshot(session)
        if fingerprint(current) != token.expected_fingerprint:
            raise HTTPException(409, '工作空间已变化，当前数据未被替换；请重新选择文件并预览')
        tables = json.loads(token.payload_json)
        changed = fingerprint(current) != fingerprint(tables)
        result = {'changed': changed, 'backup': None}
        if changed:
            tables = rebase_versions(current, tables)
            account_ids = {r['id'] for r in tables['game_accounts']}
            receipt_keys = {(r['account_id'], r['request_id']) for r in tables['mutation_receipts']}
            # A request executed after the chosen snapshot is being rolled back.
            # Keep its identifier as a tombstone, so a delayed retry cannot add
            # the same inventory again after recovery.
            for row in current['mutation_receipts']:
                if row['account_id'] in account_ids and (row['account_id'], row['request_id']) not in receipt_keys:
                    tables['mutation_receipts'].append({**row, 'result': json.dumps({'_restored_request': True})})
            backup = safe_backup('before-workspace-replace')
            # Old previews must never apply against restored state.
            session.execute(delete(Base.metadata.tables['cycle_change_previews']))
            fill_tables(session, tables)
            audit(session, 'workspace_' + token.kind, '1', summary(current), {'summary': summary(tables), 'backup': backup.name})
            result['backup'] = backup.name
            session.execute(delete(DataChangePreview).where(DataChangePreview.id != preview_id))
        token.payload_json = ''
        token.result_json = json.dumps(result)
        session.commit()
        return result

    return router
