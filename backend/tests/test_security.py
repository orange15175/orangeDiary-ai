"""密码与令牌的兼容性单测。

这些用例的价值在"M2 差分"时体现：只要有一条不通过，就说明 Python 侧签出的令牌
或校验出的密码与 Java 不一致，切流会直接踢掉存量用户。
"""
from __future__ import annotations

import base64

import bcrypt
import pytest

from app.core.security import (
    create_access_token,
    decode_jwt_secret,
    hash_password,
    parse_access_token,
    verify_password,
)


def test_password_roundtrip():
    stored = hash_password("orange123")
    assert stored.startswith("$2b$")
    assert verify_password("orange123", stored)
    assert not verify_password("orange124", stored)


def test_spring_style_2a_hash_is_accepted():
    # Spring Security 的 BCryptPasswordEncoder 生成 $2a$ 前缀，Python 必须能校验。
    stored = bcrypt.hashpw(b"orange123", bcrypt.gensalt(rounds=4, prefix=b"2a")).decode("ascii")
    assert stored.startswith("$2a$")
    assert verify_password("orange123", stored)


def test_broken_hash_returns_false_instead_of_raising():
    assert not verify_password("orange123", "not-a-bcrypt-hash")
    assert not verify_password("orange123", None)


def test_long_password_is_truncated_like_spring():
    # 73 字节以上 BCrypt 只取前 72 字节；两边都截断才能互相校验通过。
    base = "a" * 72
    stored = hash_password(base + "X")
    assert verify_password(base + "Y", stored)


def test_jwt_secret_accepts_base64_and_base64url():
    raw = b"0123456789abcdef0123456789abcdef"
    standard = base64.b64encode(raw).decode("ascii")
    urlsafe = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    assert decode_jwt_secret(standard) == raw
    assert decode_jwt_secret(urlsafe) == raw


def test_jwt_secret_rejects_empty():
    with pytest.raises(ValueError):
        decode_jwt_secret("")


def test_access_token_roundtrip():
    secret = decode_jwt_secret(base64.b64encode(b"k" * 32).decode("ascii"))
    token = create_access_token(42, "sid-0123456789abcdef", secret, 1800)
    user_id, session_id = parse_access_token(token, secret)
    assert (user_id, session_id) == (42, "sid-0123456789abcdef")


def test_access_token_with_other_key_is_rejected():
    secret = decode_jwt_secret(base64.b64encode(b"k" * 32).decode("ascii"))
    other = decode_jwt_secret(base64.b64encode(b"m" * 32).decode("ascii"))
    token = create_access_token(42, "sid-0123456789abcdef", secret, 1800)
    with pytest.raises(ValueError):
        parse_access_token(token, other)
