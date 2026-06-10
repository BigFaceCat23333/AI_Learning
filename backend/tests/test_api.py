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
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

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


def test_upload_document_writes_chunks(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("demo.md", b"FastAPI knowledge base document", "text/markdown")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == 1
    assert payload["filename"] == "demo.md"
    assert payload["file_type"] == "md"
    assert payload["chunk_count"] == 1


def test_upload_document_rejects_unsupported_extension(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("demo.pdf", b"content", "application/pdf")},
    )

    assert response.status_code == 400
    assert "supported" in response.json()["detail"]


def test_query_returns_answer_and_sources(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(LLMClient, "complete", lambda self, prompt: "real llm test answer")

    client.post(
        "/api/documents/upload",
        files={"file": ("demo.md", "FastAPI 支持上传文档并解析入库".encode("utf-8"), "text/markdown")},
    )

    response = client.post("/api/query", json={"question": "文档怎么入库？", "top_k": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["question"] == "文档怎么入库？"
    assert payload["answer"] == "real llm test answer"
    assert payload["sources"][0]["filename"] == "demo.md"
    assert "上传文档" in payload["sources"][0]["chunk_text"]


def test_query_without_api_key_returns_error(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("AI_LEARNING_LLM_API_KEY", "")
    get_settings.cache_clear()

    client.post(
        "/api/documents/upload",
        files={"file": ("demo.md", "FastAPI 支持上传文档并解析入库".encode("utf-8"), "text/markdown")},
    )

    response = client.post("/api/query", json={"question": "文档怎么入库？", "top_k": 1})

    assert response.status_code == 503
    assert "AI_LEARNING_LLM_API_KEY" in response.json()["detail"]


def test_query_without_documents_returns_error(client: TestClient) -> None:
    response = client.post("/api/query", json={"question": "anything"})

    assert response.status_code == 404
    assert response.json()["detail"] == "No documents have been uploaded."
