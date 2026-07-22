"""WCDB / SQLCipher-4 decryption backend for WeChat 4.1.x (read-only).

Corrected model (see plan Task 4/5 amendment): the captured secret is a 32-byte
*passphrase*; each database's key is derived per-file:
    enc_key = PBKDF2-HMAC-SHA512(passphrase, db_salt, 256000, 32)
SQLCipher-4 params: AES-256-CBC, page 4096, HMAC-SHA512, reserve 80 (IV16+HMAC64),
mac_salt = salt XOR 0x3a, mac_key = PBKDF2-HMAC-SHA512(enc_key, mac_salt, 2, 32).

A backend decrypts a snapshot into a standard plaintext SQLite file the reader
opens with the stdlib. No sqlcipher binary / subprocess needed.
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from pathlib import Path
from typing import Protocol

PAGE = 4096
ITER = 256_000
FAST_KDF_ITER = 2
RESERVE = 80
IV_OFF = PAGE - RESERVE          # 4016
HMAC_OFF = IV_OFF + 16           # 4032
HMAC_LEN = 64
SQLITE_HDR = b"SQLite format 3\x00"


class CipherError(RuntimeError):
    pass


def _aes_cbc_decryptor(key: bytes, iv: bytes):
    """Lazily resolve an AES-256-CBC decryptor. Validation needs only hashlib;
    only full-page decryption needs a cipher library (cryptography / pycryptodome)."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        return Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    except ModuleNotFoundError:
        try:
            from Crypto.Cipher import AES  # pyright: ignore[reportMissingImports]
            cipher = AES.new(key, AES.MODE_CBC, iv)
            class _Wrap:
                def update(self, data): return cipher.decrypt(data)
                def finalize(self): return b""
            return _Wrap()
        except ModuleNotFoundError as exc:
            raise CipherError(
                "decryption needs 'cryptography' (or 'pycryptodome') installed"
            ) from exc


def enc_key_from_passphrase(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, ITER, 32)


def _mac_key(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3a for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, FAST_KDF_ITER, 32)


def validates(snapshot: Path, passphrase: bytes) -> bool:
    """True iff the passphrase decrypts page 1 (HMAC-SHA512 check)."""
    with open(snapshot, "rb") as handle:
        page1 = handle.read(PAGE)
    if len(page1) < PAGE:
        return False
    salt = page1[:16]
    enc_key = enc_key_from_passphrase(passphrase, salt)
    mac_key = _mac_key(enc_key, salt)
    calc = hmac.new(mac_key, page1[16:HMAC_OFF] + struct.pack("<I", 1), hashlib.sha512).digest()
    return hmac.compare_digest(calc, page1[HMAC_OFF:HMAC_OFF + HMAC_LEN])


class CipherBackend(Protocol):
    def decrypt(self, snapshot: Path, passphrase: bytes, dest: Path) -> Path: ...
    def probe(self, snapshot: Path, passphrase: bytes) -> bool: ...


class WcdbCipherBackend:
    def probe(self, snapshot: Path, passphrase: bytes) -> bool:
        return validates(snapshot, passphrase)

    def decrypt(self, snapshot: Path, passphrase: bytes, dest: Path) -> Path:
        data = Path(snapshot).read_bytes()
        if len(data) < PAGE:
            raise CipherError("file too small to be a SQLCipher database")
        salt = data[:16]
        enc_key = enc_key_from_passphrase(passphrase, salt)
        if not validates(snapshot, passphrase):
            raise CipherError("passphrase did not validate page 1")
        out = bytearray()
        npages = len(data) // PAGE
        for i in range(npages):
            page = data[i * PAGE:(i + 1) * PAGE]
            off = 16 if i == 0 else 0
            ct = page[off:IV_OFF]
            iv = page[IV_OFF:IV_OFF + 16]
            if len(ct) % 16:
                ct = ct[:len(ct) - (len(ct) % 16)]
            dec = _aes_cbc_decryptor(enc_key, iv)
            pt = dec.update(ct) + dec.finalize()
            out += (SQLITE_HDR + pt if i == 0 else pt) + b"\x00" * RESERVE
        Path(dest).write_bytes(out)
        return Path(dest)
