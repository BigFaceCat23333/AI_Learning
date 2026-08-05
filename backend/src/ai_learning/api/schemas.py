from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    conversation_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_blank_question(self) -> "QueryRequest":
        stripped = self.question.strip()
        if not stripped:
            raise ValueError("问题不能为空或仅包含空白字符。")
        self.question = stripped
        return self


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
    conversation_id: int | None = None
    conversation_title: str | None = None


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
    captcha_id: str = Field(..., min_length=1)
    captcha_code: str = Field(..., min_length=1, max_length=10)


class LoginResponse(BaseModel):
    user_id: int
    username: str
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    bio: str | None = None
    avatar_url: str | None = None


class UserMeResponse(BaseModel):
    user_id: int
    username: str
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    bio: str | None = None
    avatar_url: str | None = None


class ProfileUpdateRequest(BaseModel):
    """个人资料更新请求。不接受 username 修改。"""
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    bio: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def strip_and_normalize(self) -> "ProfileUpdateRequest":
        """去除首尾空格，空字符串转为 None，邮箱转小写。"""
        for field_name in ("display_name", "email", "phone", "bio"):
            val = getattr(self, field_name)
            if isinstance(val, str):
                stripped = val.strip()
                setattr(self, field_name, stripped if stripped else None)
        if self.email is not None:
            self.email = self.email.lower()
        return self


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


# ── 会话相关 ──


class ConversationSummary(BaseModel):
    """会话列表摘要。"""
    id: int
    title: str
    created_at: datetime
    last_message_at: datetime


class ConversationListResponse(BaseModel):
    """分页会话列表响应。"""
    items: list[ConversationSummary]
    total: int


class ConversationMessageOut(BaseModel):
    """会话消息输出（不含 sources 原始 JSON，前端按需解析）。"""
    id: int
    role: str
    content: str
    sources: list[QuerySource] | None = None
    created_at: datetime


class ConversationDetail(BaseModel):
    """会话详情：元信息 + 全部消息。"""
    id: int
    title: str
    created_at: datetime
    last_message_at: datetime
    messages: list[ConversationMessageOut]


class ConversationRenameRequest(BaseModel):
    """会话改名请求。"""
    title: str = Field(..., min_length=1, max_length=100)

    @model_validator(mode="after")
    def strip_title(self) -> "ConversationRenameRequest":
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("会话标题不能为空。")
        return self
