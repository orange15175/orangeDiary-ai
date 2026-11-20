"""密码哈希与访问令牌。

迁移兼容是这里的第一目标，不是"重新设计一套更好看的认证"：

- 密码沿用 BCrypt，Spring 生成的 `$2a$` 哈希必须能在 Python 里直接校验，
  否则切流时所有存量用户都要重置密码。
- JWT 沿用 HS256，claims 保持 `sub/jti/sid/typ`；`JWT_SECRET` 在 Java 里是按
  Base64 解码后当密钥用的，直接把原字符串塞给 PyJWT 会签出两边不互认的令牌。
"""
from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

# BCrypt 只处理前 72 字节，超出部分会被静默丢弃。Spring 同样按字节截断，
# 这里显式截断是为了避免 bcrypt 4.x 直接抛 ValueError（版本行为不一致）。
_BCRYPT_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_truncate(plain), bcrypt.gensalt(rounds=10)).decode("ascii")


def verify_password(plain: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        return bcrypt.checkpw(_truncate(plain), stored_hash.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        # 库里存了非 BCrypt 格式（例如历史明文或损坏数据）时按校验失败处理，
        # 不能让异常冒到接口层变成 500。
        return False


def _truncate(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def decode_jwt_secret(raw: str) -> bytes:
    """复刻 jjwt 的 `Decoders.BASE64` → `Decoders.BASE64URL` 回退顺序。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError("JWT_SECRET 未配置")
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        pass
    try:
        # validate 参数 urlsafe_b64decode 不支持，这里靠 jjwt 同样的宽松语义兜底；
        # 前面的严格校验已经拦掉了包含非法字符的输入。
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("JWT_SECRET 既不是合法 Base64 也不是 Base64URL") from exc


def create_access_token(
    user_id: int,
    session_id: str,
    secret: bytes,
    expires_in_seconds: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "jti": str(uuid.uuid4()),
        "sid": session_id,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in_seconds),
    }
    # 不指定 headers：PyJWT 与 jjwt 默认的 typ=JWT 一致，不必额外设置。
    return jwt.encode(payload, secret, algorithm="HS256")


def parse_access_token(token: str, secret: bytes) -> tuple[int, str]:
    """返回 (userId, sessionId)；任何异常都收敛成 ValueError 由调用方转 401。"""
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as exc:
        raise ValueError("访问令牌无效") from exc

    if claims.get("typ") != "access":
        raise ValueError("令牌类型不是 access")
    session_id = claims.get("sid")
    if not session_id or not claims.get("jti"):
        raise ValueError("访问令牌缺少必要声明")
    try:
        return int(claims["sub"]), str(session_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("访问令牌的 sub 不是用户 ID") from exc
