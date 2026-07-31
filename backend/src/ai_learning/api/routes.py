import httpx
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, selectinload

from ai_learning.agent.graph import run_agent
from ai_learning.api.schemas import (
    AgentRequest,
    AgentResponse,
    DocumentListItem,
    DocumentUploadResponse,
    LoginRequest,
    LoginResponse,
    QueryRequest,
    QueryResponse,
    QuerySource,
    UserMeResponse,
)
from ai_learning.auth import (
    clear_auth_cookie,
    create_token,
    get_current_user,
    set_auth_cookie,
    verify_login_password,
)
from ai_learning.core.config import get_settings
from ai_learning.core.llm_client import LLMClient
from ai_learning.db import get_db
from ai_learning.models import Document, DocumentChunk, User
from ai_learning.rag.embedder import EmbeddingServiceError, get_embedding_client
from ai_learning.rag.retriever import retrieve
from ai_learning.services import save_uploaded_document

logger = logging.getLogger(__name__)

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])


# ── 公开接口 ──


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ── 认证接口 ──


@auth_router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, response: Response, db: Session = Depends(get_db)) -> LoginResponse:
    """用户名密码登录，成功后设置 HttpOnly Cookie。

    始终执行 Argon2 密码校验以消除用户名时序枚举：
    - 存在且活跃用户使用真实 hash 校验
    - 不存在或停用用户使用固定 dummy hash 校验
    - 最终所有失败路径统一返回相同 401 文案。
    """
    username = request.username.strip().lower()

    user = db.query(User).filter(User.username == username).first()
    is_active = user.is_active if user is not None else False

    exists_and_active, password_ok = verify_login_password(request.password, user, is_active)

    if not exists_and_active or not password_ok:
        raise HTTPException(status_code=401, detail="用户名或密码错误。")

    # user 一定非 None（exists_and_active 为 True 保证了这一点）
    token = create_token(user.id)  # type: ignore[union-attr]
    set_auth_cookie(response, token)

    return LoginResponse(user_id=user.id, username=user.username)  # type: ignore[union-attr]


@auth_router.post("/logout", status_code=204)
def logout(
    response: Response,
    user: User = Depends(get_current_user),
) -> None:
    """退出登录，清除认证 Cookie。"""
    clear_auth_cookie(response)


@auth_router.get("/me", response_model=UserMeResponse)
def me(user: User = Depends(get_current_user)) -> UserMeResponse:
    """返回当前已登录用户信息。"""
    return UserMeResponse(user_id=user.id, username=user.username)


# ── 业务接口（均需认证）──


@router.post("/documents/upload", response_model=DocumentUploadResponse)
def upload_document(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentUploadResponse:
    try:
        document = save_uploaded_document(file, db, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmbeddingServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return DocumentUploadResponse(
        document_id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        chunk_count=len(document.chunks),
    )


@router.post("/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QueryResponse:
    settings = get_settings()
    if settings.rag_debug_logs:
        logger.info(
            "查询请求 | user_id=%d question_length=%d top_k=%d",
            user.id,
            len(request.question),
            request.top_k,
        )

    # 检查当前用户是否有文档
    chunk_count = (
        db.query(DocumentChunk)
        .join(Document, DocumentChunk.document_id == Document.id)
        .filter(Document.user_id == user.id)
        .count()
    )
    if chunk_count == 0:
        if settings.rag_debug_logs:
            logger.info("查询拒绝 | user_id=%d 原因=无文档", user.id)
        raise HTTPException(status_code=404, detail="No documents have been uploaded.")

    # 使用向量检索召回
    try:
        embedder = get_embedding_client(settings)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        results = retrieve(
            query=request.question,
            db=db,
            embedder=embedder,
            top_k=request.top_k,
            min_score=settings.retrieval_min_score,
            user_id=user.id,
        )
    except EmbeddingServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"Embedding error: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Embedding provider error: {exc}") from exc

    # 低相关度拒答
    if not results:
        if settings.rag_debug_logs:
            logger.info("查询拒答 | user_id=%d 原因=低相关度 阈值=%.2f", user.id, settings.retrieval_min_score)
        return QueryResponse(
            question=request.question,
            answer="文档中没有找到足够依据。",
            sources=[],
        )

    # 构建带引用编号的 Context
    context_parts: list[str] = []
    sources: list[QuerySource] = []
    for idx, result in enumerate(results, start=1):
        chunk = result.chunk
        context_parts.append(f"[{idx}] {chunk.chunk_text}")
        sources.append(
            QuerySource(
                document_id=chunk.document_id,
                filename=chunk.document.filename,
                file_type=chunk.document.file_type,
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_metadata.get("chunk_index", chunk.chunk_index),
                chunk_text=chunk.chunk_text,
                score=round(result.score, 4),
                chunk_metadata=chunk.chunk_metadata,
            )
        )

    context = "\n\n".join(context_parts)
    prompt = (
        "请仅根据以下检索到的上下文回答用户问题。如果上下文中没有足够依据，请明确说"
        "\"根据已有文档，无法回答该问题\"，不要编造任何信息。\n\n"
        f"用户问题：\n{request.question}\n\n"
        f"检索上下文（带引用编号）：\n{context}"
    )

    try:
        answer = LLMClient().complete(prompt)
    except ValueError as exc:
        if settings.rag_debug_logs:
            logger.warning("查询失败 | user_id=%d 原因=LLM配置缺失", user.id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        if settings.rag_debug_logs:
            logger.warning("查询失败 | user_id=%d 原因=LLM上游错误", user.id)
        raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc

    if settings.rag_debug_logs:
        logger.info(
            "查询完成 | user_id=%d answer_length=%d source_count=%d",
            user.id,
            len(answer),
            len(sources),
        )
    return QueryResponse(question=request.question, answer=answer, sources=sources)


@router.post("/agent", response_model=AgentResponse)
def agent(
    request: AgentRequest,
    user: User = Depends(get_current_user),
) -> AgentResponse:
    result = run_agent(request.task)
    return AgentResponse(task=request.task, result=result)


@router.get("/documents", response_model=list[DocumentListItem])
def list_documents(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DocumentListItem]:
    """获取当前用户的历史文档列表，按创建时间倒序排列。"""
    documents = (
        db.query(Document)
        .options(selectinload(Document.chunks))
        .filter(Document.user_id == user.id)
        .order_by(Document.created_at.desc(), Document.id.desc())
        .all()
    )

    return [
        DocumentListItem(
            document_id=doc.id,
            filename=doc.filename,
            file_type=doc.file_type,
            chunk_count=len(doc.chunks),
            created_at=doc.created_at,
        )
        for doc in documents
    ]


# 文件类型到 MIME 的映射
_MIME_TYPES: dict[str, str] = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
}


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """下载原文件，校验路径安全和用户归属后返回原始文件内容。"""
    settings = get_settings()

    # 同时按文档 ID 和当前 user_id 查询，避免资源存在性泄露
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.user_id == user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    # 规范化路径并校验是否位于上传目录内，防止路径遍历攻击
    upload_dir = Path(settings.upload_dir).resolve()
    saved_path = Path(document.saved_path).resolve()
    try:
        saved_path.relative_to(upload_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail="Document file not found.")

    # 检查磁盘文件是否存在
    if not saved_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found.")

    media_type = _MIME_TYPES.get(document.file_type, "application/octet-stream")

    return FileResponse(
        path=str(saved_path),
        filename=document.filename,
        media_type=media_type,
    )
