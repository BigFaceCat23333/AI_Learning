FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.5.30 /uv /uvx /bin/

WORKDIR /app/backend

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY application.yml ./
COPY src ./src

RUN mkdir -p /app/uploads

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONPATH=/app/backend/src \
    AI_LEARNING_UPLOAD_DIR=/app/uploads

EXPOSE 8000

CMD ["uvicorn", "ai_learning.main:app", "--host", "0.0.0.0", "--port", "8000"]
