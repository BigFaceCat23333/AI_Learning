from pathlib import Path


ALLOWED_DOCUMENT_EXTENSIONS = {".txt", ".md"}


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


def split_chunks(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between 0 and chunk_size.")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunk = text[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks
