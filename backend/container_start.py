"""Prepare this application's bind mount, then run the service without root."""
import os
import sys
from pathlib import Path


def prepare_data(directory: Path, uid: int, gid: int):
    if uid <= 0 or gid <= 0:
        raise RuntimeError('JX3_UID/JX3_GID 必须是非 root 的正整数')
    if directory.is_symlink():
        raise RuntimeError('data 挂载路径不能是符号链接')
    directory.mkdir(parents=True, exist_ok=True)
    backups = directory / 'backups'
    if backups.is_symlink():
        raise RuntimeError('backups 目录不能是符号链接')
    backups.mkdir(exist_ok=True)
    if os.geteuid() == 0:
        # Only the application's own directories and three SQLite files.
        # Never recursively change the NAS share or unrelated files.
        for path in (directory, backups, directory / 'app.sqlite3', directory / 'app.sqlite3-wal', directory / 'app.sqlite3-shm'):
            if path.is_symlink():
                raise RuntimeError('数据库路径不能是符号链接')
            if path.exists():
                os.chown(path, uid, gid, follow_symlinks=False)
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    if not os.access(directory, os.W_OK | os.X_OK) or not os.access(backups, os.W_OK | os.X_OK):
        raise RuntimeError('data/backups 不可写，请检查挂载目录与 JX3_UID/JX3_GID')


def main():
    try:
        uid = int(os.environ.get('JX3_APP_UID', '10001'))
        gid = int(os.environ.get('JX3_APP_GID', '10001'))
        directory = Path(os.environ.get('JX3_DATA_DIR', '/app/data'))
        prepare_data(directory, uid, gid)
        print(f'data 已就绪，服务以 UID {os.geteuid()} / GID {os.getegid()} 运行', flush=True)
        command = sys.argv[1:] or [sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '1']
        os.execvp(command[0], command)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'容器启动失败：{error}', file=sys.stderr, flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
