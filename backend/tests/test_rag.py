import os
from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import text

from ai_learning.core.config import Settings, get_settings
from ai_learning.db import Base, get_engine, get_session_factory
from ai_learning.models import Document, DocumentChunk, User
from ai_learning.rag.embedder import (
    EmbeddingCancelledError,
    EmbeddingServiceError,
    MockEmbeddingClient,
    OpenAICompatibleEmbeddingClient,
    get_embedding_client,
)
from ai_learning.rag.loader import (
    load_documents,
    parse_text_file,
    split_chunks,
    validate_document_filename,
)
from ai_learning.rag.retriever import retrieve
from ai_learning.upload_control import finish_upload, register_upload, request_upload_cancel

POSTGRES_TEST_DATABASE_URL = (
    "postgresql+psycopg://urpapa:postgres@localhost:5432/ai_learning_test"
)


def _create_test_user(session) -> User:
    """在测试数据库中创建一个活跃用户。"""
    from ai_learning.auth import hash_password
    user = User(
        username="admin",
        password_hash=hash_password("test-password"),
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def db_session(monkeypatch, tmp_path) -> Generator:
    """提供测试数据库 Session，含重建表和 mock embedding 配置。"""
    monkeypatch.setenv(
        "AI_LEARNING_DATABASE_URL",
        os.getenv("AI_LEARNING_TEST_DATABASE_URL", POSTGRES_TEST_DATABASE_URL),
    )
    monkeypatch.setenv("AI_LEARNING_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("AI_LEARNING_EMBEDDING_DIMENSIONS", "1024")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_MIN_SCORE", "0.0")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_TOP_K", "5")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_CANDIDATE_K", "20")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    engine = get_engine()
    # 重建表
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("DROP TABLE IF EXISTS document_chunks CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS documents CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
    import ai_learning.models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    session = get_session_factory()()
    _create_test_user(session)
    yield session
    session.close()

    # 清理
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS document_chunks CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS documents CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


# ── loader 测试 ──


def test_load_documents_removes_empty_values() -> None:
    assert load_documents([" hello ", "", "world"]) == ["hello", "world"]


def test_validate_document_filename_rejects_unsupported_extension() -> None:
    try:
        validate_document_filename("demo.pdf")
    except ValueError as exc:
        assert "supported" in str(exc)
    else:
        raise AssertionError("Expected unsupported extension to fail")


def test_parse_text_file_rejects_empty_content() -> None:
    try:
        parse_text_file(b"   ")
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("Expected empty file to fail")


def test_parse_text_file_supports_utf8_bom() -> None:
    """UTF-8 BOM 不应出现在解析后的正文中。"""
    assert parse_text_file("兽王".encode("utf-8-sig")) == "兽王"


def test_parse_text_file_supports_gb18030() -> None:
    """兼容 Windows 中文 TXT 常见的 GBK/GB18030 编码。"""
    text = "兽王\r\n作者：雨魔"
    assert parse_text_file(text.encode("gb18030")) == text


def test_parse_text_file_rejects_unknown_encoding() -> None:
    with pytest.raises(ValueError, match="UTF-8 or GB18030"):
        parse_text_file(b"\xff\xff")


def test_split_chunks_uses_overlap() -> None:
    chunks = split_chunks("abcdefghij", chunk_size=6, overlap=2)
    assert len(chunks) >= 2
    # 验证 chunk_metadata 字段存在
    for chunk in chunks:
        assert chunk.text
        assert chunk.chunk_metadata
        assert "filename" in chunk.chunk_metadata
        assert "file_type" in chunk.chunk_metadata
        assert "chunk_index" in chunk.chunk_metadata
        assert "char_start" in chunk.chunk_metadata
        assert "char_end" in chunk.chunk_metadata
        assert chunk.content_hash
        assert chunk.char_count > 0


def test_split_chunks_with_metadata() -> None:
    """验证 chunk 携带的元数据正确。"""
    text = "Hello World Test Content"
    chunks = split_chunks(
        text,
        filename="test.md",
        file_type="md",
        chunk_size=100,
        overlap=0,
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.text == "Hello World Test Content"
    assert chunk.chunk_metadata["filename"] == "test.md"
    assert chunk.chunk_metadata["file_type"] == "md"
    assert chunk.chunk_metadata["chunk_index"] == 0
    assert chunk.chunk_metadata["char_start"] == 0
    assert chunk.char_count == len("Hello World Test Content")
    assert len(chunk.content_hash) == 64  # SHA256 hex


def test_split_chunks_content_hash_stable() -> None:
    """同一文本多次分块，content_hash 应相同。"""
    text = "Stable content for hashing"
    chunks1 = split_chunks(text)
    chunks2 = split_chunks(text)
    assert len(chunks1) == len(chunks2)
    for c1, c2 in zip(chunks1, chunks2):
        assert c1.content_hash == c2.content_hash


# ── embedder 测试 ──


def test_mock_embedder_stable_vector() -> None:
    """Mock embedding 对相同输入返回相同的向量。"""
    settings = Settings(embedding_provider="mock", embedding_dimensions=128)
    client = MockEmbeddingClient(settings)
    vec1 = client.embed_query("hello")
    vec2 = client.embed_query("hello")
    assert vec1 == vec2
    assert len(vec1) == 128


def test_mock_embedder_different_inputs_different_vectors() -> None:
    """不同输入应产生不同的向量。"""
    settings = Settings(embedding_provider="mock", embedding_dimensions=128)
    client = MockEmbeddingClient(settings)
    vec1 = client.embed_query("hello")
    vec2 = client.embed_query("world")
    assert vec1 != vec2


def test_mock_embedder_batch_embed_texts() -> None:
    """批量 embedding 应返回正确数量的向量。"""
    settings = Settings(embedding_provider="mock", embedding_dimensions=128)
    client = MockEmbeddingClient(settings)
    texts = ["first", "second", "third"]
    vectors = client.embed_texts(texts)
    assert len(vectors) == 3
    for vec in vectors:
        assert len(vec) == 128


def test_mock_embedder_empty_input() -> None:
    """空列表应返回空列表。"""
    settings = Settings(embedding_provider="mock")
    client = MockEmbeddingClient(settings)
    assert client.embed_texts([]) == []


def test_mock_embedder_factory() -> None:
    """通过工厂函数创建 mock embedding 客户端。"""
    settings = Settings(embedding_provider="mock", embedding_dimensions=128)
    client = get_embedding_client(settings)
    assert isinstance(client, MockEmbeddingClient)
    vec = client.embed_query("test")
    assert len(vec) == 128


def test_openai_compatible_missing_config_raises() -> None:
    """缺少 base_url 或 api_key 时应抛出 ValueError。"""
    # 缺少 base_url
    with pytest.raises(ValueError, match="EMBEDDING_BASE_URL"):
        get_embedding_client(
            Settings(embedding_provider="openai_compatible", embedding_base_url="")
        )
    # 缺少 api_key
    with pytest.raises(ValueError, match="EMBEDDING_API_KEY"):
        get_embedding_client(
            Settings(
                embedding_provider="openai_compatible",
                embedding_base_url="https://api.example.com/v1",
                embedding_api_key=None,
            )
        )


def test_openai_compatible_sends_dimensions(monkeypatch) -> None:
    """OpenAI 兼容接口请求应显式传递 dimensions，匹配 pgvector 表维度。"""
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {
                        "embedding": [0.1] * 1024,
                    }
                ]
            }

    def fake_post(url: str, headers: dict, json: dict, timeout: int) -> FakeResponse:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("ai_learning.rag.embedder.httpx.post", fake_post)
    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        embedding_api_key="test-key",
        embedding_model="text-embedding-v3",
        embedding_dimensions=1024,
    )

    client = OpenAICompatibleEmbeddingClient(settings)
    vectors = client.embed_texts(["hello"])

    assert len(vectors) == 1
    assert captured["url"].endswith("/compatible-mode/v1/embeddings")
    assert captured["json"] == {
        "model": "text-embedding-v3",
        "input": ["hello"],
        "dimensions": 1024,
    }


