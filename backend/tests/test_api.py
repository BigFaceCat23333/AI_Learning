from collections.abc import Generator
import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from unittest.mock import ANY

from ai_learning.auth import _captcha_answer_digest, _draw_captcha_image, _normalize_captcha_answer, hash_password
from ai_learning.core.config import get_settings
from ai_learning.core.llm_client import LLMClient
from ai_learning.db import get_engine, get_session_factory
from ai_learning.main import create_app
from ai_learning.models import CaptchaChallenge


POSTGRES_TEST_DATABASE_URL = "postgresql+psycopg://urpapa:postgres@localhost:5432/ai_learning_test"

TEST_AUTH_SECRET = "test-secret-key-at-least-32-chars-long!!"


def recreate_tables() -> None:
    """重建测试数据库表结构。"""
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("DROP TABLE IF EXISTS document_chunks CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS documents CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS captcha_challenges CASCADE"))
        connection.execute(text("DROP TABLE IF EXISTS users CASCADE"))
    from ai_learning.db import Base
    from ai_learning import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def clear_database() -> None:
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE document_chunks, documents, captcha_challenges, users RESTART IDENTITY CASCADE"))


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


_TEST_CAPTCHA_CODE = "ABCD"
_TEST_CAPTCHA_ID = "test-captcha-id-00000000000000000000"


def _create_test_captcha(code: str = _TEST_CAPTCHA_CODE, captcha_id: str = _TEST_CAPTCHA_ID) -> None:
    """在测试数据库中创建一个可预测的验证码挑战，供登录测试使用。"""
    session = get_session_factory()()
    try:
        normalized = _normalize_captcha_answer(code)
        digest = _captcha_answer_digest(captcha_id, normalized)
        challenge = CaptchaChallenge(
            id=captcha_id,
            answer_digest=digest,
            expires_at=datetime.utcnow() + timedelta(seconds=300),
            created_at=datetime.utcnow(),
        )
        session.add(challenge)
        session.commit()
    finally:
        session.close()


def _login(
    client: TestClient,
    username: str = "admin",
    password: str = "test-password",
    captcha_code: str = _TEST_CAPTCHA_CODE,
    captcha_id: str | None = None,
) -> None:
    """在 TestClient 上执行登录，设置 Cookie。自动创建可预测的验证码。"""
    cid = captcha_id if captcha_id is not None else uuid4().hex
    _create_test_captcha(captcha_code, cid)
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_id": cid,
            "captcha_code": captcha_code,
        },
    )
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
    _create_test_captcha()
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": _TEST_CAPTCHA_ID,
            "captcha_code": _TEST_CAPTCHA_CODE,
        },
    )

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
    _create_test_captcha()
    response = client.post(
        "/api/auth/login",
        json={
            "username": "  Admin  ",
            "password": "test-password",
            "captcha_id": _TEST_CAPTCHA_ID,
            "captcha_code": _TEST_CAPTCHA_CODE,
        },
    )

    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_login_wrong_password_returns_401(client: TestClient) -> None:
    _create_test_captcha()
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "wrong",
            "captcha_id": _TEST_CAPTCHA_ID,
            "captcha_code": _TEST_CAPTCHA_CODE,
        },
    )

    assert response.status_code == 401
    assert "用户名或密码错误" in response.json()["detail"]


def test_login_wrong_username_returns_401(client: TestClient) -> None:
    _create_test_captcha()
    response = client.post(
        "/api/auth/login",
        json={
            "username": "nonexistent",
            "password": "test-password",
            "captcha_id": _TEST_CAPTCHA_ID,
            "captcha_code": _TEST_CAPTCHA_CODE,
        },
    )

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

    _create_test_captcha("WXYZ", "inactive-captcha-id-0000000000000000")
    response = client.post(
        "/api/auth/login",
        json={
            "username": "inactive",
            "password": "test",
            "captcha_id": "inactive-captcha-id-0000000000000000",
            "captcha_code": "WXYZ",
        },
    )

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
    _create_test_captcha()
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": _TEST_CAPTCHA_ID,
            "captcha_code": _TEST_CAPTCHA_CODE,
        },
    )

    assert response.status_code == 200
    cookies = response.headers.get("set-cookie", "")
    assert "max-age=28800" in cookies.lower()


