import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event

ROOT = Path(__file__).resolve().parent.parent


def get_data_dir():
    return Path(os.environ.get("JX3_DATA_DIR", ROOT / "data")).resolve()


def backup_database(path: Path, prefix: str = "backup") -> Path:
    if not path.is_file():
        raise FileNotFoundError('数据库文件不存在，未创建备份')
    destination_dir = path.parent / "backups"
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = destination_dir / f"{prefix}-{stamp}.sqlite3"
    # Reserve exclusively so a concurrent backup can never overwrite a snapshot.
    with destination.open('xb'):
        pass
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            names = {r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            cleaned = False
            for name in ('cycle_change_previews', 'data_change_previews'):
                if name in names and target.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone():
                    target.execute(f'DELETE FROM "{name}"')
                    cleaned = True
            if cleaned:
                # Pending uploads can be large. Do not carry their payloads or
                # stale preview tokens into a recovery snapshot. Keep receipts:
                # retries after recovery must not duplicate inventory changes.
                target.commit()
                target.execute('VACUUM')
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("备份完整性校验失败，操作已停止")
            if target.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("备份外键校验失败，请检查原数据库")
    except Exception:
        # This is only the newly reserved file, never an existing backup.
        destination.unlink(missing_ok=True)
        raise
    return destination


def make_engine(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{(data_dir / 'app.sqlite3').as_posix()}", connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN IMMEDIATE" if connection.get_execution_options().get('sqlite_immediate') else "BEGIN")

    return engine


def migrate(engine):
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    head = ScriptDirectory.from_config(config).get_current_head()
    if current == head:
        return
    if current is not None:
        backup_database(Path(engine.url.database), "before-migration")
    with engine.connect() as connection:
        # SQLite 的批量迁移需替换表；在独立迁移连接、事务开始前暂关外键，
        # 事务提交前检查全部引用。运行期连接始终启用外键。
        raw_connection = connection.connection.driver_connection
        raw_connection.execute("PRAGMA foreign_keys=OFF")
        try:
            with connection.begin():
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("迁移后外键校验失败，升级已回滚")
        finally:
            raw_connection.execute("PRAGMA foreign_keys=ON")
