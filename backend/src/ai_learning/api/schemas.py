from datetime import datetime

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class QuerySource(BaseModel):
    document_id: int
    filename: str
    file_type: str
    chunk_id: int
    chunk_index: int
    chunk_text: str
    score: float
    chunk_metadata: dict


class QueryResponse(BaseModel):
    question: str
    answer: str
    sources: list[QuerySource]


class DocumentUploadResponse(BaseModel):
    document_id: int
    filename: str
    file_type: str
    chunk_count: int


class AgentRequest(BaseModel):
    task: str = Field(..., min_length=1)


class AgentResponse(BaseModel):
    task: str
    result: str


class DocumentListItem(BaseModel):
    """历史文档列表项响应模型。"""
    document_id: int
    filename: str
    file_type: str
    chunk_count: int
    created_at: datetime


# ── 认证相关 ──


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class LoginResponse(BaseModel):
    user_id: int
    username: str


class UserMeResponse(BaseModel):
    user_id: int
    username: str
