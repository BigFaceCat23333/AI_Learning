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
    # 兼容旧环境：增量创建会话相关表与索引。
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS conversations ("
                "  id SERIAL PRIMARY KEY,"
                "  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
                "  title VARCHAR(100) NOT NULL,"
                "  created_at TIMESTAMP NOT NULL DEFAULT NOW(),"
                "  last_message_at TIMESTAMP NOT NULL DEFAULT NOW(),"
                "  deleted_at TIMESTAMP NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_conversations_user_list "
                "ON conversations(user_id, last_message_at DESC, id DESC)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS conversation_messages ("
                "  id SERIAL PRIMARY KEY,"
                "  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
                "  role VARCHAR(16) NOT NULL,"
                "  content TEXT NOT NULL,"
                "  sources JSONB NULL,"
                "  created_at TIMESTAMP NOT NULL DEFAULT NOW()"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_conversation_messages_conv "
                "ON conversation_messages(conversation_id)"
            )
        )
        # role CHECK 约束：幂等添加（PostgreSQL 不支持 ADD CONSTRAINT IF NOT EXISTS，
        # 需要用 DO 块捕获 duplicate_object 错误）。
        connection.execute(
            text(
                "DO $$ "
                "BEGIN "
                "  ALTER TABLE conversation_messages "
                "    ADD CONSTRAINT chk_conversation_messages_role "
                "    CHECK (role IN ('user', 'assistant')); "
                "EXCEPTION WHEN duplicate_object THEN NULL; "
                "END $$"
            )
        )


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
