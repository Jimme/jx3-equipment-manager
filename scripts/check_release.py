"""Validate an immutable public release before giving Docker Actions a token."""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    tag = os.environ['RELEASE_TAG']
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise SystemExit('Expected a vMAJOR.MINOR.PATCH release tag')
    version = tag[1:]
    if json.loads((ROOT / 'frontend/package.json').read_text())['version'] != version:
        raise SystemExit('Release tag and frontend version differ')
    sys.path.insert(0, str(ROOT))
    from backend.main import app
    if app.version != version:
        raise SystemExit('Release tag and backend version differ')
    base = 'https://hub.docker.com/v2/namespaces/jimmeyoumu/repositories/jx3-equipment-manager'
    with urllib.request.urlopen(base, timeout=30) as response:
        repo = json.load(response)
    if repo.get('namespace') != 'jimmeyoumu' or repo.get('name') != 'jx3-equipment-manager' or repo.get('is_private') is not False:
        raise SystemExit('Expected the audited public Docker Hub repository')
    try:
        with urllib.request.urlopen(base + '/tags/' + version, timeout=30):
            raise SystemExit('Fixed version already exists; refusing to overwrite')
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write('version=' + version + '\n')
    print('Release version and public repository verified; fixed tag is unused.')


if __name__ == '__main__':
    main()
