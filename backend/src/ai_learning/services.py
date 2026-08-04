import logging
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import UploadFile
from sqlalchemy.orm import Session

from ai_learning.core.config import get_settings
from ai_learning.models import Document, DocumentChunk
from ai_learning.rag.embedder import EmbeddingCancelledError, EmbeddingServiceError, get_embedding_client
from ai_learning.rag.loader import parse_text_file, split_chunks, validate_document_filename

logger = logging.getLogger(__name__)


def save_uploaded_document(
    file: UploadFile,
    db: Session,
    user_id: int,
    should_cancel: Callable[[], bool] | None = None,
) -> Document:
    """上传并入库文档：解析、分块、生成 embedding、保存。"""
    settings = get_settings()
    filename = Path(file.filename or "").name
    file_type = validate_document_filename(filename)

    content = file.file.read()
    if len(content) > settings.upload_max_bytes:
        raise ValueError(f"Document size cannot exceed {settings.upload_max_bytes} bytes.")

    raw_text = parse_text_file(content)
    if should_cancel is not None and should_cancel():
        raise EmbeddingCancelledError("文档上传已取消。")

    # 生成带元数据的分块
    parsed_chunks = split_chunks(
        raw_text,
        filename=filename,
        file_type=file_type,
    )
    if not parsed_chunks:
        raise ValueError("Document did not produce any chunks.")

    # 批量生成 chunk embeddings（在 commit 之前完成，避免写入无向量的 chunk）
    try:
        embedder = get_embedding_client(settings)
        chunk_texts = [chunk.text for chunk in parsed_chunks]
        embeddings = embedder.embed_texts(chunk_texts, should_cancel=should_cancel)
    except ValueError as exc:
        # 配置缺失等明确错误
        raise ValueError(f"Embedding error: {exc}") from exc
    except httpx.HTTPError as exc:
        # 网络/API 错误，透传为 EmbeddingServiceError，让路由层返回 503
        raise EmbeddingServiceError(f"Embedding service unavailable: {exc}") from exc

    if should_cancel is not None and should_cancel():
        raise EmbeddingCancelledError("文档上传已取消。")

    # 保存上传文件到磁盘
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / f"{uuid4().hex}-{filename}"
    saved_path.write_bytes(content)

    # 创建 Document 和 DocumentChunk
    document = Document(
        user_id=user_id,
        filename=filename,
        file_type=file_type,
        saved_path=str(saved_path),
        raw_text=raw_text,
    )
    document.chunks = [
        DocumentChunk(
            chunk_index=parsed_chunk.chunk_metadata["chunk_index"],
            chunk_text=parsed_chunk.text,
            embedding=embedding,
            chunk_metadata=parsed_chunk.chunk_metadata,
            content_hash=parsed_chunk.content_hash,
            char_count=parsed_chunk.char_count,
        )
        for parsed_chunk, embedding in zip(parsed_chunks, embeddings)
    ]
    db.add(document)
    db.commit()
    db.refresh(document)

    if settings.rag_debug_logs:
        logger.info(
            "文档上传完成 | user_id=%d filename=%s file_type=%s content_bytes=%d "
            "raw_text_chars=%d chunk_count=%d document_id=%d",
            user_id,
            filename,
            file_type,
            len(content),
            len(raw_text),
            len(document.chunks),
            document.id,
        )

    return document
