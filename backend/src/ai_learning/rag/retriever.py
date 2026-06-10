from ai_learning.rag.embedder import cosine_similarity, embed


def score_documents(query: str, documents: list[str], top_k: int = 3) -> list[tuple[str, float]]:
    query_vector = embed(query)
    scored = [
        (document, cosine_similarity(query_vector, embed(document)))
        for document in documents
    ]
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)
    return ranked[:top_k]


def retrieve(query: str, documents: list[str], top_k: int = 3) -> list[str]:
    return [document for document, _score in score_documents(query, documents, top_k)]
