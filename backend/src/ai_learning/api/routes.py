import httpx
import logging
import os
import re
import secrets
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, Response as FastAPIResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session, selectinload

from ai_learning.agent.graph import run_agent
from ai_learning.api.schemas import (
    AgentRequest,
    AgentResponse,
    ConversationDetail,
    ConversationListResponse,
    ConversationMessageOut,
    ConversationRenameRequest,
    ConversationSummary,
    DocumentListItem,
    DocumentUploadResponse,
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    QueryRequest,
    QueryResponse,
    QuerySource,
    UserMeResponse,
)
from ai_learning.auth import (
    clear_auth_cookie,
    create_token,
    generate_captcha,
    get_current_user,
    hash_password,
    set_auth_cookie,
    verify_and_consume_captcha,
    verify_login_password,
    verify_password,
)
from ai_learning.core.config import get_settings
from ai_learning.core.llm_client import LLMClient
from ai_learning.db import get_db
from ai_learning.models import Conversation, ConversationMessage, Document, DocumentChunk, User
from ai_learning.rag.embedder import (
    EmbeddingCancelledError,
    EmbeddingServiceError,
    get_embedding_client,
)
from ai_learning.rag.retriever import retrieve
from ai_learning.services import save_uploaded_document
from ai_learning.upload_control import finish_upload, register_upload, request_upload_cancel

logger = logging.getLogger(__name__)

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])


# ── 辅助函数 ──


def _build_user_response(user: User) -> dict:
    """构建包含资料字段和头像 URL 的用户响应字典。"""
    settings = get_settings()
    avatar_url = None
    if user.avatar_path:
        # 使用微秒级时间戳确保快速连续替换时版本号也不同
        ts_us = int(user.updated_at.timestamp() * 1_000_000)
        avatar_url = f"/auth/me/avatar?v={ts_us}"
    return {
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "phone": user.phone,
        "bio": user.bio,
        "avatar_url": avatar_url,
    }


_EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_PHONE_RE = re.compile(r"^[0-9 +\-()]{1,32}$")


# ── 公开接口 ──


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ── 认证接口 ──


@auth_router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, response: Response, db: Session = Depends(get_db)) -> LoginResponse:
    """用户名密码登录，先校验验证码再校验密码，成功后设置 HttpOnly Cookie。

    始终执行 Argon2 密码校验以消除用户名时序枚举：
    - 存在且活跃用户使用真实 hash 校验
    - 不存在或停用用户使用固定 dummy hash 校验
    - 最终所有失败路径统一返回相同 401 文案。
    """
    # 1. 校验并消费验证码（消费状态在 verify_and_consume_captcha 内部已提交）
    if not verify_and_consume_captcha(db, request.captcha_id, request.captcha_code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期，请刷新后重试。")

    # 2. 用户名密码校验
    username = request.username.strip().lower()

    user = db.query(User).filter(User.username == username).first()
    is_active = user.is_active if user is not None else False

    exists_and_active, password_ok = verify_login_password(request.password, user, is_active)

    if not exists_and_active or not password_ok:
        raise HTTPException(status_code=401, detail="用户名或密码错误。")

    # user 一定非 None（exists_and_active 为 True 保证了这一点）
    token = create_token(user.id)  # type: ignore[union-attr]
    set_auth_cookie(response, token)

    return LoginResponse(**_build_user_response(user))  # type: ignore[union-attr]


@auth_router.post("/logout", status_code=204)
def logout(
    response: Response,
    user: User = Depends(get_current_user),
) -> None:
    """退出登录，清除认证 Cookie。"""
    clear_auth_cookie(response)


@auth_router.get("/me", response_model=UserMeResponse)
def me(user: User = Depends(get_current_user)) -> UserMeResponse:
    """返回当前已登录用户信息。"""
    return UserMeResponse(**_build_user_response(user))


# ── 验证码接口 ──


@auth_router.get("/captcha")
def captcha(db: Session = Depends(get_db)) -> Response:
    """生成验证码图片，返回 image/png，通过 X-Captcha-Id 头返回挑战 ID。"""
    captcha_id, png_bytes = generate_captcha(db)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-Captcha-Id": captcha_id,
            "Cache-Control": "no-store, no-cache, must-revalidate",
        },
    )


# ── 个人资料接口 ──


