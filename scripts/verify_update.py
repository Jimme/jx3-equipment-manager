"""Verify an update against its consistent snapshot, accounting for known migrations.

Run with the project virtualenv. State contains private exports: keep it in .tools.
"""
import argparse
import copy
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import Boolean
from backend.data_management import TABLE_MAP, fingerprint
from backend.drop_merge import merge_drop_rows


def expected_tables(document):
    revision = document['schema_revision']
    if revision not in ('0006', '0007', '0008', '0009'):
        raise ValueError('Unsupported source revision: ' + revision)
    tables = copy.deepcopy(document['tables'])
    if revision in ('0006', '0007'):
        for row in tables['equipment_cycles']:
            assert 'has_constitution_sockets' not in row
            row['has_constitution_sockets'] = True
    if revision == '0006':
        for row in tables['characters']:
            row.update(raid_10=False, raid_25pt=False, raid_25yx=False)
    merged, changes = merge_drop_rows(tables['raid_drop_notes'])
    tables['raid_drop_notes'] = merged
    for _, after in changes:
        state = next((row for row in tables['raid_memo_states'] if row['account_id'] == after['account_id']), None)
        if state:
            state['version'] += 1
        else:
            tables['raid_memo_states'].append({'account_id': after['account_id'], 'version': 1})
    return tables, changes


def verify_tables(before, after):
    assert after['schema_revision'] == '0009'
    expected, changes = expected_tables(before)
    actual = copy.deepcopy(after['tables'])
    old_ids = {row['id'] for row in expected['audit_events']}
    added = [row for row in actual['audit_events'] if row['id'] not in old_ids]
    assert len(added) == len(changes), 'Unexpected audit events; someone may have edited data during update'
    for prior, merged in changes:
        matches = [row for row in added if row['operation'] == 'drop_merge_migration' and row['entity_id'] == merged['id']]
        assert len(matches) == 1
        detail = json.loads(matches[0]['snapshots'])
        assert detail['before'] == prior and detail['after'] == merged
    actual['audit_events'] = [row for row in actual['audit_events'] if row['id'] in old_ids]
    assert fingerprint(expected) == fingerprint(actual), 'Business data differs from expected migration'
    return len(changes)


def check_snapshot(path, document):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0] == document['schema_revision']
        tables = {}
        for name in document['tables']:
            booleans = {column.name for column in TABLE_MAP[name].columns if isinstance(column.type, Boolean)}
            tables[name] = [{key: bool(value) if key in booleans else value for key, value in dict(row).items()}
                            for row in db.execute(f'SELECT * FROM "{name}"')]
        assert fingerprint(tables) == fingerprint(document['tables']), 'Snapshot differs; concurrent edits may have occurred'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['before', 'after'])
    parser.add_argument('--url', required=True, help='Application URL without /api')
    parser.add_argument('--state', type=Path, default=Path('.tools/update-verification.json'))
    parser.add_argument('--version', default='0.1.10')
    args = parser.parse_args()
    if '.tools' not in args.state.resolve().parts:
        parser.error('Private update state must be stored under .tools')
    def request(path, method='GET'):
        req = urllib.request.Request(args.url.rstrip('/') + '/api' + path, method=method)
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read()
    def get(path):
        return json.loads(request(path))
    args.state.parent.mkdir(parents=True, exist_ok=True)
    if args.phase == 'before':
        assert not args.state.exists(), 'Use a fresh state path; do not overwrite an earlier baseline'
        previous_backups = get('/data/backups')
        backup = json.loads(request('/data/backups', 'POST'))['name']
        snapshot = args.state.with_suffix('.sqlite3')
        snapshot.write_bytes(request('/data/backups/' + backup))
        document = get('/data/export')
        check_snapshot(snapshot, document)
        value = {'url': args.url, 'health': get('/health'), 'document': document, 'backup': backup,
                 'snapshot': str(snapshot), 'previous_backups': previous_backups, 'verified': False}
        args.state.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({'phase': 'before', 'health': value['health'], 'backup_checked': True,
                          'records': sum(map(len, document['tables'].values()))}))
    else:
        value = json.loads(args.state.read_text(encoding='utf-8'))
        assert value['url'] == args.url
        health, document = get('/health'), get('/data/export')
        assert health == {'status': 'ok', 'version': args.version}, health
        merges = verify_tables(value['document'], document)
        backup = json.loads(request('/data/backups', 'POST'))['name']
        snapshot = args.state.with_suffix('.after.sqlite3')
        snapshot.write_bytes(request('/data/backups/' + backup))
        check_snapshot(snapshot, document)
        value.update(verified=True, after_health=health, after_backup=backup, after_snapshot=str(snapshot))
        args.state.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({'phase': 'after', 'health': health, 'migration_verified': True,
                          'integrity': 'ok', 'merged_groups': merges, 'records': sum(map(len, document['tables'].values()))}))


if __name__ == '__main__':
    main()
