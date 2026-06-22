import httpx
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ai_learning.agent.graph import run_agent
from ai_learning.api.schemas import (
    AgentRequest,
    AgentResponse,
    DocumentUploadResponse,
    QueryRequest,
    QueryResponse,
    QuerySource,
)
from ai_learning.core.config import get_settings
from ai_learning.core.llm_client import LLMClient
from ai_learning.db import get_db
from ai_learning.models import DocumentChunk
from ai_learning.rag.embedder import EmbeddingServiceError, get_embedding_client
from ai_learning.rag.retriever import retrieve
from ai_learning.services import save_uploaded_document

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/documents/upload", response_model=DocumentUploadResponse)
def upload_document(file: UploadFile, db: Session = Depends(get_db)) -> DocumentUploadResponse:
    try:
        document = save_uploaded_document(file, db)
    except ValueError as exc:
        # 文件类型、大小、空文档等用户输入问题 -> 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmbeddingServiceError as exc:
        # embedding 上游服务不可用 -> 503
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return DocumentUploadResponse(
        document_id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        chunk_count=len(document.chunks),
    )


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, db: Session = Depends(get_db)) -> QueryResponse:
    settings = get_settings()
    if settings.rag_debug_logs:
        logger.info(
            "查询请求 | question_length=%d top_k=%d",
            len(request.question),
            request.top_k,
        )

    # 检查是否有文档
    chunk_count = db.query(DocumentChunk).count()
    if chunk_count == 0:
        if settings.rag_debug_logs:
            logger.info("查询拒绝 | 原因=无文档")
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
        )
    except EmbeddingServiceError as exc:
        # embedding 上游服务不可用 -> 503
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        # embedding 返回格式/维度错误 -> 503
        raise HTTPException(status_code=503, detail=f"Embedding error: {exc}") from exc
    except httpx.HTTPError as exc:
        # embedding 上游服务网络错误 -> 502
        raise HTTPException(status_code=502, detail=f"Embedding provider error: {exc}") from exc

    # 低相关度拒答
    if not results:
        if settings.rag_debug_logs:
            logger.info("查询拒答 | 原因=低相关度 阈值=%.2f", settings.retrieval_min_score)
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
            logger.warning("查询失败 | 原因=LLM配置缺失")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        if settings.rag_debug_logs:
            logger.warning("查询失败 | 原因=LLM上游错误")
        raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc

    if settings.rag_debug_logs:
        logger.info(
            "查询完成 | answer_length=%d source_count=%d",
            len(answer),
            len(sources),
        )
    return QueryResponse(question=request.question, answer=answer, sources=sources)


@router.post("/agent", response_model=AgentResponse)
def agent(request: AgentRequest) -> AgentResponse:
    result = run_agent(request.task)
    return AgentResponse(task=request.task, result=result)