def test_login_cookie_secure_when_configured(client: TestClient, monkeypatch) -> None:
    """auth_cookie_secure=true 时 Cookie 应包含 Secure 属性。"""
    monkeypatch.setenv("AI_LEARNING_AUTH_COOKIE_SECURE", "true")
    get_settings.cache_clear()

    _create_test_captcha()
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": _TEST_CAPTCHA_ID,
            "captcha_code": _TEST_CAPTCHA_CODE,
        },
    )

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


# ══════════════════════════════════════════════════════════════════════
# ── 验证码接口测试 ──
# ══════════════════════════════════════════════════════════════════════


def test_captcha_returns_png(client: TestClient) -> None:
    """验证码接口返回 PNG 图片和 X-Captcha-Id。"""
    response = client.get("/api/auth/captcha")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "X-Captcha-Id" in response.headers
    assert len(response.headers["X-Captcha-Id"]) > 0
    # 禁止缓存
    assert "no-store" in response.headers.get("cache-control", "").lower()


def test_captcha_correct_code_allows_login(client: TestClient) -> None:
    """正确验证码 + 正确密码 = 登录成功。"""
    # 获取真实验证码（但我们无法知道答案，用测试辅助创建已知验证码）
    _create_test_captcha("XYZ9", "captcha-test-11111111111111111111")
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": "captcha-test-11111111111111111111",
            "captcha_code": "XYZ9",
        },
    )
    assert response.status_code == 200


def test_captcha_case_insensitive(client: TestClient) -> None:
    """验证码大小写不敏感。"""
    _create_test_captcha("abcd", "captcha-test-22222222222222222222")
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": "captcha-test-22222222222222222222",
            "captcha_code": "ABCD",  # 大写 vs 小写
        },
    )
    assert response.status_code == 200


def test_captcha_wrong_code_rejected(client: TestClient) -> None:
    """错误验证码拒绝登录。"""
    _create_test_captcha("AAAA", "captcha-test-33333333333333333333")
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": "captcha-test-33333333333333333333",
            "captcha_code": "BBBB",
        },
    )
    assert response.status_code == 400
    assert "验证码错误" in response.json()["detail"]


def test_captcha_wrong_answer_then_correct_rejected(client: TestClient) -> None:
    """错误答案后，同挑战改填正确答案仍被拒绝（因为已被消费）。"""
    _create_test_captcha("GGGG", "captcha-test-66666666666666666666")

    # 第一次：错误答案
    r1 = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": "captcha-test-66666666666666666666",
            "captcha_code": "HHHH",  # 错误的答案
        },
    )
    assert r1.status_code == 400

    # 第二次：同挑战，正确答案
    r2 = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": "captcha-test-66666666666666666666",
            "captcha_code": "GGGG",  # 正确答案，但已被消费
        },
    )
    assert r2.status_code == 400
    assert "验证码错误" in r2.json()["detail"]


def test_captcha_expired_rejected(client: TestClient) -> None:
    """过期验证码被拒绝。"""
    session = get_session_factory()()
    try:
        from datetime import timedelta

        normalized = _normalize_captcha_answer("EXP1")
        digest = _captcha_answer_digest("captcha-expired-777777777777777777", normalized)
        challenge = CaptchaChallenge(
            id="captcha-expired-777777777777777777",
            answer_digest=digest,
            expires_at=datetime.utcnow() - timedelta(seconds=60),  # 已过期
            created_at=datetime.utcnow() - timedelta(seconds=180),
        )
        session.add(challenge)
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": "captcha-expired-777777777777777777",
            "captcha_code": "EXP1",
        },
    )
    assert response.status_code == 400
    assert "验证码错误" in response.json()["detail"]


def test_captcha_missing_id_rejected(client: TestClient) -> None:
    """缺少 captcha_id 时 FastAPI 校验拒绝（422）。"""
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    # 缺少必填字段，FastAPI 返回 422
    assert response.status_code == 422