def test_openai_compatible_batches_large_input_in_order(monkeypatch) -> None:
    """大文档分批请求 Embedding，并按输入顺序合并向量。"""
    captured_batches: list[list[str]] = []

    class FakeResponse:
        def __init__(self, inputs: list[str]) -> None:
            self._inputs = inputs

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {
                        "index": index,
                        "embedding": [float(text.removeprefix("text-"))] * 4,
                    }
                    for index, text in reversed(list(enumerate(self._inputs)))
                ]
            }

    def fake_post(url: str, headers: dict, json: dict, timeout: int) -> FakeResponse:
        batch = list(json["input"])
        captured_batches.append(batch)
        return FakeResponse(batch)

    monkeypatch.setattr("ai_learning.rag.embedder.httpx.post", fake_post)
    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_base_url="https://api.example.com/v1",
        embedding_api_key="test-key",
        embedding_dimensions=4,
    )

    texts = [f"text-{index}" for index in range(23)]
    vectors = OpenAICompatibleEmbeddingClient(settings).embed_texts(texts)

    assert [len(batch) for batch in captured_batches] == [10, 10, 3]
    assert [vector[0] for vector in vectors] == [float(index) for index in range(23)]


def test_openai_compatible_stops_after_cancel(monkeypatch) -> None:
    """取消后不再调用后续 Embedding 批次。"""
    captured_batches: list[list[str]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{"embedding": [0.1] * 4} for _ in range(10)]}

    def fake_post(url: str, headers: dict, json: dict, timeout: int) -> FakeResponse:
        captured_batches.append(list(json["input"]))
        return FakeResponse()

    monkeypatch.setattr("ai_learning.rag.embedder.httpx.post", fake_post)
    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_base_url="https://api.example.com/v1",
        embedding_api_key="test-key",
        embedding_dimensions=4,
    )

    with pytest.raises(EmbeddingCancelledError, match="已取消"):
        OpenAICompatibleEmbeddingClient(settings).embed_texts(
            [f"text-{index}" for index in range(20)],
            should_cancel=lambda: len(captured_batches) >= 1,
        )

    assert len(captured_batches) == 1


