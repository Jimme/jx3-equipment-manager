import argparse

from .database import backup_database, get_data_dir, make_engine, migrate


def main():
    parser = argparse.ArgumentParser(description="装备管理数据库维护")
    parser.add_argument("command", choices=["migrate", "backup"])
    args = parser.parse_args()
    directory = get_data_dir()
    if args.command == "backup":
        path = directory / "app.sqlite3"
        if not path.is_file():
            parser.error("数据库不存在，请先启动应用")
        print(backup_database(path))
    else:
        engine = make_engine(directory)
        try:
            migrate(engine)
            print("数据库迁移完成")
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