@auth_router.put("/me/profile", response_model=UserMeResponse)
def update_profile(
    body: ProfileUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UserMeResponse:
    """更新当前用户的个人资料。"""
    # 邮箱格式校验
    if body.email is not None and not _EMAIL_RE.match(body.email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确。")

    # 手机号字符校验
    if body.phone is not None and not _PHONE_RE.match(body.phone):
        raise HTTPException(status_code=400, detail="手机号包含无效字符。")

    user.display_name = body.display_name
    user.email = body.email
    user.phone = body.phone
    user.bio = body.bio
    db.commit()
    db.refresh(user)

    return UserMeResponse(**_build_user_response(user))


# ── 头像接口 ──


def _avatar_root() -> Path:
    """头像根目录：从 upload_dir 派生，保证 Docker 持久化一致。"""
    settings = get_settings()
    return Path(settings.upload_dir) / "avatars"


def _resolve_avatar_path(user_avatar_path: str | None) -> tuple[Path, Path] | None:
    """安全解析头像路径：返回 (absolute_path, avatar_root)。

    仅用于 GET/DELETE；数据库存的是相对路径（如 avatars/1.webp）。
    """
    if not user_avatar_path:
        return None
    root = _avatar_root().resolve()
    resolved = root / Path(user_avatar_path).name
    try:
        resolved.resolve().relative_to(root)
    except ValueError:
        return None
    return resolved, root


def _validate_and_process_avatar(file: UploadFile) -> bytes:
    """校验并处理头像上传：防御伪造/损坏/解压炸弹，验证图片、缩放、转 WebP。

    所有 Pillow 操作在受控异常边界内执行，任何异常转为 400。
    """
    import warnings

    settings = get_settings()

    # 限制读取大小（2 MB）
    raw = file.file.read(settings.avatar_max_bytes + 1)
    if len(raw) > settings.avatar_max_bytes:
        raise HTTPException(status_code=400, detail="头像文件大小不能超过 2 MB。")

    # 保守像素上限：4096×4096 = 16.7M 像素，匹配服务内存预算
    max_pixels = 4096 * 4096

    from PIL import Image as PILImage

    try:
        # 第一阶段：验证图片完整性并检测格式伪造
        img = PILImage.open(BytesIO(raw))
        img.verify()
    except (UnidentifiedImageError, Exception):
        raise HTTPException(status_code=400, detail="无法识别的图片格式，请上传 JPEG、PNG 或 WebP。")

    # 局部 warning 上下文：把 DecompressionBombWarning 转为 400
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("error", PILImage.DecompressionBombWarning)
        try:
            # 第二阶段：重新打开并完整解码
            img = PILImage.open(BytesIO(raw))
            if img.format not in ("JPEG", "PNG", "WEBP"):
                raise HTTPException(status_code=400, detail="仅支持 JPEG、PNG、WebP 格式。")

            # 预检总像素（在完整解码前拒绝）
            w, h = img.size
            if w * h > max_pixels:
                raise HTTPException(status_code=400, detail="图片像素过大。")

            # 完整加载像素数据（通过 DecompressionBombWarning 防护）
            img.load()

            # EXIF 方向纠正
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)

            # 转换为 RGB/RGBA
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")

            # 缩放到最长边不超过 max_pixels
            max_dim = max(img.size)
            if max_dim > settings.avatar_max_pixels:
                ratio = settings.avatar_max_pixels / max_dim
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, PILImage.LANCZOS)

            # 转 WebP
            out = BytesIO()
            img.save(out, format="WEBP", quality=85)
            return out.getvalue()

        except PILImage.DecompressionBombWarning:
            raise HTTPException(status_code=400, detail="图片像素过大。")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="图片处理失败，请确认上传的是有效图片。")


@auth_router.put("/me/avatar", response_model=UserMeResponse)
def upload_avatar(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UserMeResponse:
    """上传/替换当前用户头像。

    - 固定目标文件名 avatars/{user_id}.webp
    - 同目录临时文件 + os.replace 原子替换
    - 数据库存储受控相对路径
    """
    # 校验并处理图片
    webp_bytes = _validate_and_process_avatar(file)

    # 确保头像目录存在
    avatar_root = _avatar_root()
    avatar_root.mkdir(parents=True, exist_ok=True)

    # 固定目标文件名
    target = avatar_root / f"{user.id}.webp"

    # 同目录临时文件 + 原子替换
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(avatar_root), suffix=".webp")
    try:
        with open(tmp_fd, "wb") as f:
            f.write(webp_bytes)
        os.replace(tmp_path, str(target))  # 原子替换：POSIX rename
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="头像保存失败。")

    # 数据库存受控相对路径；显式更新时间戳确保版本号变化
    from datetime import datetime as _dt
    user.avatar_path = f"avatars/{user.id}.webp"
    user.updated_at = _dt.utcnow()  # type: ignore[assignment]
    db.commit()
    db.refresh(user)

    return UserMeResponse(**_build_user_response(user))


