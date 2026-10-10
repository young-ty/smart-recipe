"""口令与会话令牌的哈希（设计方案 6.5）。

只用标准库，不引入 passlib / bcrypt：PBKDF2-HMAC-SHA256 + 每账户独立随机盐，
字符串格式 `pbkdf2_sha256$<迭代次数>$<盐>$<摘要>`。迭代次数写进字符串里，
以后调高参数时老账户照样验证得通，不需要重新落库。

会话令牌是 `secrets.token_urlsafe(32)`（256 位熵），库里只存 sha256 摘要：
数据库被读走也换不出可用的会话令牌。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional

PBKDF2_ALGORITHM = "pbkdf2_sha256"
# OWASP 对 PBKDF2-HMAC-SHA256 的建议下限。单次登录约 60 ms，正常登录感觉不到，
# 爆破成本却抬高了三个数量级。
PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
TOKEN_BYTES = 32


def _encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(
    password: str,
    *,
    iterations: int = PBKDF2_ITERATIONS,
    salt: Optional[bytes] = None,
) -> str:
    """生成口令哈希。`salt` 只为测试可复现而留，正常调用一律随机。"""

    actual_salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, iterations)
    return f"{PBKDF2_ALGORITHM}${iterations}${_encode(actual_salt)}${_encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """校验口令。格式不对、算法不认识、摘要为空一律当失败，不抛异常。"""

    try:
        algorithm, iterations_text, salt_text, digest_text = (stored or "").split("$")
        iterations = int(iterations_text)
        salt = _decode(salt_text)
        expected = _decode(digest_text)
    except (ValueError, TypeError, AttributeError):
        return False
    if algorithm != PBKDF2_ALGORITHM or iterations <= 0 or not expected:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
