from ai_learning.rag.loader import load_documents, parse_text_file, split_chunks, validate_document_filename
from ai_learning.rag.retriever import retrieve


def test_load_documents_removes_empty_values() -> None:
    assert load_documents([" hello ", "", "world"]) == ["hello", "world"]


def test_retrieve_returns_most_relevant_document_first() -> None:
    documents = ["python fastapi service", "database migration", "frontend style"]
    assert retrieve("fastapi python", documents, top_k=1) == ["python fastapi service"]


def test_retrieve_supports_basic_chinese_text() -> None:
    # 匹配的文档放在第二位，证明检索是按相关性而非原始顺序命中
    documents = ["前端页面样式调整", "FastAPI 上传文档并解析入库"]
    assert retrieve("文档入库", documents, top_k=1) == ["FastAPI 上传文档并解析入库"]


def test_validate_document_filename_rejects_unsupported_extension() -> None:
    try:
        validate_document_filename("demo.pdf")
    except ValueError as exc:
        assert "supported" in str(exc)
    else:
        raise AssertionError("Expected unsupported extension to fail")


def test_parse_text_file_rejects_empty_content() -> None:
    try:
        parse_text_file(b"   ")
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("Expected empty file to fail")


def test_split_chunks_uses_overlap() -> None:
    chunks = split_chunks("abcdefghij", chunk_size=6, overlap=2)
    assert chunks == ["abcdef", "efghij", "ij"]