def test_upload_cancel_can_arrive_before_upload_registration() -> None:
    """文件仍在传输时发出的取消标记，应由随后开始的后端处理读取。"""
    upload_id = "cancel-before-register"
    request_upload_cancel(1, upload_id)
    try:
        assert register_upload(1, upload_id).is_set()
        assert not register_upload(2, upload_id).is_set()
    finally:
        finish_upload(1, upload_id)
        finish_upload(2, upload_id)


# ── retriever 测试 ──


def _make_chunk(
    doc: Document,
    chunk_index: int,
    chunk_text: str,
    embedding: list[float],
) -> DocumentChunk:
    """辅助函数：创建带完整字段的 DocumentChunk。"""
    return DocumentChunk(
        document=doc,
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        embedding=embedding,
        chunk_metadata={
            "filename": doc.filename,
            "file_type": doc.file_type,
            "chunk_index": chunk_index,
            "char_start": 0,
            "char_end": len(chunk_text),
        },
        content_hash="abc123",
        char_count=len(chunk_text),
    )


def test_retrieve_returns_results_sorted_by_score(db_session) -> None:
    """检索结果应按 score 降序排列。"""
    settings = get_settings()
    embedder = MockEmbeddingClient(settings)

    user_id = db_session.query(User).first().id
    doc = Document(user_id=user_id, filename="test.md", file_type="md", saved_path="/tmp/test.md", raw_text="test")
    # chunk2 的文本与查询文本更接近（完全匹配），mock embedding 会产生相同的向量
    query = "python fastapi service"
    chunks_data = [
        ("database migration guide for postgres", 0),
        ("python fastapi service", 1),  # 应排第一
        ("frontend css styling tips", 2),
    ]
    doc.chunks = [
        _make_chunk(doc, idx, text, embedder.embed_query(text))
        for text, idx in chunks_data
    ]
    db_session.add(doc)
    db_session.commit()

    results = retrieve(query=query, db=db_session, embedder=embedder, top_k=3)

    assert len(results) >= 1
    # 验证按 score 降序
    for i in range(len(results) - 1):
        assert results[i].score >= results[i + 1].score
    # 完全匹配的 chunk 应包含 "python fastapi service"
    assert any("python fastapi service" in r.chunk.chunk_text for r in results)
    # 验证 document 关系已加载
    for r in results:
        assert r.chunk.document is not None
        assert r.chunk.document.filename == "test.md"


