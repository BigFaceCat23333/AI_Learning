from collections.abc import Generator
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ai_learning.core.config import get_settings
from ai_learning.core.llm_client import LLMClient
from ai_learning.db import get_engine, get_session_factory
from ai_learning.main import create_app


POSTGRES_TEST_DATABASE_URL = "postgresql+psycopg://urpapa:postgres@localhost:5432/ai_learning_test"


def recreate_tables() -> None:
    """重建测试数据库表结构（开发环境适用）。"""
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("DROP TABLE IF EXISTS document_chunks CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS documents CASCADE"))
    # 让 SQLAlchemy 重新创建表
    from ai_learning.db import Base
    from ai_learning import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def clear_database() -> None:
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE document_chunks, documents RESTART IDENTITY CASCADE"))


@pytest.fixture
def client(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv(
        "AI_LEARNING_DATABASE_URL",
        os.getenv("AI_LEARNING_TEST_DATABASE_URL", POSTGRES_TEST_DATABASE_URL),
    )
    monkeypatch.setenv("AI_LEARNING_UPLOAD_DIR", str(tmp_path / "uploads"))
    # 测试环境使用 mock embedding，避免网络和密钥依赖
    monkeypatch.setenv("AI_LEARNING_EMBEDDING_PROVIDER", "mock")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    # 重建表以适应新 schema（开发环境）
    recreate_tables()

    app = create_app()
    with TestClient(app) as test_client:
        clear_database()
        yield test_client
        clear_database()

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_txt_writes_chunks(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("demo.txt", "FastAPI knowledge base document content for testing".encode("utf-8"), "text/plain")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == 1
    assert payload["filename"] == "demo.txt"
    assert payload["file_type"] == "txt"
    assert payload["chunk_count"] >= 1


def test_upload_md_writes_chunks(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("readme.md", "# FastAPI\n\nFastAPI 支持上传文档并解析入库。".encode("utf-8"), "text/markdown")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["file_type"] == "md"
    assert payload["chunk_count"] >= 1


def test_upload_rejects_pdf(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("demo.pdf", b"content", "application/pdf")},
    )

    assert response.status_code == 400
    assert "supported" in response.json()["detail"]


def test_upload_rejects_unsupported_extension(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("demo.docx", b"content", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 400
    assert "supported" in response.json()["detail"]


def test_upload_rejects_empty_document(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("empty.txt", b"   ", "text/plain")},
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_query_returns_answer_and_sources(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(LLMClient, "complete", lambda self, prompt: "real llm test answer")
    # 降低检索阈值以确保 mock embedding 能命中
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_MIN_SCORE", "0.0")
    get_settings.cache_clear()

    client.post(
        "/api/documents/upload",
        files={"file": ("demo.md", "FastAPI 支持上传文档并解析入库，用户可以通过 API 上传 txt 和 md 文件。".encode("utf-8"), "text/markdown")},
    )

    response = client.post("/api/query", json={"question": "文档怎么入库？", "top_k": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["question"] == "文档怎么入库？"
    assert payload["answer"] == "real llm test answer"
    assert len(payload["sources"]) >= 1
    source = payload["sources"][0]
    assert source["filename"] == "demo.md"
    assert source["file_type"] == "md"
    assert "chunk_index" in source
    assert "chunk_metadata" in source
    assert source["score"] > 0  # 越大越相关


def test_query_without_documents_returns_404(client: TestClient) -> None:
    response = client.post("/api/query", json={"question": "anything"})

    assert response.status_code == 404
    assert response.json()["detail"] == "No documents have been uploaded."


def test_query_low_relevance_rejects(client: TestClient, monkeypatch) -> None:
    """低相关度应触发拒答，sources 为空且 answer 为固定文案。"""
    llm_called = False

    def _tracked_complete(self, prompt: str) -> str:
        nonlocal llm_called
        llm_called = True
        return "should not be called"

    monkeypatch.setattr(LLMClient, "complete", _tracked_complete)
    # 强制 min_score=1.1，任何 cosine score 都低于此阈值，保证触发拒答
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_MIN_SCORE", "1.1")
    get_settings.cache_clear()

    # 上传一个小文档
    client.post(
        "/api/documents/upload",
        files={"file": ("note.txt", "今天天气很好。".encode("utf-8"), "text/plain")},
    )

    response = client.post(
        "/api/query",
        json={"question": "如何配置数据库连接池参数？", "top_k": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sources"] == []
    assert "没有找到足够依据" in payload["answer"]
    # 拒答不应走到 LLM 调用
    assert not llm_called


def test_query_without_llm_api_key_returns_error(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("AI_LEARNING_LLM_API_KEY", "")
    # 降低检索阈值以确保检索命中，让请求走到 LLM 调用阶段
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_MIN_SCORE", "0.0")
    get_settings.cache_clear()

    client.post(
        "/api/documents/upload",
        files={"file": ("demo.md", "FastAPI 支持上传文档并解析入库。".encode("utf-8"), "text/markdown")},
    )

    response = client.post("/api/query", json={"question": "文档怎么入库？", "top_k": 1})

    assert response.status_code == 503
    assert "AI_LEARNING_LLM_API_KEY" in response.json()["detail"]
