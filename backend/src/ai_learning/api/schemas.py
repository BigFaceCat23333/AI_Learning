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
