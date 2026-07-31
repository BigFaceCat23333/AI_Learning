from datetime import datetime

from pydantic import BaseModel, Field, model_validator


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
