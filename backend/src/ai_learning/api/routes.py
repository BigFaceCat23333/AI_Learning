import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from ai_learning.agent.graph import run_agent
from ai_learning.api.schemas import (
    AgentRequest,
    AgentResponse,
    DocumentUploadResponse,
    QueryRequest,
    QueryResponse,
    QuerySource,
)
from ai_learning.core.llm_client import LLMClient
from ai_learning.db import get_db
from ai_learning.models import DocumentChunk
from ai_learning.rag.retriever import score_documents
from ai_learning.services import save_uploaded_document

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/documents/upload", response_model=DocumentUploadResponse)
def upload_document(file: UploadFile, db: Session = Depends(get_db)) -> DocumentUploadResponse:
    try:
        document = save_uploaded_document(file, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DocumentUploadResponse(
        document_id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        chunk_count=len(document.chunks),
    )


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, db: Session = Depends(get_db)) -> QueryResponse:
    chunks = (
        db.query(DocumentChunk)
        .options(joinedload(DocumentChunk.document))
        .order_by(DocumentChunk.id)
        .all()
    )
    if not chunks:
        raise HTTPException(status_code=404, detail="No documents have been uploaded.")

    chunk_texts = [chunk.chunk_text for chunk in chunks]
    scored_texts = score_documents(request.question, chunk_texts, top_k=request.top_k)
    if not scored_texts:
        raise HTTPException(status_code=404, detail="No document chunks are available.")
    matched_texts = [(text, score) for text, score in scored_texts if score > 0]
    if not matched_texts:
        matched_texts = scored_texts

    selected: list[tuple[DocumentChunk, float]] = []
    used_indexes: set[int] = set()
    for text, score in matched_texts:
        for index, chunk in enumerate(chunks):
            if index not in used_indexes and chunk.chunk_text == text:
                selected.append((chunk, score))
                used_indexes.add(index)
                break

    context = "\n\n".join(chunk.chunk_text for chunk, _score in selected)
    prompt = (
        "Answer the question using only the retrieved context.\n\n"
        f"Question:\n{request.question}\n\n"
        f"Context:\n{context}"
    )
    try:
        answer = LLMClient().complete(prompt)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc
    sources = [
        QuerySource(
            document_id=chunk.document_id,
            filename=chunk.document.filename,
            chunk_id=chunk.id,
            chunk_text=chunk.chunk_text,
            score=score,
        )
        for chunk, score in selected
    ]
    return QueryResponse(question=request.question, answer=answer, sources=sources)


@router.post("/agent", response_model=AgentResponse)
def agent(request: AgentRequest) -> AgentResponse:
    result = run_agent(request.task)
    return AgentResponse(task=request.task, result=result)
