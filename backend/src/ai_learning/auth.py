"""认证模块：密码哈希、JWT、Cookie 和当前用户依赖。"""

import logging
import secrets
import time

import jwt
from fastapi import Depends, HTTPException, Request, Response
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy.orm import Session

from ai_learning.core.config import get_settings
from ai_learning.db import get_db
from ai_learning.models import User

logger = logging.getLogger(__name__)

# Argon2id 密码哈希，使用推荐参数
_hasher = PasswordHash([Argon2Hasher()])

# 固定的 dummy hash，用于不存在/停用用户时仍执行 Argon2 校验以消除时序差异。
# 该 hash 由 Argon2id 对固定已知种子计算得出，永不对应任何真实密码。
_DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$mRcNrgbcbd88/xLdNIzaIQ$N6LKW+p/xCyeVwkx4shp1xpCSjrnjCapKBYEYm7HsAg"


def hash_password(password: str) -> str:
    """使用 Argon2id 随机盐单向哈希密码。"""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """恒定时间安全校验密码。"""
    return _hasher.verify(password, password_hash)


def verify_login_password(password: str, user: User | None, is_active: bool) -> tuple[bool, bool]:
    """登录密码校验：始终执行 Argon2 校验以消除用户名时序枚举。

    参数：
        password: 用户提交的密码
        user: 数据库中的用户对象（可能为 None）
        is_active: 该用户是否活跃

    返回：
        (user_exists_and_active, password_correct)
    """
    if user is not None and is_active:
        return (True, _hasher.verify(password, user.password_hash))
    # 不存在或停用用户：使用 dummy hash 执行等价 Argon2 校验
    _hasher.verify(password, _DUMMY_HASH)
    return (False, False)


def create_token(user_id: int) -> str:
    """签发包含 sub/iat/exp 的 HS256 JWT。"""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + settings.auth_token_ttl_seconds,
    }
    return jwt.encode(payload, settings.auth_secret, algorithm="HS256")


def set_auth_cookie(response: Response, token: str) -> None:
    """在响应中设置 HttpOnly 认证 Cookie。"""
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_token_ttl_seconds,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.auth_cookie_secure,
    )


def clear_auth_cookie(response: Response) -> None:
    """清除认证 Cookie。"""
    settings = get_settings()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        samesite="lax",
        secure=settings.auth_cookie_secure,
    )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """从 Cookie 读取 Token，验证签名和过期时间，返回当前用户。

    无效/过期 Token、用户不存在或已停用时统一返回 401。
    """
    settings = get_settings()
    token = request.cookies.get(settings.auth_cookie_name)

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    try:
        payload = jwt.decode(token, settings.auth_secret, algorithms=["HS256"])
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Not authenticated.")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    return user


def generate_random_password() -> str:
    """使用安全随机源生成高强度随机密码。"""
    return secrets.token_urlsafe(24)
