from alembic import context


def run_migrations_online():
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("请通过 python -m backend.manage migrate 执行迁移")
    context.configure(connection=connection, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