def test_captcha_already_consumed_rejected(client: TestClient) -> None:
    """同一验证码不能重复使用。"""
    _create_test_captcha("CCCC", "captcha-test-44444444444444444444")
    login_json = {
        "username": "admin",
        "password": "test-password",
        "captcha_id": "captcha-test-44444444444444444444",
        "captcha_code": "CCCC",
    }
    # 第一次成功
    r1 = client.post("/api/auth/login", json=login_json)
    assert r1.status_code == 200

    # 退出后再次用同一验证码
    client.post("/api/auth/logout")
    r2 = client.post("/api/auth/login", json=login_json)
    assert r2.status_code == 400
    assert "验证码错误" in r2.json()["detail"]


def test_captcha_consumed_even_on_wrong_password(client: TestClient) -> None:
    """即使密码错误也消费验证码。"""
    _create_test_captcha("DDDD", "captcha-test-55555555555555555555")
    login_json = {
        "username": "admin",
        "password": "wrong-password",
        "captcha_id": "captcha-test-55555555555555555555",
        "captcha_code": "DDDD",
    }
    r1 = client.post("/api/auth/login", json=login_json)
    assert r1.status_code == 401  # 密码错误

    # 再次用同一验证码应失败（已被消费）
    login_json2 = {
        **login_json,
        "password": "test-password",  # 这次密码对了
    }
    r2 = client.post("/api/auth/login", json=login_json2)
    assert r2.status_code == 400  # 验证码已被消费


# ══════════════════════════════════════════════════════════════════════
# ── 验证码图片布局边界测试 ──
# ══════════════════════════════════════════════════════════════════════


def test_captcha_chars_stay_within_canvas() -> None:
    """字符绘制后所有 bbox 均不超出画布边界。"""
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    # 用固定 46px 字体生成多张验证码，逐字符检测 bbox
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 46)
        except (OSError, IOError):
            font = ImageFont.load_default(size=46)

    width, height = 200, 70
    # 多轮生成确保覆盖不同随机值
    for _ in range(20):
        png = _draw_captcha_image("ABCD", width, height)
        img = Image.open(BytesIO(png))
        draw = ImageDraw.Draw(img)

        # 检查每个字符槽位的字形边界
        char_width = width // 4
        for i, ch in enumerate("ABCD"):
            bbox = draw.textbbox((0, 0), ch, font=font)
            # 估算字形可能位置：用 bbox 相对偏移反推
            # 检查图片中对应槽位是否有非白色像素
            slot_left = char_width * i
            slot_right = slot_left + char_width

            # 确认字符槽位内有绘制内容
            found = False
            for y in range(height):
                for x in range(slot_left, slot_right):
                    pixel = img.getpixel((x, y))
                    if pixel != (255, 255, 255):
                        found = True
                        break
                if found:
                    break
            assert found, f"字符 {ch}（槽位 {i}）没有任何绘制像素"


def test_captcha_default_font_46px_sufficient() -> None:
    """默认回退字体在 46px size 下字形尺寸明显大于 10x8（排除 Docker 小字问题）。"""
    from PIL import ImageFont as PF

    font = PF.load_default(size=46)
    # 用虚拟 ImageDraw 的 textbbox 测量
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (200, 70))
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), "W", font=font)
    char_w = bbox[2] - bbox[0]
    char_h = bbox[3] - bbox[1]

    # 46px 字体下 W 字形至少应有 20px 宽、20px 高
    assert char_w >= 20, f"默认 46px 字体 W 宽度 {char_w} < 20"
    assert char_h >= 20, f"默认 46px 字体 W 高度 {char_h} < 20"


# ══════════════════════════════════════════════════════════════════════
# ── 当前用户资料测试 ──
# ══════════════════════════════════════════════════════════════════════


