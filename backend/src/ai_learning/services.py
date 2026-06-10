from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from ai_learning.core.config import get_settings
from ai_learning.models import Document, DocumentChunk
from ai_learning.rag.loader import parse_text_file, split_chunks, validate_document_filename


def save_uploaded_document(file: UploadFile, db: Session) -> Document:
    settings = get_settings()
    filename = Path(file.filename or "").name
    file_type = validate_document_filename(filename)

    content = file.file.read()
    if len(content) > settings.upload_max_bytes:
        raise ValueError(f"Document size cannot exceed {settings.upload_max_bytes} bytes.")

    raw_text = parse_text_file(content)
    chunks = split_chunks(raw_text)
    if not chunks:
        raise ValueError("Document did not produce any chunks.")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / f"{uuid4().hex}-{filename}"
    saved_path.write_bytes(content)

    document = Document(
        filename=filename,
        file_type=file_type,
        saved_path=str(saved_path),
        raw_text=raw_text,
    )
    document.chunks = [
        DocumentChunk(chunk_index=index, chunk_text=chunk)
        for index, chunk in enumerate(chunks)
    ]
    db.add(document)
    db.commit()
    db.refresh(document)
    return document