def test_retrieve_top_k_limits_results(db_session) -> None:
    """top_k 应限制返回结果数。"""
    settings = get_settings()
    embedder = MockEmbeddingClient(settings)

    user_id = db_session.query(User).first().id
    doc = Document(user_id=user_id, filename="multi.md", file_type="md", saved_path="/tmp/multi.md", raw_text="multi")
    doc.chunks = [
        _make_chunk(doc, i, f"chunk content number {i}", embedder.embed_query(f"chunk {i}"))
        for i in range(10)
    ]
    db_session.add(doc)
    db_session.commit()

    top_k = 3
    results = retrieve(query="chunk 5", db=db_session, embedder=embedder, top_k=top_k)
    assert len(results) == top_k


def test_retrieve_min_score_filters_low_relevance(db_session) -> None:
    """min_score 应过滤低于阈值的 chunk。"""
    settings = get_settings()
    embedder = MockEmbeddingClient(settings)

    user_id = db_session.query(User).first().id
    doc = Document(user_id=user_id, filename="note.md", file_type="md", saved_path="/tmp/note.md", raw_text="note")
    doc.chunks = [
        _make_chunk(doc, 0, "天气很好适合出去玩", embedder.embed_query("天气很好适合出去玩")),
    ]
    db_session.add(doc)
    db_session.commit()

    # 用极高的 min_score 过滤所有结果
    results = retrieve(query="不相关的查询", db=db_session, embedder=embedder, min_score=1.1)
    assert results == []

    # 用极低的 min_score 应召回
    results_low = retrieve(query="天气", db=db_session, embedder=embedder, min_score=0.0)
    assert len(results_low) >= 1


def test_retrieve_empty_database_returns_empty(db_session) -> None:
    """无 chunk 时检索应返回空列表。"""
    settings = get_settings()
    embedder = MockEmbeddingClient(settings)
    results = retrieve(query="anything", db=db_session, embedder=embedder)
    assert results == []


def test_retrieve_defaults_from_settings(db_session) -> None:
    """未显式传参时应从 Settings 取默认值。"""
    settings = get_settings()
    embedder = MockEmbeddingClient(settings)

    user_id = db_session.query(User).first().id
    doc = Document(user_id=user_id, filename="default.md", file_type="md", saved_path="/tmp/default.md", raw_text="default")
    doc.chunks = [
        _make_chunk(doc, i, f"content {i}", embedder.embed_query(f"content {i}"))
        for i in range(10)
    ]
    db_session.add(doc)
    db_session.commit()

    # 不传 top_k、candidate_k、min_score，应从 settings 默认值
    results = retrieve(query="content 3", db=db_session, embedder=embedder)
    assert len(results) <= settings.retrieval_top_k
    assert all(r.score >= settings.retrieval_min_score for r in results)


def test_embedding_service_error_propagation(db_session, monkeypatch) -> None:
    """EmbeddingServiceError 应从 routes 层被捕获并转为 503。"""
    from fastapi.testclient import TestClient
    from ai_learning.main import create_app
    from ai_learning.core.llm_client import LLMClient
    from ai_learning.auth import create_token
    from ai_learning.core.config import get_settings as _get_settings

    monkeypatch.setenv("AI_LEARNING_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("AI_LEARNING_AUTH_SECRET", "test-secret-key-at-least-32-chars-long!!")
    _get_settings.cache_clear()

    app = create_app()
    client = TestClient(app)

    # 创建用户并登录获取 Cookie
    user = db_session.query(User).first()
    token = create_token(user.id)

    # 上传文档
    client.post(
        "/api/documents/upload",
        files={"file": ("demo.md", "FastAPI 测试文档内容。".encode("utf-8"), "text/markdown")},
        cookies={_get_settings().auth_cookie_name: token},
    )

    # mock embed_query 抛出 EmbeddingServiceError
    def _failing_embed_query(self, query: str) -> list[float]:
        raise EmbeddingServiceError("mock upstream embedding failure")

    monkeypatch.setattr(
        MockEmbeddingClient, "embed_query", _failing_embed_query
    )

    monkeypatch.setattr(LLMClient, "complete", lambda self, prompt: "should not be called")
    response = client.post(
        "/api/query",
        json={"question": "测试？", "top_k": 1},
        cookies={_get_settings().auth_cookie_name: token},
    )

    assert response.status_code == 503
    assert "mock upstream embedding failure" in response.json()["detail"]

    _get_settings.cache_clear()


# ── 配置默认值测试 ──


def test_sql_echo_defaults_to_false() -> None:
    """sql_echo 默认应为 False。"""
    settings = Settings()
    assert settings.sql_echo is False


def test_rag_debug_logs_defaults_to_false() -> None:
    """rag_debug_logs 默认应为 False。"""
    settings = Settings()
    assert settings.rag_debug_logs is False


# ── 日志安全测试 ──


def test_embedding_log_excludes_api_key(monkeypatch, caplog) -> None:
    """Embedding 调试日志不应包含 API Key。"""
    import logging

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {"embedding": [0.1] * 128},
                ]
            }

    def fake_post(url: str, headers: dict, json: dict, timeout: int) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("ai_learning.rag.embedder.httpx.post", fake_post)
    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_base_url="https://api.example.com/v1",
        embedding_api_key="sk-secret-do-not-leak",
        embedding_dimensions=128,
        rag_debug_logs=True,
    )

    caplog.set_level(logging.INFO)
    client = OpenAICompatibleEmbeddingClient(settings)
    client.embed_texts(["test input"])

    log_text = caplog.text
    assert "Embedding 调用成功" in log_text
    assert "sk-secret-do-not-leak" not in log_text
    assert "Bearer" not in log_text


