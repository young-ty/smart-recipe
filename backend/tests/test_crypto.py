# -*- coding: utf-8 -*-
"""账号级模型 Key 的落库加密（设计方案 6.5、7.8）。

要看住四件事：能原样解回来、密文里认不出明文、被改一个字节就解不开、
换一把主密钥也解不开。另外掩码只露头尾。
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.crypto import (  # noqa: E402
    MASTER_KEY_BYTES,
    SecretBox,
    decode_master_key,
    load_or_create_master_key,
    mask_secret,
)

# 占位值：形如 sk-<32 位十六进制> 的字符串会被仓库的密钥扫描当成真 Key 拦下
KEY = "sk-local-demo-key-abcdef"


@pytest.fixture()
def box() -> SecretBox:
    return SecretBox(secrets.token_bytes(MASTER_KEY_BYTES))


def test_round_trip_gives_back_the_original(box: SecretBox):
    token = box.encrypt(KEY)

    assert token.startswith("v1$")
    assert box.decrypt(token) == KEY


def test_ciphertext_never_carries_the_plaintext(box: SecretBox):
    token = box.encrypt(KEY)

    assert KEY not in token
    assert KEY[:8] not in token, "连前缀都不该露出来"


def test_same_plaintext_encrypts_differently_each_time(box: SecretBox):
    """每次都要换随机盐与随机数，否则「两账号存了同一把 Key」一眼就看得出来。"""

    assert box.encrypt(KEY) != box.encrypt(KEY)


@pytest.mark.parametrize("plaintext", ["", "k", "含中文的说明", "a" * 500])
def test_round_trip_handles_edge_lengths(box: SecretBox, plaintext: str):
    assert box.decrypt(box.encrypt(plaintext)) == plaintext


def test_tampered_ciphertext_is_rejected(box: SecretBox):
    token = box.encrypt(KEY)
    version, salt, nonce, cipher, tag = token.split("$")
    flipped = "A" if cipher[0] != "A" else "B"

    broken = "$".join([version, salt, nonce, flipped + cipher[1:], tag])

    assert box.decrypt(broken) is None


def test_another_master_key_cannot_open_it(box: SecretBox):
    token = box.encrypt(KEY)

    assert SecretBox(secrets.token_bytes(MASTER_KEY_BYTES)).decrypt(token) is None


@pytest.mark.parametrize("broken", ["", "明文", "v1$only", "v2$c2FsdA==$c2FsdA==$AA$AA", "$$$$"])
def test_broken_records_read_as_nothing(box: SecretBox, broken: str):
    """格式被改坏时返回 None 而不是抛异常：读不出来就当没配，不该 500。"""

    assert box.decrypt(broken) is None


def test_short_master_key_is_refused():
    with pytest.raises(ValueError):
        SecretBox(b"too-short")


# ------------------------------------------------------------ 主密钥的来源

def test_master_key_is_generated_once_and_reused(tmp_path: Path):
    path = tmp_path / "secret.key"

    first = load_or_create_master_key(path, environ={})
    second = load_or_create_master_key(path, environ={})

    assert len(first) == MASTER_KEY_BYTES
    assert first == second, "重启后必须还是同一把，否则账号里存的 Key 全部作废"
    assert len(path.read_text(encoding="ascii").strip()) == MASTER_KEY_BYTES * 2


def test_environment_variable_wins_over_the_file(tmp_path: Path):
    path = tmp_path / "secret.key"
    load_or_create_master_key(path, environ={})
    configured = "ab" * MASTER_KEY_BYTES

    from_env = load_or_create_master_key(path, environ={"RECIPE_SECRET_KEY": configured})

    assert from_env == bytes.fromhex(configured)
    assert from_env != load_or_create_master_key(path, environ={})


def test_unwritable_location_still_yields_a_key(tmp_path: Path):
    """落不了盘也要让服务起得来，只是这次运行用的是临时密钥。"""

    directory = tmp_path / "as-a-directory"
    directory.mkdir()

    key = load_or_create_master_key(directory, environ={})

    assert len(key) == MASTER_KEY_BYTES


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", None),
        ("太短了", None),
        ("ab" * 16, None),
        ("ab" * 32, bytes.fromhex("ab" * 32)),
    ],
)
def test_decode_master_key_accepts_hex_and_rejects_noise(raw: str, expected):
    assert decode_master_key(raw) == expected


# ------------------------------------------------------------------ 掩码

def test_mask_keeps_only_head_and_tail():
    masked = mask_secret(KEY)

    assert masked == KEY[:4] + "••••" + KEY[-4:]
    assert KEY not in masked


@pytest.mark.parametrize("raw", ["", "   ", "abcd"])
def test_mask_never_reveals_short_or_empty_values(raw: str):
    masked = mask_secret(raw)

    assert raw.strip() not in masked or raw.strip() == ""