@auth_router.delete("/me/avatar", response_model=UserMeResponse)
def delete_avatar(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UserMeResponse:
    """删除当前用户头像。文件不存在时幂等成功。

    使用与 GET 相同的安全路径解析，防止目录遍历。
    """
    if user.avatar_path:
        resolved = _resolve_avatar_path(user.avatar_path)
        if resolved is not None:
            avatar_path, _root = resolved
            avatar_path.unlink(missing_ok=True)
        user.avatar_path = None
        db.commit()
        db.refresh(user)

    return UserMeResponse(**_build_user_response(user))


@auth_router.get("/me/avatar")
def get_avatar(user: User = Depends(get_current_user)) -> FileResponse:
    """返回当前用户头像。无头像或文件不存在返回 404。"""
    if not user.avatar_path:
        raise HTTPException(status_code=404, detail="头像不存在。")

    resolved = _resolve_avatar_path(user.avatar_path)
    if resolved is None:
        raise HTTPException(status_code=404, detail="头像不存在。")
    avatar_path, _root = resolved

    if not avatar_path.is_file():
        raise HTTPException(status_code=404, detail="头像不存在。")

    return FileResponse(
        path=str(avatar_path),
        media_type="image/webp",
    )


# ── 修改密码接口 ──


@auth_router.put("/me/password", status_code=204)
def change_password(
    body: PasswordChangeRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """修改当前用户密码，成功后清除认证 Cookie。"""
    # 校验当前密码
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码错误。")

    # 新旧密码相同
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同。")

    # 更新密码
    user.password_hash = hash_password(body.new_password)
    db.commit()

    # 清除认证 Cookie，要求重新登录
    clear_auth_cookie(response)


# ── 业务接口（均需认证）──


@router.post("/documents/upload", response_model=DocumentUploadResponse)
def upload_document(
    file: UploadFile,
    x_upload_id: str | None = Header(default=None, alias="X-Upload-Id"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentUploadResponse:
    if x_upload_id is not None and len(x_upload_id) > 64:
        raise HTTPException(status_code=400, detail="上传标识无效。")

    cancel_event = register_upload(user.id, x_upload_id) if x_upload_id else None
    try:
        document = save_uploaded_document(
            file,
            db,
            user_id=user.id,
            should_cancel=cancel_event.is_set if cancel_event is not None else None,
        )
    except EmbeddingCancelledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmbeddingServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        if x_upload_id:
            finish_upload(user.id, x_upload_id)

    return DocumentUploadResponse(
        document_id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        chunk_count=len(document.chunks),
    )


@router.delete("/documents/upload/{upload_id}", status_code=204)
def cancel_document_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
) -> None:
    """幂等取消当前用户的文档上传或向量处理任务。"""
    if not upload_id or len(upload_id) > 64:
        raise HTTPException(status_code=400, detail="上传标识无效。")
    request_upload_cancel(user.id, upload_id)


def _resolve_conversation(
    conversation_id: int | None,
    db: Session,
    user: User,
) -> Conversation | None:
    """校验会话归属，返回会话对象或 404。

    不存在、已删除或属于其他用户统一返回 404（避免资源存在性泄露）。
    """
    if conversation_id is None:
        return None
    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
        )
        .first()
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在。")
    return conv


def _build_user_history(conv: Conversation | None, current_question: str) -> tuple[str, str]:
    """根据历史消息构建检索文本和提示词历史段落。

    返回 (search_text, history_text)：
    - search_text: 最近 3 条用户问题 + 当前问题，用于向量检索改善指代追问。
    - history_text: 最近 10 轮问答的格式化文本。
    """
    if conv is None:
        return current_question, ""

    messages = conv.messages  # 已按 created_at 正序
    # 最近 3 条用户问题
    user_questions = [m.content for m in messages if m.role == "user"][-3:]
    search_text = "\n".join(user_questions + [current_question])

    # 最近 20 条消息（10 轮问答）
    recent = messages[-20:]
    history_parts: list[str] = []
    for m in recent:
        role_label = "用户" if m.role == "user" else "助手"
        history_parts.append(f"{role_label}：{m.content}")
    history_text = "\n".join(history_parts)

    return search_text, history_text


def _persist_conversation_messages(
    db: Session,
    user_id: int,
    conversation_id: int,
    question: str,
    answer: str,
    sources_snapshot: list[dict] | None,
    is_new: bool,
) -> tuple[int, str]:
    """在同一事务中保存用户消息、助手消息并更新会话时间/标题。

    返回 (conversation_id, title)。
    """
    now = datetime.utcnow()
    title = question.strip()[:30]

    if is_new:
        conv = Conversation(
            user_id=user_id,
            title=title,
            created_at=now,
            last_message_at=now,
        )
        db.add(conv)
        db.flush()  # 获取 ID
        conv_id = conv.id
    else:
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conv is None:
            raise HTTPException(status_code=500, detail="会话状态异常。")
        conv.last_message_at = now
        conv_id = conv.id
        title = conv.title

    user_msg = ConversationMessage(
        conversation_id=conv_id,
        role="user",
        content=question,
        created_at=now,
    )
    db.add(user_msg)

    assistant_msg = ConversationMessage(
        conversation_id=conv_id,
        role="assistant",
        content=answer,
        sources=sources_snapshot,
        created_at=now,
    )
    db.add(assistant_msg)

    db.commit()
    return conv_id, title


@router.post("/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QueryResponse:
    settings = get_settings()
    log_prefix = (
        f"查询请求 | user_id={user.id} conv_id={request.conversation_id} "
        f"question_length={len(request.question)} top_k={request.top_k}"
    )
    if settings.rag_debug_logs:
        logger.info(log_prefix)

    # 校验会话归属
    conv = _resolve_conversation(request.conversation_id, db, user)

    # 检查当前用户是否有文档
    chunk_count = (
        db.query(DocumentChunk)
        .join(Document, DocumentChunk.document_id == Document.id)
        .filter(Document.user_id == user.id, Document.deleted_at.is_(None))
        .count()
    )
    if chunk_count == 0:
        if settings.rag_debug_logs:
            logger.info("查询拒绝 | user_id=%d 原因=无文档", user.id)
        raise HTTPException(status_code=404, detail="No documents have been uploaded.")

    # 构建检索文本（含历史用户问题改善指代追问）和提示词历史
    search_text, history_text = _build_user_history(conv, request.question)

    # 向量检索召回
    try:
        embedder = get_embedding_client(settings)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        results = retrieve(
            query=search_text,
            db=db,
            embedder=embedder,
            top_k=request.top_k,
            min_score=settings.retrieval_min_score,
            user_id=user.id,
        )
    except EmbeddingServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"Embedding error: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Embedding provider error: {exc}") from exc

    # 构建 Source 列表和快照
    context_parts: list[str] = []
    sources: list[QuerySource] = []
    sources_snapshot: list[dict] = []
    for idx, result in enumerate(results, start=1):
        chunk = result.chunk
        context_parts.append(f"[{idx}] {chunk.chunk_text}")
        source = QuerySource(
            document_id=chunk.document_id,
            filename=chunk.document.filename,
            file_type=chunk.document.file_type,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_metadata.get("chunk_index", chunk.chunk_index),
            chunk_text=chunk.chunk_text,
            score=round(result.score, 4),
            chunk_metadata=chunk.chunk_metadata,
        )
        sources.append(source)
        sources_snapshot.append(source.model_dump())

    # 低相关度拒答（仍需持久化）
    if not results:
        if settings.rag_debug_logs:
            logger.info(
                "查询拒答 | user_id=%d 原因=低相关度 阈值=%.2f",
                user.id,
                settings.retrieval_min_score,
            )
        answer = "文档中没有找到足够依据。"
        conv_id, conv_title = _persist_conversation_messages(
            db,
            user.id,
            request.conversation_id,
            request.question,
            answer,
            None,
            is_new=(conv is None),
        )
        return QueryResponse(
            question=request.question,
            answer=answer,
            sources=[],
            conversation_id=conv_id,
            conversation_title=conv_title,
        )

    # 构建提示词
    context = "\n\n".join(context_parts)
    prompt_parts: list[str] = []
    prompt_parts.append(
        "请仅根据以下检索到的上下文回答用户问题。如果上下文中没有足够依据，请明确说"
        "\"根据已有文档，无法回答该问题\"，不要编造任何信息。\n"
    )
    if history_text:
        prompt_parts.append(f"## 历史对话\n{history_text}\n")
    prompt_parts.append(f"## 检索上下文（带引用编号）\n{context}")
    prompt_parts.append(f"## 用户问题\n{request.question}")
    prompt = "\n\n".join(prompt_parts)

    # LLM 调用
    try:
        answer = LLMClient().complete(prompt)
    except ValueError as exc:
        # LLM 配置缺失：不保存消息
        if settings.rag_debug_logs:
            logger.warning("查询失败 | user_id=%d 原因=LLM配置缺失", user.id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        # LLM 上游网络错误：不保存消息
        if settings.rag_debug_logs:
            logger.warning("查询失败 | user_id=%d 原因=LLM上游错误", user.id)
        raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc

    # 持久化消息（成功回答或拒答都在此处）
    conv_id, conv_title = _persist_conversation_messages(
        db,
        user.id,
        request.conversation_id,
        request.question,
        answer,
        sources_snapshot,
        is_new=(conv is None),
    )

    if settings.rag_debug_logs:
        logger.info(
            "查询完成 | user_id=%d conv_id=%d answer_length=%d source_count=%d",
            user.id,
            conv_id,
            len(answer),
            len(sources),
        )
    return QueryResponse(
        question=request.question,
        answer=answer,
        sources=sources,
        conversation_id=conv_id,
        conversation_title=conv_title,
    )


# ── 会话接口 ──


@router.get("/conversations", response_model=ConversationListResponse)
def list_conversations(
    offset: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConversationListResponse:
    """返回当前用户未删除会话的分页列表，按 last_message_at 倒序。"""
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset 不能为负数。")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit 必须在 1～100 之间。")

    base = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id, Conversation.deleted_at.is_(None))
    )
    total = base.count()
    items = (
        base
        .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return ConversationListResponse(
        items=[
            ConversationSummary(
                id=c.id,
                title=c.title,
                created_at=c.created_at,
                last_message_at=c.last_message_at,
            )
            for c in items
        ],
        total=total,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConversationDetail:
    """返回会话详情及全部消息（按创建时间正序）。"""
    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
        )
        .first()
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在。")

    messages = [
        ConversationMessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            sources=(
                [QuerySource(**s) for s in m.sources]
                if m.sources is not None
                else None
            ),
            created_at=m.created_at,
        )
        for m in conv.messages
    ]

    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        last_message_at=conv.last_message_at,
        messages=messages,
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: int,
    body: ConversationRenameRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConversationSummary:
    """重命名当前用户的会话。"""
    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
        )
        .first()
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在。")

    conv.title = body.title
    db.commit()
    db.refresh(conv)

    return ConversationSummary(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        last_message_at=conv.last_message_at,
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """软删除当前用户的会话。"""
    conv = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
        )
        .first()
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在。")

    conv.deleted_at = datetime.utcnow()
    db.commit()


