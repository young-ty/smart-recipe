"""账号级模型 Key 的落库加密（设计方案 6.5、7.8）。

先用 HKDF-SHA256 把主密钥派生成加密密钥与认证密钥，再把明文与 HMAC-SHA256 生成的
密钥流异或，最后对「版本 + 盐 + 随机数 + 密文」做一次认证（先加密后认证）。
只用标准库：要保管的是一个第三方 API Key，不是长期归档的数据，
按 PRF 当流密码用、再配一个认证标签，就足以挡住「数据库文件被读走」这类情况；
换 AES-GCM 还得引入密码学库，这个项目不必为此多一个依赖。

主密钥优先取环境变量 `RECIPE_SECRET_KEY`，其次读密钥文件，都没有就生成一份落盘（0600）。
换了主密钥，已经存下的 Key 就解不开了：换机器部署时要把它一起带过去。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VERSION = "v1"
SALT_BYTES = 16
NONCE_BYTES = 16
MASTER_KEY_BYTES = 32
DERIVED_KEY_BYTES = 64
MASTER_KEY_ENV = "RECIPE_SECRET_KEY"

_ENCRYPTION_INFO = b"recipe-llm-key"
_MASK_HEAD = 4
_MASK_TAIL = 4


def mask_secret(secret: str) -> str:
    """界面上只露头尾：截图与录屏里带不出一个完整的 Key。"""

    value = (secret or "").strip()
    if not value:
        return ""
    if len(value) <= _MASK_HEAD + _MASK_TAIL:
        return "•" * len(value)
    return f"{value[:_MASK_HEAD]}••••{value[-_MASK_TAIL:]}"


def _hkdf_expand(master: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 的 Expand 段。主密钥本身是随机数，Extract 那一步没有存在的必要。"""

    prk = hmac.new(salt, master, hashlib.sha256).digest()
    blocks: list = []
    previous = b""
    counter = 1
    while sum(len(block) for block in blocks) < length:
        previous = hmac.new(prk, previous + info + bytes([counter]), hashlib.sha256).digest()
        blocks.append(previous)
        counter += 1
    return b"".join(blocks)[:length]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """按计数器拼出与明文等长的密钥流。"""

    blocks: list = []
    counter = 0
    while sum(len(block) for block in blocks) < length:
        blocks.append(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return b"".join(blocks)[:length]


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SecretBox:
    """一把主密钥的加解密封装。密文形如 `v1$盐$随机数$密文$认证标签`。"""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) < MASTER_KEY_BYTES:
            raise ValueError("主密钥至少 %d 字节" % MASTER_KEY_BYTES)
        self._master = master_key

    def _keys(self, salt: bytes) -> tuple:
        material = _hkdf_expand(self._master, salt, _ENCRYPTION_INFO, DERIVED_KEY_BYTES)
        return material[:32], material[32:]

    def encrypt(self, plaintext: str) -> str:
        salt = secrets.token_bytes(SALT_BYTES)
        nonce = secrets.token_bytes(NONCE_BYTES)
        encryption_key, mac_key = self._keys(salt)
        payload = plaintext.encode("utf-8")
        cipher = _xor(payload, _keystream(encryption_key, nonce, len(payload)))
        header = VERSION.encode("ascii") + salt + nonce
        tag = hmac.new(mac_key, header + cipher, hashlib.sha256).digest()
        return "$".join(
            [VERSION, _b64encode(salt), _b64encode(nonce), _b64encode(cipher), _b64encode(tag)]
        )

    def decrypt(self, token: str) -> Optional[str]:
        """解不开就返回 None：格式不对、认证不过、不是这把主密钥，都算「读不出来」。"""

        try:
            version, salt_text, nonce_text, cipher_text, tag_text = (token or "").split("$")
            if version != VERSION:
                return None
            salt = _b64decode(salt_text)
            nonce = _b64decode(nonce_text)
            cipher = _b64decode(cipher_text)
            tag = _b64decode(tag_text)
        except (ValueError, TypeError, AttributeError):
            return None
        if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES:
            return None
        encryption_key, mac_key = self._keys(salt)
        expected = hmac.new(
            mac_key, version.encode("ascii") + salt + nonce + cipher, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, tag):
            return None
        return _xor(cipher, _keystream(encryption_key, nonce, len(cipher))).decode(
            "utf-8", "replace"
        )


def decode_master_key(raw: str) -> Optional[bytes]:
    """主密钥支持十六进制与 base64 两种写法；短于 32 字节的一律不认。"""

    text = (raw or "").strip()
    if not text:
        return None
    for decoder in (bytes.fromhex, _b64decode):
        try:
            candidate = decoder(text)
        except (ValueError, TypeError):
            continue
        if len(candidate) >= MASTER_KEY_BYTES:
            return candidate
    return None


def load_or_create_master_key(path: Path, *, environ: Optional[dict] = None) -> bytes:
    """取主密钥：环境变量优先，其次密钥文件，都没有就生成一份。"""

    env = os.environ if environ is None else environ
    from_env = decode_master_key(env.get(MASTER_KEY_ENV, ""))
    if from_env is not None:
        return from_env

    key_file = Path(path)
    try:
        if key_file.is_file():
            existing = decode_master_key(key_file.read_text(encoding="ascii"))
            if existing is not None:
                return existing
            logger.warning("密钥文件读不出可用内容，重新生成：%s", key_file)
        generated = secrets.token_bytes(MASTER_KEY_BYTES)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # 并发启动时另一个进程刚写好，读它的结果
            return decode_master_key(key_file.read_text(encoding="ascii")) or generated
        with os.fdopen(handle, "w", encoding="ascii") as stream:
            stream.write(generated.hex())
        logger.info("已生成账号级模型 Key 的主密钥：%s", key_file)
        return generated
    except OSError as exc:
        # 落不了盘也得让服务起得来：这次运行用一份临时密钥，重启后账号里的 Key 得重填
        logger.error("主密钥无法落盘（%s），本次运行改用临时密钥", exc)
        return secrets.token_bytes(MASTER_KEY_BYTES)
