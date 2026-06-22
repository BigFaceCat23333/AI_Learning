import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_learning.api.routes import router
from ai_learning.core.config import get_settings
from ai_learning.db import init_db

CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def setup_logging() -> None:
    """根据配置初始化日志级别。

    当 rag_debug_logs=True 时，将 ai_learning 及其子 logger 设为 INFO，
    确保 Docker/Uvicorn 运行时可通过 docker compose logs 看到调试日志。
    """
    settings = get_settings()
    if settings.rag_debug_logs:
        # 只提升项目自身的日志级别，不影响第三方库
        project_logger = logging.getLogger("ai_learning")
        project_logger.setLevel(logging.INFO)
        # 确保至少有一个 handler 输出到 stdout/stderr（uvicorn 已配置，此处为兜底）
        if not project_logger.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            project_logger.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api")
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("ai_learning.main:app", host="0.0.0.0", port=8000, reload=True)