def test_me_returns_profile_fields(client: TestClient) -> None:
    """GET /auth/me 返回新增资料字段。"""
    _login(client)
    response = client.get("/api/auth/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "admin"
    assert "display_name" in payload
    assert "email" in payload
    assert "phone" in payload
    assert "bio" in payload
    assert "avatar_url" in payload
    # 新用户这些字段应为 null
    assert payload["display_name"] is None
    assert payload["avatar_url"] is None


def test_update_profile_saves_fields(client: TestClient) -> None:
    """PUT /auth/me/profile 保存个人资料。"""
    _login(client)

    response = client.put(
        "/api/auth/me/profile",
        json={
            "display_name": "测试用户",
            "email": "Test@Example.COM",
            "phone": "+86-13800138000",
            "bio": "这是一段个人简介。",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["display_name"] == "测试用户"
    assert payload["email"] == "test@example.com"  # 转小写
    assert payload["phone"] == "+86-13800138000"
    assert payload["bio"] == "这是一段个人简介。"

    # 确认持久化
    me = client.get("/api/auth/me")
    assert me.json()["display_name"] == "测试用户"


def test_update_profile_empty_fields_normalized(client: TestClient) -> None:
    """空字符串和空白字符串规范化为 null。"""
    _login(client)

    # 先写入
    client.put(
        "/api/auth/me/profile",
        json={"display_name": "name", "email": "e@e.com"},
    )
    # 再清空
    response = client.put(
        "/api/auth/me/profile",
        json={"display_name": "   ", "email": "", "phone": None, "bio": None},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["display_name"] is None
    assert payload["email"] is None


def test_update_profile_rejects_invalid_email(client: TestClient) -> None:
    """无效邮箱格式被拒绝。"""
    _login(client)
    response = client.put(
        "/api/auth/me/profile",
        json={"email": "not-an-email"},
    )
    assert response.status_code == 400
    assert "邮箱" in response.json()["detail"]


def test_update_profile_rejects_invalid_phone(client: TestClient) -> None:
    """无效手机号字符被拒绝。"""
    _login(client)
    response = client.put(
        "/api/auth/me/profile",
        json={"phone": "包含中文"},
    )
    assert response.status_code == 400
    assert "手机号" in response.json()["detail"]


def test_update_profile_rejects_username_modification(client: TestClient) -> None:
    """尝试修改 username 被拒绝（extra=forbid）。"""
    _login(client)
    response = client.put(
        "/api/auth/me/profile",
        json={"username": "hacker"},
    )
    # Pydantic extra=forbid 返回 422
    assert response.status_code == 422


def test_update_profile_requires_auth(client: TestClient) -> None:
    """未登录不能修改资料。"""
    response = client.put("/api/auth/me/profile", json={"display_name": "x"})
    assert response.status_code == 401


def test_bio_max_length(client: TestClient) -> None:
    """个人简介可以在接近上限时保存。"""
    _login(client)
    long_bio = "测" * 500
    response = client.put(
        "/api/auth/me/profile",
        json={"bio": long_bio},
    )
    assert response.status_code == 200
    assert len(response.json()["bio"]) == 500


# ══════════════════════════════════════════════════════════════════════
# ── 头像测试 ──
# ══════════════════════════════════════════════════════════════════════


def _make_test_image(width: int = 100, height: int = 100, fmt: str = "PNG") -> bytes:
    """生成一个简单的测试图片。"""
    from PIL import Image as PILImage
    from io import BytesIO

    img = PILImage.new("RGB", (width, height), color=(100, 150, 200))
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_upload_avatar_success(client: TestClient) -> None:
    """上传合法头像成功。"""
    _login(client)
    img_bytes = _make_test_image(200, 200, "PNG")

    response = client.put(
        "/api/auth/me/avatar",
        files={"file": ("avatar.png", img_bytes, "image/png")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["avatar_url"] is not None

    # 确认 GET /auth/me 返回 avatar_url
    me = client.get("/api/auth/me")
    assert me.json()["avatar_url"] is not None


def test_upload_avatar_requires_auth(client: TestClient) -> None:
    """未登录不能上传头像。"""
    response = client.put(
        "/api/auth/me/avatar",
        files={"file": ("avatar.png", b"fake", "image/png")},
    )
    assert response.status_code == 401


def test_delete_avatar_idempotent(client: TestClient) -> None:
    """删除头像幂等（即使没有头像也成功）。"""
    _login(client)

    # 无头像时删除也成功
    response = client.delete("/api/auth/me/avatar")
    assert response.status_code == 200
    assert response.json()["avatar_url"] is None

    # 上传后再删除
    client.put(
        "/api/auth/me/avatar",
        files={"file": ("avatar.png", _make_test_image(), "image/png")},
    )
    response2 = client.delete("/api/auth/me/avatar")
    assert response2.status_code == 200
    assert response2.json()["avatar_url"] is None


def test_avatar_rejects_oversized_file(client: TestClient, monkeypatch) -> None:
    """超大文件被拒绝。"""
    monkeypatch.setenv("AI_LEARNING_AVATAR_MAX_BYTES", "100")
    get_settings.cache_clear()

    _login(client)
    big_img = _make_test_image(500, 500, "PNG")
    response = client.put(
        "/api/auth/me/avatar",
        files={"file": ("big.png", big_img, "image/png")},
    )
    assert response.status_code == 400
    get_settings.cache_clear()


def test_avatar_rejects_fake_image(client: TestClient) -> None:
    """伪造的图片格式被拒绝。"""
    _login(client)
    response = client.put(
        "/api/auth/me/avatar",
        files={"file": ("fake.png", b"not an image at all", "image/png")},
    )
    assert response.status_code == 400


def test_avatar_rejects_text_file(client: TestClient) -> None:
    """文本文件伪装图片被拒绝。"""
    _login(client)
    response = client.put(
        "/api/auth/me/avatar",
        files={"file": ("doc.txt", b"hello world text", "image/png")},
    )
    assert response.status_code == 400


def test_get_avatar_requires_auth(client: TestClient) -> None:
    """未登录不能读取头像。"""
    response = client.get("/api/auth/me/avatar")
    assert response.status_code == 401


def test_get_avatar_no_avatar_returns_404(client: TestClient) -> None:
    """无头像时返回 404。"""
    _login(client)
    response = client.get("/api/auth/me/avatar")
    assert response.status_code == 404


def test_get_avatar_returns_image(client: TestClient) -> None:
    """上传后可读取头像。"""
    _login(client)
    client.put(
        "/api/auth/me/avatar",
        files={"file": ("avatar.png", _make_test_image(), "image/png")},
    )

    response = client.get("/api/auth/me/avatar")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"


# ══════════════════════════════════════════════════════════════════════
# ── 修改密码测试 ──
# ══════════════════════════════════════════════════════════════════════


def test_change_password_success(client: TestClient) -> None:
    """正确当前密码 + 新密码 = 修改成功并清除 Cookie。"""
    _login(client)

    response = client.put(
        "/api/auth/me/password",
        json={
            "current_password": "test-password",
            "new_password": "new-strong-password-123",
        },
    )
    assert response.status_code == 204

    # Cookie 应被清除
    cookies = response.headers.get("set-cookie", "")
    assert "ai_learning_session" in cookies.lower()

    # 旧密码不能登录
    _create_test_captcha("EEEE", "pw-test-11111111111111111111")
    r_old = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": "pw-test-11111111111111111111",
            "captcha_code": "EEEE",
        },
    )
    assert r_old.status_code == 401

    # 新密码可以登录
    _create_test_captcha("FFFF", "pw-test-22222222222222222222")
    r_new = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "new-strong-password-123",
            "captcha_id": "pw-test-22222222222222222222",
            "captcha_code": "FFFF",
        },
    )
    assert r_new.status_code == 200


def test_change_password_wrong_current(client: TestClient) -> None:
    """当前密码错误被拒绝。"""
    _login(client)

    response = client.put(
        "/api/auth/me/password",
        json={
            "current_password": "wrong-current",
            "new_password": "new-password-12345678",
        },
    )
    assert response.status_code == 400
    assert "当前密码错误" in response.json()["detail"]


def test_change_password_same_as_current(client: TestClient) -> None:
    """新旧密码相同被拒绝。"""
    _login(client)

    response = client.put(
        "/api/auth/me/password",
        json={
            "current_password": "test-password",
            "new_password": "test-password",
        },
    )
    assert response.status_code == 400
    assert "相同" in response.json()["detail"]


def test_change_password_too_short(client: TestClient) -> None:
    """新密码太短被拒绝（Pydantic 校验，422）。"""
    _login(client)

    response = client.put(
        "/api/auth/me/password",
        json={
            "current_password": "test-password",
            "new_password": "short",
        },
    )
    assert response.status_code == 422


def test_change_password_requires_auth(client: TestClient) -> None:
    """未登录不能修改密码。"""
    response = client.put(
        "/api/auth/me/password",
        json={
            "current_password": "x",
            "new_password": "y12345678",
        },
    )
    assert response.status_code == 401


# ══════════════════════════════════════════════════════════════════════
# ── 登录响应字段扩展测试 ──
# ══════════════════════════════════════════════════════════════════════


def test_login_response_includes_profile_fields(client: TestClient) -> None:
    """登录成功响应包含新增资料字段。"""
    _create_test_captcha()
    response = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password",
            "captcha_id": _TEST_CAPTCHA_ID,
            "captcha_code": _TEST_CAPTCHA_CODE,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "display_name" in payload
    assert "email" in payload
    assert "phone" in payload
    assert "bio" in payload
    assert "avatar_url" in payload


# ══════════════════════════════════════════════════════════════════════
# ── 头像版本与内容测试 ──
# ══════════════════════════════════════════════════════════════════════


def test_avatar_consecutive_uploads_change_version(client: TestClient) -> None:
    """连续上传两次头像后 avatar_url 版本参数应不同。"""
    import time

    _login(client)

    img1 = _make_test_image(200, 200, "PNG")
    r1 = client.put(
        "/api/auth/me/avatar",
        files={"file": ("avatar1.png", img1, "image/png")},
    )
    assert r1.status_code == 200
    url1 = r1.json()["avatar_url"]
    assert url1 is not None

    # 等待确保 microsecond 级时间不同
    time.sleep(0.001)

    img2 = _make_test_image(300, 300, "PNG")
    r2 = client.put(
        "/api/auth/me/avatar",
        files={"file": ("avatar2.png", img2, "image/png")},
    )
    assert r2.status_code == 200
    url2 = r2.json()["avatar_url"]
    assert url2 is not None

    # 版本参数应不同
    assert url1 != url2, f"Consecutive avatar uploads produced same version URL: {url1}"


def test_avatar_rejects_large_pixels(client: TestClient) -> None:
    """高压缩比超大像素 JPEG 被拒绝（解压炸弹防护）。"""
    _login(client)

    from PIL import Image as PILImage
    from io import BytesIO

    big_img = PILImage.new("RGB", (5000, 5000), color=(255, 0, 0))
    buf = BytesIO()
    big_img.save(buf, format="JPEG", quality=10)
    compressed = buf.getvalue()
    assert len(compressed) < 1_000_000

    response = client.put(
        "/api/auth/me/avatar",
        files={"file": ("big.jpg", compressed, "image/jpeg")},
    )
    assert response.status_code == 400
    assert "像素" in response.json()["detail"]


def test_avatar_rejects_truncated_image(client: TestClient) -> None:
    """截断/损坏的图片被拒绝。"""
    _login(client)

    valid = _make_test_image(100, 100, "PNG")
    truncated = valid[: len(valid) // 2]

    response = client.put(
        "/api/auth/me/avatar",
        files={"file": ("broken.png", truncated, "image/png")},
    )
    assert response.status_code == 400


# ══════════════════════════════════════════════════════════════════════
# ── 验证码并发消费测试（PostgreSQL 集成） ──
# ══════════════════════════════════════════════════════════════════════


def test_captcha_concurrent_single_consumption(client: TestClient) -> None:
    """两个并发请求使用同一验证码，最多一个成功消费。"""
    import concurrent.futures

    _create_test_captcha("CONC", "captcha-conc-9999999999999999999999")

    def try_login(idx: int) -> int:
        r = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "test-password",
                "captcha_id": "captcha-conc-9999999999999999999999",
                "captcha_code": "CONC",
            },
        )
        return r.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(try_login, i) for i in range(2)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # 并发测试后清理客户端 cookie，避免影响后续测试
    client.post("/api/auth/logout")

    success_count = sum(1 for r in results if r == 200)
    assert success_count <= 1, f"Expected at most 1 success, got: {results}"
    assert 400 in results, f"Expected one 400, got: {results}"
