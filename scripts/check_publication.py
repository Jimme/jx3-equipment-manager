"""Check the actual Git index before publishing; never print matching secrets."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET = re.compile(rb'dckr_pat_[A-Za-z0-9_-]{12,}|github_pat_[A-Za-z0-9_]{15,}|gh[pousr]_[A-Za-z0-9]{15,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')


def main():
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    errors = []
    checked = 0
    for relative in filter(None, files):
        path = Path(relative)
        blocked = any(part in {'data', 'backups', '.tools', '.venv', 'node_modules', '.ssh', '.docker'} for part in path.parts)
        blocked |= path.name == '.env' or (path.name.startswith('.env.') and path.name != '.env.example')
        blocked |= path.suffix in {'.sqlite3', '.db', '.pem', '.key'} or path.name.endswith(('.sqlite3-wal', '.sqlite3-shm', '.db-wal', '.db-shm'))
        if blocked:
            errors.append(relative + ': sensitive path')
            continue
        raw = (ROOT / path).read_bytes()
        checked += 1
        if SECRET.search(raw):
            errors.append(relative + ': credential pattern')
        if path.suffix == '.json' and b'jx3-equipment-workspace' in raw and b'schema_revision' in raw:
            errors.append(relative + ': workspace export')
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Publication scan passed: {checked} tracked files; no matching credentials or data paths.')


if __name__ == '__main__':
    main()
