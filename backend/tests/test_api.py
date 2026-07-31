from collections.abc import Generator
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from unittest.mock import ANY

from ai_learning.auth import hash_password
from ai_learning.core.config import get_settings
from ai_learning.core.llm_client import LLMClient
from ai_learning.db import get_engine, get_session_factory
from ai_learning.main import create_app


POSTGRES_TEST_DATABASE_URL = "postgresql+psycopg://urpapa:postgres@localhost:5432/ai_learning_test"

TEST_AUTH_SECRET = "test-secret-key-at-least-32-chars-long!!"


def recreate_tables() -> None:
    """重建测试数据库表结构。"""
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("DROP TABLE IF EXISTS document_chunks CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS documents CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS users CASCADE"))
    from ai_learning.db import Base
    from ai_learning import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def clear_database() -> None:
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE document_chunks, documents, users RESTART IDENTITY CASCADE"))


def _create_test_user(username: str = "admin") -> int:
    """在测试数据库中创建一个活跃用户，返回 user_id。"""
    from ai_learning import models as _models
    session = get_session_factory()()
    try:
        user = _models.User(
            username=username,
            password_hash=hash_password("test-password"),
            is_active=True,
        )
        session.add(user)
        session.commit()
        return user.id
    finally:
        session.close()


def _login(client: TestClient, username: str = "admin", password: str = "test-password") -> None:
    """在 TestClient 上执行登录，设置 Cookie。"""
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, f"Login failed: {response.json()}"


