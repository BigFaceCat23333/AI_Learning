from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ai_learning.core.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    settings = get_settings()
    return create_engine(settings.database_url, echo=settings.sql_echo)


@lru_cache
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def init_db() -> None:
    """初始化数据库：启用 pgvector 扩展、创建表并执行轻量兼容升级。

    注意：这里只执行本项目明确维护的幂等兼容 SQL；其他结构变更仍需正式迁移。
    """
    from ai_learning import models  # noqa: F401

    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)
    # 兼容已存在的 documents 表，确保旧环境无需删库即可使用文档逻辑删除。
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL")
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_documents_user_deleted_at "
                "ON documents(user_id, deleted_at)"
            )
        )


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
