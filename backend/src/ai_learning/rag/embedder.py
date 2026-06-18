"""Embedding provider 模块。

提供 EmbeddingClient 抽象接口及两种实现：
- OpenAICompatibleEmbeddingClient：调用 OpenAI 兼容 API
- MockEmbeddingClient：测试用，返回稳定可重复的向量
"""

import hashlib
from abc import ABC, abstractmethod

import httpx

from ai_learning.core.config import Settings, get_settings


class EmbeddingServiceError(Exception):
    """Embedding 服务异常（网络、超时、限流等上游问题）。

    与 ValueError（配置缺失、返回格式错误）区分，方便路由层映射到不同的 HTTP 状态码。
    """


class EmbeddingClient(ABC):
    """Embedding 客户端抽象接口。"""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """对多个文本生成 embedding。"""

    @abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """对单个查询文本生成 embedding。"""


class OpenAICompatibleEmbeddingClient(EmbeddingClient):
    """调用 OpenAI 兼容 embedding API。"""

    def __init__(self, settings: Settings) -> None:
        if not settings.embedding_base_url:
            raise ValueError(
                "AI_LEARNING_EMBEDDING_BASE_URL is required for openai_compatible provider."
            )
        if not settings.embedding_api_key:
            raise ValueError(
                "AI_LEARNING_EMBEDDING_API_KEY is required for openai_compatible provider."
            )
        self._settings = settings
        self._url = f"{settings.embedding_base_url.rstrip('/')}/embeddings"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = httpx.post(
            self._url,
            headers={"Authorization": f"Bearer {self._settings.embedding_api_key}"},
            json={
                "model": self._settings.embedding_model,
                "input": texts,
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload["data"]
        if len(data) != len(texts):
            raise ValueError(
                f"Embedding API returned {len(data)} vectors, expected {len(texts)}."
            )
        vectors: list[list[float]] = []
        for item in data:
            vec = item["embedding"]
            if len(vec) != self._settings.embedding_dimensions:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self._settings.embedding_dimensions}, "
                    f"got {len(vec)}."
                )
            vectors.append(vec)
        return vectors

    def embed_query(self, query: str) -> list[float]:
        vectors = self.embed_texts([query])
        return vectors[0]


class MockEmbeddingClient(EmbeddingClient):
    """测试用 mock embedding 客户端。

    使用文本的 SHA256 哈希生成确定性、可重复的固定维度向量。
    向量归一化后返回，保证稳定。
    """

    def __init__(self, settings: Settings) -> None:
        self._dimensions = settings.embedding_dimensions

    def _hash_vector(self, text: str) -> list[float]:
        """基于文本哈希生成确定性的归一化向量。"""
        hash_bytes = hashlib.sha256(text.encode("utf-8")).digest()
        # 使用哈希字节生成 dimensions 个浮点数
        vec: list[float] = []
        for i in range(self._dimensions):
            # 循环使用哈希字节生成伪随机值
            b = hash_bytes[i % len(hash_bytes)]
            # 映射到 [-1, 1] 范围
            val = (b / 127.5) - 1.0
            vec.append(val)
        # L2 归一化
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._hash_vector(query)


def get_embedding_client(settings: Settings | None = None) -> EmbeddingClient:
    """工厂函数：根据配置返回对应的 embedding 客户端。"""
    if settings is None:
        settings = get_settings()
    provider = settings.embedding_provider
    if provider == "mock":
        return MockEmbeddingClient(settings)
    if provider == "openai_compatible":
        return OpenAICompatibleEmbeddingClient(settings)
    raise ValueError(f"Unknown embedding provider: {provider}")