@pytest.fixture
def client(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv(
        "AI_LEARNING_DATABASE_URL",
        os.getenv("AI_LEARNING_TEST_DATABASE_URL", POSTGRES_TEST_DATABASE_URL),
    )
    monkeypatch.setenv("AI_LEARNING_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AI_LEARNING_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("AI_LEARNING_AUTH_SECRET", TEST_AUTH_SECRET)
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    recreate_tables()
    # 预置测试用户
    _create_test_user("admin")

    app = create_app()
    with TestClient(app) as test_client:
        clear_database()
        # 重建表后会清空数据，需要重新创建测试用户
        _create_test_user("admin")
        yield test_client
        clear_database()

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


# ── 公开接口 ──


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── 认证接口 ──


def test_login_success_sets_cookie(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] >= 1
    assert payload["username"] == "admin"
    # Cookie 应包含 ai_learning_session
    cookies = response.headers.get("set-cookie", "")
    assert "ai_learning_session" in cookies
    assert "httponly" in cookies.lower()
    assert "samesite=lax" in cookies.lower()
    assert "path=/" in cookies.lower()


def test_login_username_trimmed_and_lower(client: TestClient) -> None:
    """用户名自动去除首尾空格并转小写。"""
    response = client.post("/api/auth/login", json={"username": "  Admin  ", "password": "test-password"})

    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_login_wrong_password_returns_401(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401
    assert "用户名或密码错误" in response.json()["detail"]


def test_login_wrong_username_returns_401(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "nonexistent", "password": "test-password"})

    assert response.status_code == 401
    assert "用户名或密码错误" in response.json()["detail"]


def test_login_inactive_user_returns_401(client: TestClient) -> None:
    """停用用户返回 401，与错误用户名/密码文案一致。"""
    from ai_learning import models as _models
    session = get_session_factory()()
    try:
        inactive = _models.User(
            username="inactive",
            password_hash=hash_password("test"),
            is_active=False,
        )
        session.add(inactive)
        session.commit()
    finally:
        session.close()

    response = client.post("/api/auth/login", json={"username": "inactive", "password": "test"})

    assert response.status_code == 401
    assert "用户名或密码错误" in response.json()["detail"]


def test_me_returns_user(client: TestClient) -> None:
    _login(client)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "admin"
    assert "user_id" in payload


def test_me_without_login_returns_401(client: TestClient) -> None:
    response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_logout_clears_cookie(client: TestClient) -> None:
    _login(client)

    response = client.post("/api/auth/logout")

    assert response.status_code == 204
    cookies = response.headers.get("set-cookie", "")
    # 清除 Cookie 应设置空值或过期时间
    assert "ai_learning_session" in cookies.lower()


def test_logout_requires_auth(client: TestClient) -> None:
    response = client.post("/api/auth/logout")

    assert response.status_code == 401


# ── 密码哈希单元测试 ──


def test_password_hash_not_equal_to_password() -> None:
    pwd = "my-secret"
    h = hash_password(pwd)
    assert h != pwd
    assert h.startswith("$argon2id$")


def test_same_password_different_hash() -> None:
    pwd = "same-password"
    h1 = hash_password(pwd)
    h2 = hash_password(pwd)
    assert h1 != h2  # 不同盐值产生不同哈希
    from ai_learning.auth import verify_password
    assert verify_password(pwd, h1)
    assert verify_password(pwd, h2)


# ── 鉴权保护测试 ──


def test_upload_without_login_returns_401(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("demo.txt", b"content", "text/plain")},
    )

    assert response.status_code == 401


def test_query_without_login_returns_401(client: TestClient) -> None:
    response = client.post("/api/query", json={"question": "test"})

    assert response.status_code == 401


def test_list_documents_without_login_returns_401(client: TestClient) -> None:
    response = client.get("/api/documents")

    assert response.status_code == 401


def test_download_without_login_returns_401(client: TestClient) -> None:
    response = client.get("/api/documents/1/download")

    assert response.status_code == 401


def test_agent_without_login_returns_401(client: TestClient) -> None:
    response = client.post("/api/agent", json={"task": "test"})

    assert response.status_code == 401


# ── 用户隔离测试 ──


def test_users_have_isolated_documents(client: TestClient) -> None:
    """两个用户的文档列表互相不可见。"""
    from ai_learning import models as _models

    # 创建第二个用户
    session = get_session_factory()()
    try:
        user2 = _models.User(
            username="user2",
            password_hash=hash_password("password2"),
            is_active=True,
        )
        session.add(user2)
        session.commit()
        user2_id = user2.id
    finally:
        session.close()

    # admin 上传一个文档
    _login(client, "admin", "test-password")
    client.post(
        "/api/documents/upload",
        files={"file": ("admin.txt", "admin document content".encode("utf-8"), "text/plain")},
    )

    # 验证 admin 看到自己的文档
    response = client.get("/api/documents")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["filename"] == "admin.txt"

    # 退出并用 user2 登录
    client.post("/api/auth/logout")
    _login(client, "user2", "password2")

    # user2 看不到 admin 的文档
    response = client.get("/api/documents")
    assert response.status_code == 200
    assert response.json() == []

    # user2 上传自己的文档
    client.post(
        "/api/documents/upload",
        files={"file": ("user2.txt", "user2 document content".encode("utf-8"), "text/plain")},
    )

    # user2 只看到自己的文档
    response = client.get("/api/documents")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["filename"] == "user2.txt"


def test_user_cannot_download_other_user_document(client: TestClient) -> None:
    """其他用户的文档下载返回 404。"""
    from ai_learning import models as _models

    # 创建 user2
    session = get_session_factory()()
    try:
        user2 = _models.User(
            username="user2",
            password_hash=hash_password("password2"),
            is_active=True,
        )
        session.add(user2)
        session.commit()
    finally:
        session.close()

    # admin 上传文档
    _login(client, "admin", "test-password")
    client.post(
        "/api/documents/upload",
        files={"file": ("secret.md", "# Secret\n\nOnly admin can see this.".encode("utf-8"), "text/markdown")},
    )
    client.post("/api/auth/logout")

    # user2 登录，尝试下载 admin 的文档
    _login(client, "user2", "password2")
    response = client.get("/api/documents/1/download")

    assert response.status_code == 404


# ── 业务功能测试（登录后）──


def test_upload_txt_writes_chunks(client: TestClient) -> None:
    _login(client)
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
    _login(client)
    response = client.post(
        "/api/documents/upload",
        files={"file": ("readme.md", "# FastAPI\n\nFastAPI 支持上传文档并解析入库。".encode("utf-8"), "text/markdown")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["file_type"] == "md"
    assert payload["chunk_count"] >= 1


def test_upload_rejects_pdf(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/api/documents/upload",
        files={"file": ("demo.pdf", b"content", "application/pdf")},
    )

    assert response.status_code == 400
    assert "supported" in response.json()["detail"]


def test_upload_rejects_unsupported_extension(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/api/documents/upload",
        files={"file": ("demo.docx", b"content", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 400
    assert "supported" in response.json()["detail"]


def test_upload_rejects_empty_document(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/api/documents/upload",
        files={"file": ("empty.txt", b"   ", "text/plain")},
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_query_returns_answer_and_sources(client: TestClient, monkeypatch) -> None:
    _login(client)
    monkeypatch.setattr(LLMClient, "complete", lambda self, prompt: "real llm test answer")
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
    assert source["score"] > 0


def test_query_without_documents_returns_404(client: TestClient) -> None:
    _login(client)
    response = client.post("/api/query", json={"question": "anything"})

    assert response.status_code == 404
    assert response.json()["detail"] == "No documents have been uploaded."


def test_query_low_relevance_rejects(client: TestClient, monkeypatch) -> None:
    _login(client)
    llm_called = False

    def _tracked_complete(self, prompt: str) -> str:
        nonlocal llm_called
        llm_called = True
        return "should not be called"

    monkeypatch.setattr(LLMClient, "complete", _tracked_complete)
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_MIN_SCORE", "1.1")
    get_settings.cache_clear()

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
    assert not llm_called


def test_query_without_llm_api_key_returns_error(client: TestClient, monkeypatch) -> None:
    _login(client)
    monkeypatch.setenv("AI_LEARNING_LLM_API_KEY", "")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_MIN_SCORE", "0.0")
    get_settings.cache_clear()

    client.post(
        "/api/documents/upload",
        files={"file": ("demo.md", "FastAPI 支持上传文档并解析入库。".encode("utf-8"), "text/markdown")},
    )

    response = client.post("/api/query", json={"question": "文档怎么入库？", "top_k": 1})

    assert response.status_code == 503
    assert "AI_LEARNING_LLM_API_KEY" in response.json()["detail"]


# ── 历史文档列表与下载接口测试 ──


def test_list_documents_empty_returns_empty_array(client: TestClient) -> None:
    _login(client)
    response = client.get("/api/documents")

    assert response.status_code == 200
    payload = response.json()
    assert payload == []


def test_list_documents_returns_latest_first(client: TestClient) -> None:
    _login(client)
    client.post(
        "/api/documents/upload",
        files={"file": ("a.txt", "内容 A 用于测试。".encode("utf-8"), "text/plain")},
    )
    client.post(
        "/api/documents/upload",
        files={"file": ("b.md", "# 内容 B\n\n用于测试。".encode("utf-8"), "text/markdown")},
    )

    response = client.get("/api/documents")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 2

    first = payload[0]
    second = payload[1]
    assert first["filename"] == "b.md"
    assert first["file_type"] == "md"
    assert first["chunk_count"] >= 1
    assert "created_at" in first

    assert second["filename"] == "a.txt"
    assert second["file_type"] == "txt"
    assert second["chunk_count"] >= 1
    assert "created_at" in second

    assert first["created_at"] >= second["created_at"]


def test_download_document_returns_file(client: TestClient) -> None:
    _login(client)
    original = "这是用于下载测试的原始内容。\n第二行。".encode("utf-8")
    client.post(
        "/api/documents/upload",
        files={"file": ("readme.md", original, "text/markdown")},
    )

    response = client.get("/api/documents/1/download")

    assert response.status_code == 200
    content_disposition = response.headers.get("content-disposition", "")
    assert "readme.md" in content_disposition
    assert response.content == original


def test_download_nonexistent_document_returns_404(client: TestClient) -> None:
    _login(client)
    response = client.get("/api/documents/99999/download")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_download_missing_file_returns_404(client: TestClient) -> None:
    _login(client)
    from pathlib import Path

    original = "下载后删除文件测试。".encode("utf-8")
    client.post(
        "/api/documents/upload",
        files={"file": ("note.txt", original, "text/plain")},
    )

    response = client.get("/api/documents/1/download")
    assert response.status_code == 200

    from ai_learning.core.config import get_settings
    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    for f in upload_dir.iterdir():
        if f.is_file():
            f.unlink()

    response = client.get("/api/documents/1/download")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_download_path_traversal_rejected(client: TestClient) -> None:
    _login(client)
    from ai_learning import models as _models

    client.post(
        "/api/documents/upload",
        files={"file": ("safe.txt", "安全文件。".encode("utf-8"), "text/plain")},
    )

    session = get_session_factory()()
    try:
        doc = session.query(_models.Document).filter(_models.Document.id == 1).first()
        doc.saved_path = "/etc/passwd"
        session.commit()
    finally:
        session.close()

    response = client.get("/api/documents/1/download")
    assert response.status_code == 404


# ── 登录时序枚举防护测试 ──


def test_login_always_calls_password_verify(monkeypatch) -> None:
    """错误用户名和停用用户分支也必须调用一次密码校验（通过 spy 验证）。"""
    from ai_learning import auth as auth_module

    verify_calls: list[tuple[str, str]] = []
    _original_verify = auth_module._hasher.verify

    def _spy_verify(password: str, password_hash: str) -> bool:
        verify_calls.append((password, password_hash))
        return _original_verify(password, password_hash)

    monkeypatch.setattr(auth_module._hasher, "verify", _spy_verify)

    # 错误用户名
    ok, pwd_ok = auth_module.verify_login_password("foo", None, False)
    assert not ok
    assert not pwd_ok
    assert len(verify_calls) >= 1
    assert verify_calls[-1][1] == auth_module._DUMMY_HASH
    verify_calls.clear()

    # 停用用户（user 不为 None 但 is_active=False）
    ok2, pwd_ok2 = auth_module.verify_login_password("bar", ANY, False)
    assert not ok2
    assert not pwd_ok2
    assert len(verify_calls) >= 1
    assert verify_calls[-1][1] == auth_module._DUMMY_HASH


# ── Cookie 属性测试 ──


def test_login_cookie_max_age_is_28800(client: TestClient) -> None:
    """登录 Cookie 的 Max-Age 应为 28800（8 小时）。"""
    response = client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})

    assert response.status_code == 200
    cookies = response.headers.get("set-cookie", "")
    assert "max-age=28800" in cookies.lower()


def test_login_cookie_secure_when_configured(client: TestClient, monkeypatch) -> None:
    """auth_cookie_secure=true 时 Cookie 应包含 Secure 属性。"""
    monkeypatch.setenv("AI_LEARNING_AUTH_COOKIE_SECURE", "true")
    get_settings.cache_clear()

    response = client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})

    assert response.status_code == 200
    cookies = response.headers.get("set-cookie", "")
    assert "secure" in cookies.lower()

    get_settings.cache_clear()


# ── JWT Token 安全测试 ──


def test_tampered_jwt_returns_401(client: TestClient) -> None:
    """篡改 JWT 签名后应返回 401。"""
    from ai_learning.auth import create_token
    from ai_learning.models import User as UserModel

    session = get_session_factory()()
    try:
        user = session.query(UserModel).filter(UserModel.username == "admin").first()
        token = create_token(user.id)
    finally:
        session.close()

    # 篡改 token 最后一个字符
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    settings = get_settings()
    response = client.get(
        "/api/auth/me",
        cookies={settings.auth_cookie_name: tampered},
    )

    assert response.status_code == 401


def test_expired_jwt_returns_401(client: TestClient, monkeypatch) -> None:
    """过期 JWT 应返回 401。"""
    from ai_learning.auth import create_token
    from ai_learning.models import User as UserModel
    import time

    session = get_session_factory()()
    try:
        user = session.query(UserModel).filter(UserModel.username == "admin").first()
    finally:
        session.close()

    # 缩短 TTL 使 token 在签发后迅速过期
    monkeypatch.setenv("AI_LEARNING_AUTH_TOKEN_TTL_SECONDS", "1")
    get_settings.cache_clear()

    token = create_token(user.id)
    # 等待 token 过期
    time.sleep(1.5)

    settings = get_settings()
    response = client.get(
        "/api/auth/me",
        cookies={settings.auth_cookie_name: token},
    )

    assert response.status_code == 401
    get_settings.cache_clear()


# ── 启动时认证密钥校验测试 ──


def test_startup_rejects_missing_auth_secret(monkeypatch) -> None:
    """缺失 auth_secret 时启动应失败。"""
    monkeypatch.setenv("AI_LEARNING_AUTH_SECRET", "")
    get_settings.cache_clear()

    with pytest.raises(SystemExit):
        from ai_learning.main import _validate_auth_secret
        _validate_auth_secret()

    get_settings.cache_clear()


def test_startup_rejects_short_auth_secret(monkeypatch) -> None:
    """auth_secret 不足 32 字符时启动应失败。"""
    monkeypatch.setenv("AI_LEARNING_AUTH_SECRET", "too-short")
    get_settings.cache_clear()

    with pytest.raises(SystemExit):
        from ai_learning.main import _validate_auth_secret
        _validate_auth_secret()

    get_settings.cache_clear()


def test_startup_rejects_non_positive_ttl(monkeypatch) -> None:
    """auth_token_ttl_seconds <= 0 时启动应失败。"""
    monkeypatch.setenv("AI_LEARNING_AUTH_SECRET", "test-secret-key-at-least-32-chars-long!!")
    monkeypatch.setenv("AI_LEARNING_AUTH_TOKEN_TTL_SECONDS", "0")
    get_settings.cache_clear()

    with pytest.raises(SystemExit):
        from ai_learning.main import _validate_auth_secret
        _validate_auth_secret()

    get_settings.cache_clear()


# ── 双用户问答检索隔离测试 ──


def test_query_sources_only_contain_current_user_docs(client: TestClient, monkeypatch) -> None:
    """两个用户均有文档时，问答检索 sources 只包含当前用户文档。"""
    from ai_learning import models as _models

    monkeypatch.setattr(LLMClient, "complete", lambda self, prompt: "answer for current user")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_MIN_SCORE", "0.0")
    get_settings.cache_clear()

    # 创建 user2
    session = get_session_factory()()
    try:
        user2 = _models.User(
            username="user2",
            password_hash=hash_password("password2"),
            is_active=True,
        )
        session.add(user2)
        session.commit()
    finally:
        session.close()

    # admin 上传文档
    _login(client, "admin", "test-password")
    client.post(
        "/api/documents/upload",
        files={"file": ("admin_doc.md", "这是管理员的私有文档，讨论数据库优化和索引策略。".encode("utf-8"), "text/markdown")},
    )

    # user2 登录并上传文档
    client.post("/api/auth/logout")
    _login(client, "user2", "password2")
    client.post(
        "/api/documents/upload",
        files={"file": ("user2_doc.md", "这是用户2的私有文档，讨论前端CSS布局技巧。".encode("utf-8"), "text/markdown")},
    )

    # user2 查询
    response = client.post("/api/query", json={"question": "数据库优化", "top_k": 3})

    assert response.status_code == 200
    payload = response.json()
    # sources 不应包含 admin 的文档
    for source in payload["sources"]:
        assert source["filename"] != "admin_doc.md"
        assert source["document_id"] != 1  # admin 的文档 ID

    # admin 登录查询
    client.post("/api/auth/logout")
    _login(client, "admin", "test-password")
    response2 = client.post("/api/query", json={"question": "CSS布局", "top_k": 3})

    assert response2.status_code == 200
    payload2 = response2.json()
    for source in payload2["sources"]:
        assert source["filename"] != "user2_doc.md"
