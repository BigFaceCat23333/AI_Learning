import hashlib
from dataclasses import dataclass
from pathlib import Path

ALLOWED_DOCUMENT_EXTENSIONS = {".txt", ".md"}


@dataclass
class ParsedChunk:
    """解析后的分块结构，包含文本和元数据。"""
    text: str
    chunk_metadata: dict
    content_hash: str
    char_count: int


def load_documents(documents: list[str]) -> list[str]:
    return [document.strip() for document in documents if document.strip()]


def validate_document_filename(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_DOCUMENT_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))
        raise ValueError(f"Only {allowed} files are supported.")
    return suffix.removeprefix(".")


def parse_text_file(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Only UTF-8 encoded documents are supported.") from exc

    normalized = text.strip()
    if not normalized:
        raise ValueError("Document content cannot be empty.")
    return normalized


def _compute_content_hash(text: str) -> str:
    """计算 chunk 文本的 SHA256 哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_chunks(
    text: str,
    filename: str = "",
    file_type: str = "",
    chunk_size: int = 800,
    overlap: int = 100,
) -> list[ParsedChunk]:
    """将文本按固定大小分块，返回带元数据的 ParsedChunk 列表。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between 0 and chunk_size.")

    chunks: list[ParsedChunk] = []
    start = 0
    chunk_index = 0
    while start < len(text):
        chunk_text = text[start : start + chunk_size].strip()
        if chunk_text:
            chunks.append(
                ParsedChunk(
                    text=chunk_text,
                    chunk_metadata={
                        "filename": filename,
                        "file_type": file_type,
                        "chunk_index": chunk_index,
                        "char_start": start,
                        "char_end": start + len(chunk_text),
                    },
                    content_hash=_compute_content_hash(chunk_text),
                    char_count=len(chunk_text),
                )
            )
            chunk_index += 1
        start += chunk_size - overlap
    return chunks
