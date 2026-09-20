from __future__ import annotations
import os
import hmac
import hashlib
import pathlib as pl

def load_or_create_key(path: pl.Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(os.urandom(32))
    return path.read_bytes()

def hmac_hex(key: bytes, msg: bytes) -> str:
    return hmac.new(key, msg, hashlib.sha256).hexdigest()

def verify_hmac(key: bytes, msg: bytes, sig_hex: str) -> bool:
    try:
        sig = bytes.fromhex(sig_hex)
    except Exception:
        return False
    calc = hmac.new(key, msg, hashlib.sha256).digest()
    return hmac.compare_digest(sig, calc)