def test_embedding_no_logs_when_flag_disabled(monkeypatch, caplog) -> None:
    """rag_debug_logs=False 时不输出 embedding 调用日志。"""
    import logging

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{"embedding": [0.1] * 128}]}

    def fake_post(url: str, headers: dict, json: dict, timeout: int) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("ai_learning.rag.embedder.httpx.post", fake_post)
    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_base_url="https://api.example.com/v1",
        embedding_api_key="sk-secret",
        embedding_dimensions=128,
        rag_debug_logs=False,
    )

    caplog.set_level(logging.INFO)
    client = OpenAICompatibleEmbeddingClient(settings)
    client.embed_texts(["test"])

    # 日志应不包含调用开始/成功信息
    assert "Embedding 调用开始" not in caplog.text
    assert "Embedding 调用成功" not in caplog.text


def test_setup_logging_enables_info_when_flag_true(monkeypatch) -> None:
    """rag_debug_logs=True 时，ai_learning logger 应启用 INFO 级别。"""
    import logging
    from ai_learning.main import setup_logging
    from ai_learning.core.config import get_settings

    # 先重置，确保初始状态
    project_logger = logging.getLogger("ai_learning")
    project_logger.setLevel(logging.WARNING)
    for h in list(project_logger.handlers):
        project_logger.removeHandler(h)

    monkeypatch.setenv("AI_LEARNING_RAG_DEBUG_LOGS", "true")
    get_settings.cache_clear()

    setup_logging()

    assert project_logger.isEnabledFor(logging.INFO) is True
    assert project_logger.level == logging.INFO

    get_settings.cache_clear()


def test_setup_logging_keeps_warning_when_flag_false(monkeypatch) -> None:
    """rag_debug_logs=False 时不应提升 ai_learning logger 级别。"""
    import logging
    from ai_learning.main import setup_logging
    from ai_learning.core.config import get_settings

    project_logger = logging.getLogger("ai_learning")
    project_logger.setLevel(logging.WARNING)
    for h in list(project_logger.handlers):
        project_logger.removeHandler(h)

    monkeypatch.setenv("AI_LEARNING_RAG_DEBUG_LOGS", "false")
    get_settings.cache_clear()

    setup_logging()

    # 不应被改为 INFO
    assert project_logger.level != logging.INFO

    get_settings.cache_clear()