# ── Agent 接口 ──


@router.post("/agent", response_model=AgentResponse)
def agent(
    request: AgentRequest,
    user: User = Depends(get_current_user),
) -> AgentResponse:
    result = run_agent(request.task)
    return AgentResponse(task=request.task, result=result)


@router.get("/documents", response_model=list[DocumentListItem])
def list_documents(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DocumentListItem]:
    """获取当前用户的历史文档列表，按创建时间倒序排列。"""
    documents = (
        db.query(Document)
        .options(selectinload(Document.chunks))
        .filter(Document.user_id == user.id, Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc(), Document.id.desc())
        .all()
    )

    return [
        DocumentListItem(
            document_id=doc.id,
            filename=doc.filename,
            file_type=doc.file_type,
            chunk_count=len(doc.chunks),
            created_at=doc.created_at,
        )
        for doc in documents
    ]


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """逻辑删除当前用户的文档，并保留原文件和分片数据。"""
    # 同时校验用户归属和未删除状态，避免泄露其他用户或历史文档是否存在。
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.user_id == user.id,
            Document.deleted_at.is_(None),
        )
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    # 只写删除时间，不移除数据库记录、分片或磁盘原文件。
    document.deleted_at = datetime.utcnow()
    db.commit()


# 文件类型到 MIME 的映射
_MIME_TYPES: dict[str, str] = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
}


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """下载原文件，校验路径安全和用户归属后返回原始文件内容。"""
    settings = get_settings()

    # 同时按文档 ID 和当前 user_id 查询，避免资源存在性泄露
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.user_id == user.id,
            Document.deleted_at.is_(None),
        )
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    # 规范化路径并校验是否位于上传目录内，防止路径遍历攻击
    upload_dir = Path(settings.upload_dir).resolve()
    saved_path = Path(document.saved_path).resolve()
    try:
        saved_path.relative_to(upload_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail="Document file not found.")

    # 检查磁盘文件是否存在
    if not saved_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found.")

    media_type = _MIME_TYPES.get(document.file_type, "application/octet-stream")

    return FileResponse(
        path=str(saved_path),
        filename=document.filename,
        media_type=media_type,
    )
