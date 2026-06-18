"""基于 pgvector 的语义检索模块。

使用 pgvector cosine distance 算子进行向量相似度召回，
score 统一为 "越大越相关"（1 - cosine_distance）。
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session, joinedload

from ai_learning.core.config import get_settings
from ai_learning.models import DocumentChunk
from ai_learning.rag.embedder import EmbeddingClient, get_embedding_client


@dataclass
class RetrievalResult:
    """单条检索结果。"""
    chunk: DocumentChunk
    score: float  # 越大越相关，范围约 [0, 1]


def retrieve(
    query: str,
    db: Session,
    embedder: EmbeddingClient | None = None,
    top_k: int | None = None,
    candidate_k: int | None = None,
    min_score: float | None = None,
) -> list[RetrievalResult]:
    """使用 pgvector 向量检索召回最相关的 chunk。

    参数：
        query: 用户查询文本
        db: 数据库 Session
        embedder: embedding 客户端，为 None 时通过工厂函数创建
        top_k: 返回结果数
        candidate_k: 候选召回数（先召回候选池再截取 top_k）
        min_score: 最低相关度阈值

    返回：
        RetrievalResult 列表，按 score 降序排列
    """
    settings = get_settings()
    if embedder is None:
        embedder = get_embedding_client(settings)
    if top_k is None:
        top_k = settings.retrieval_top_k
    if candidate_k is None:
        candidate_k = settings.retrieval_candidate_k
    if min_score is None:
        min_score = settings.retrieval_min_score

    # 生成查询向量
    query_embedding = embedder.embed_query(query)

    # 使用 pgvector cosine_distance 排序召回
    # 通过 comparator 访问 <=> 运算符，返回余弦距离（越小越相似）
    # score = 1 - distance 转换为越大越相关
    distance_expr = DocumentChunk.embedding.cosine_distance(query_embedding)
    candidates = (
        db.query(
            DocumentChunk,
            distance_expr.label("distance"),
        )
        .options(joinedload(DocumentChunk.document))
        .order_by(distance_expr)
        .limit(candidate_k)
        .all()
    )

    results: list[RetrievalResult] = []
    for chunk, distance in candidates:
        score = 1.0 - distance  # 转换为越大越相关
        if score >= min_score:
            results.append(RetrievalResult(chunk=chunk, score=score))

    # 截取 top_k
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]