def test_retrieve_user_id_isolation(db_session) -> None:
    """retrieve 传入 user_id 时应过滤其他用户分片。"""
    from ai_learning.auth import hash_password as hp
    from ai_learning.rag.embedder import MockEmbeddingClient

    settings = get_settings()
    embedder = MockEmbeddingClient(settings)

    # 获取或创建两个用户
    existing_user = db_session.query(User).first()
    u2 = User(
        username="user2_retrieve",
        password_hash=hp("test"),
        is_active=True,
    )
    db_session.add(u2)
    db_session.commit()

    # 每个用户创建一个文档
    doc1 = Document(user_id=existing_user.id, filename="u1.md", file_type="md", saved_path="/tmp/u1.md", raw_text="u1")
    doc1.chunks = [
        DocumentChunk(
            document=doc1, chunk_index=0, chunk_text="admin specific about databases",
            embedding=embedder.embed_query("admin specific about databases"),
            chunk_metadata={"filename": "u1.md", "file_type": "md", "chunk_index": 0, "char_start": 0, "char_end": 10},
            content_hash="a1", char_count=10,
        )
    ]
    db_session.add(doc1)

    user2_text = "user2 specific about frontend"
    doc2 = Document(user_id=u2.id, filename="u2.md", file_type="md", saved_path="/tmp/u2.md", raw_text="u2")
    doc2.chunks = [
        DocumentChunk(
            document=doc2, chunk_index=0, chunk_text=user2_text,
            embedding=embedder.embed_query(user2_text),
            chunk_metadata={"filename": "u2.md", "file_type": "md", "chunk_index": 0, "char_start": 0, "char_end": len(user2_text)},
            content_hash="b2", char_count=len(user2_text),
        )
    ]
    db_session.add(doc2)
    db_session.commit()

    # 以 existing_user 身份检索：查询 admin 的文档内容
    results = retrieve(query="admin specific about databases", db=db_session, embedder=embedder, user_id=existing_user.id, top_k=10, min_score=0.0)

    assert len(results) >= 1
    for r in results:
        assert r.chunk.document.user_id == existing_user.id
        assert r.chunk.document.filename != "u2.md"

    # 以 u2 身份检索：用完全相同的文本查询确保 mock embedding 精确匹配
    results2 = retrieve(query=user2_text, db=db_session, embedder=embedder, user_id=u2.id, top_k=10, min_score=0.0)

    assert len(results2) >= 1
    for r in results2:
        assert r.chunk.document.user_id == u2.id
        assert r.chunk.document.filename != "u1.md"


def test_retrieve_excludes_soft_deleted_documents(db_session) -> None:
    """逻辑删除文档的分片即使仍在数据库中，也不能参与向量召回。"""
    settings = get_settings()
    embedder = MockEmbeddingClient(settings)
    user_id = db_session.query(User).first().id
    query = "soft deleted unique content"

    deleted_doc = Document(
        user_id=user_id,
        filename="deleted.md",
        file_type="md",
        saved_path="/tmp/deleted.md",
        raw_text=query,
        deleted_at=datetime.utcnow(),
    )
    deleted_doc.chunks = [
        _make_chunk(deleted_doc, 0, query, embedder.embed_query(query)),
    ]
    active_doc = Document(
        user_id=user_id,
        filename="active.md",
        file_type="md",
        saved_path="/tmp/active.md",
        raw_text="active fallback content",
    )
    active_doc.chunks = [
        _make_chunk(
            active_doc,
            0,
            "active fallback content",
            embedder.embed_query("active fallback content"),
        ),
    ]
    db_session.add_all([deleted_doc, active_doc])
    db_session.commit()

    results = retrieve(
        query=query,
        db=db_session,
        embedder=embedder,
        user_id=user_id,
        top_k=10,
        min_score=-1.0,
    )

    assert results
    assert all(result.chunk.document.filename != "deleted.md" for result in results)


def test_embedding_log_includes_status_code_on_http_error(monkeypatch, caplog) -> None:
    """Embedding HTTP 4xx/5xx 失败日志应包含 status_code。"""
    import logging
    import httpx

    class FakeResponse:
        status_code = 429

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "Too Many Requests",
                request=httpx.Request("POST", "https://api.example.com/v1/embeddings"),
                response=httpx.Response(status_code=self.status_code),
            )

    def fake_post(url: str, headers: dict, json: dict, timeout: int) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("ai_learning.rag.embedder.httpx.post", fake_post)
    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_base_url="https://api.example.com/v1",
        embedding_api_key="sk-test",
        rag_debug_logs=True,
    )

    caplog.set_level(logging.WARNING)
    client = OpenAICompatibleEmbeddingClient(settings)

    try:
        client.embed_texts(["test"])
    except httpx.HTTPStatusError:
        pass

    log_text = caplog.text
    assert "Embedding 调用失败" in log_text
    assert "status_code=429" in log_text
