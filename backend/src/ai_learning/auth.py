"""认证模块：密码哈希、JWT、Cookie、验证码和当前用户依赖。"""

import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import uuid4

import jwt
from fastapi import Depends, HTTPException, Request, Response
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy.orm import Session

from ai_learning.core.config import get_settings
from ai_learning.db import get_db
from ai_learning.models import CaptchaChallenge, User

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


# ── 验证码 ──

# 验证码候选字符：排除 0/O、1/I/L 等易混淆字符
_CAPTCHA_CHARS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _captcha_answer_digest(captcha_id: str, answer: str) -> str:
    """使用 HMAC-SHA256 计算验证码答案摘要。

    参数：
        captcha_id: 挑战 UUID
        answer: 用户提交的原始答案（已规范化转大写并去空格）

    返回 hex 摘要字符串。
    """
    settings = get_settings()
    key = settings.auth_secret.encode("utf-8")
    message = f"{captcha_id}:{answer}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _normalize_captcha_answer(raw: str) -> str:
    """规范化验证码答案：去空格、转大写。"""
    return raw.strip().upper()


def generate_captcha(db: Session) -> tuple[str, bytes]:
    """生成验证码，写入数据库并返回 PNG 图片。

    返回：
        (captcha_id, png_bytes)
    """
    settings = get_settings()

    # 生成随机验证码
    code = "".join(secrets.choice(_CAPTCHA_CHARS) for _ in range(settings.captcha_length))

    # 计算摘要并写入数据库
    captcha_id = uuid4().hex
    digest = _captcha_answer_digest(captcha_id, _normalize_captcha_answer(code))
    now = datetime.utcnow()
    challenge = CaptchaChallenge(
        id=captcha_id,
        answer_digest=digest,
        expires_at=now + timedelta(seconds=settings.captcha_ttl_seconds),
        created_at=now,
    )
    db.add(challenge)

    # 顺带清理过期挑战
    db.query(CaptchaChallenge).filter(CaptchaChallenge.expires_at < now).delete()

    db.commit()

    # 生成 PNG 图片
    png_bytes = _draw_captcha_image(code, settings.captcha_width, settings.captcha_height)

    return captcha_id, png_bytes


def _draw_captcha_image(code: str, width: int, height: int) -> bytes:
    """使用 Pillow 绘制验证码 PNG 图片（干扰线、噪点、随机偏移）。"""
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # 尝试使用系统字体，回退到 46px 默认字体（Docker 中确保字符足够大）
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 46)
        except (OSError, IOError):
            font = ImageFont.load_default(size=46)

    # 干扰线
    for _ in range(5):
        x1 = secrets.randbelow(width)
        y1 = secrets.randbelow(height)
        x2 = secrets.randbelow(width)
        y2 = secrets.randbelow(height)
        color = (secrets.randbelow(128), secrets.randbelow(128), secrets.randbelow(128))
        draw.line([(x1, y1), (x2, y2)], fill=color, width=1)

    # 噪点
    for _ in range(80):
        x = secrets.randbelow(width)
        y = secrets.randbelow(height)
        color = (secrets.randbelow(200), secrets.randbelow(200), secrets.randbelow(200))
        draw.point((x, y), fill=color)

    # 绘制每个字符，根据 bbox 限制偏移，确保不越界、不越槽位
    char_width = width // len(code)
    margin = 2
    for i, ch in enumerate(code):
        bbox = draw.textbbox((0, 0), ch, font=font)

        # 字符槽位左右边界
        slot_left = char_width * i
        slot_right = slot_left + char_width

        # 横向：字符字形必须在 [slot_left + margin, slot_right - margin] 内
        x_min = slot_left + margin - bbox[0]
        x_max = slot_right - margin - bbox[2]
        if x_max < x_min:
            x_max = x_min
        x = x_min + secrets.randbelow(max(1, x_max - x_min + 1))

        # 纵向：字符字形必须在 [margin, height - margin] 内
        y_min = margin - bbox[1]
        y_max = height - margin - bbox[3]
        if y_max < y_min:
            y_max = y_min
        y = y_min + secrets.randbelow(max(1, y_max - y_min + 1))

        color = (
            secrets.randbelow(100),
            secrets.randbelow(100),
            secrets.randbelow(100),
        )
        draw.text((x, y), ch, fill=color, font=font)

    # 轻微模糊增加识别难度
    image = image.filter(ImageFilter.GaussianBlur(radius=0.5))

    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def verify_and_consume_captcha(db: Session, captcha_id: str, captcha_code: str) -> bool:
    """在数据库事务中校验并消费验证码。

    使用行锁 (SELECT ... FOR UPDATE) 保证同一挑战只能被消费一次。
    只要挑战存在、未过期且未消费，无论答案正确与否都会提交 consumed_at，
    确保"每次登录尝试都消费验证码"的一次性规则。

    返回：
        True 表示验证通过且已消费；False 表示验证失败（不存在/过期/已消费/答案错误）。
    """
    settings = get_settings()
    now = datetime.utcnow()

    # 在事务中查询并锁定记录
    challenge = (
        db.query(CaptchaChallenge)
        .filter(CaptchaChallenge.id == captcha_id)
        .with_for_update()
        .first()
    )

    if challenge is None:
        return False

    if challenge.expires_at < now:
        return False

    if challenge.consumed_at is not None:
        return False

    # 在持有行锁的事务内：先计算摘要和恒定时间比较，再标记并提交消费
    normalized = _normalize_captcha_answer(captcha_code)
    expected = _captcha_answer_digest(captcha_id, normalized)
    answer_ok = hmac.compare_digest(expected, challenge.answer_digest)

    # 无论答案正确与否都提交 consumed_at，确保一次性规则
    challenge.consumed_at = now
    db.commit()

    return answer_ok
