from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from storage.database import validate_database_target
from storage.models import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_online_database_url() -> str:
    """在线迁移只能使用部署显式提供的连接串，避免误连默认数据库。"""
    database_url = os.environ.get("MATHWEAVER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("MATHWEAVER_DATABASE_URL must be configured for online migrations")
    return validate_database_target(database_url)


def run_migrations_offline() -> None:
    context.configure(
        # 无凭据占位值仅用于 --sql 选择 MySQL 方言，绝不用于在线连接。
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_online_database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
