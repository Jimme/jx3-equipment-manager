"""Verify an update against its consistent snapshot, accounting for known migrations.

Run with the project virtualenv. State contains private exports: keep it in .tools.
"""
import argparse
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import Boolean
from backend.data_management import REVISION, TABLE_MAP, fingerprint


def verify_tables(before, after):
    assert before['schema_revision'] == after['schema_revision'] == REVISION
    assert fingerprint(before['tables']) == fingerprint(after['tables']), 'Business data differs'
    return 0


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
    parser.add_argument('--version', default='0.1.16')
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
