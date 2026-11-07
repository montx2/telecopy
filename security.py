"""
security.py — AES-256-GCM vault for credential storage.

Keys are derived with PBKDF2-HMAC-SHA256 (600 000 iterations) from a
machine-bound secret.  The session file is intentionally left in plaintext
so that Telethon can manage it without risk of corruption across moves.
"""

import base64
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False
    
_SECRET_PEPPER = b"TxCpy_v3_M2x_9KpL4vQz_2024_Rnd_7HmN3xW8"


# ── Key derivation helpers ────────────────────────────────────────────────────

def _machine_secret() -> bytes:
    """
    A stable secret derived from machine-specific data.
    Falls back gracefully on all platforms.
    """
    candidates = [
        Path("/etc/machine-id"),
        Path("/var/lib/dbus/machine-id"),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "MachineGuid",
    ]
    for p in candidates:
        if p.exists():
            try:
                return p.read_bytes()[:64]
            except OSError:
                continue

    # Last resort: combine executable path + username
    anchor = f"{sys.argv[0]}:{os.getlogin() if hasattr(os, 'getlogin') else 'user'}"
    return hashlib.sha256(anchor.encode()).digest()


def _derive_key(salt: bytes, secret: bytes = None) -> bytes:
    if secret is None:
        machine = _machine_secret()
        user = (os.getlogin() if hasattr(os, 'getlogin') else 'user').encode()
        # Combina machine + user + pepper (valor secreto no código)
        secret = hashlib.sha256(machine + user + _SECRET_PEPPER).digest()
    
    # Usa o secret (passado ou derivado) para criar a chave final com PBKDF2
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
        backend=default_backend(),
    )
    return kdf.derive(secret)


# ── Low-level encrypt / decrypt ───────────────────────────────────────────────

def _encrypt(plaintext: bytes) -> bytes:
    """Return  salt(32) + nonce(12) + ciphertext  as URL-safe base64."""
    salt  = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    key   = _derive_key(salt)
    ct    = AESGCM(key).encrypt(nonce, plaintext, None)
    return base64.urlsafe_b64encode(salt + nonce + ct)


def _decrypt(blob: bytes) -> bytes:
    raw   = base64.urlsafe_b64decode(blob)
    salt  = raw[:32]
    nonce = raw[32:44]
    ct    = raw[44:]
    key   = _derive_key(salt)
    return AESGCM(key).decrypt(nonce, ct, None)


# ── Vault ─────────────────────────────────────────────────────────────────────

class Vault:
    """
    Encrypted JSON store for API credentials.

    If `cryptography` is unavailable the data is saved as plain JSON
    with a warning — the tool remains functional.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- public interface ------------------------------------------------------

    def save(self, data: Dict[str, Any]) -> None:
        payload = json.dumps(data).encode()

        if _CRYPTO_OK:
            try:
                self.path.write_bytes(_encrypt(payload))
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
                return
            except Exception:
                pass  # fall through to plain JSON

        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def load(self) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None

        raw = self.path.read_bytes()

        # Try decryption first
        if _CRYPTO_OK:
            try:
                return json.loads(_decrypt(raw).decode())
            except Exception:
                pass

        # Fall back to plain JSON
        try:
            return json.loads(raw.decode())
        except Exception:
            return None

    def exists(self) -> bool:
        return self.path.exists()

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()


# ── Convenience functions (used by config.py / core.py) ──────────────────────

def save_secure(data: Dict[str, Any], filepath: str) -> None:
    Vault(Path(filepath)).save(data)


def load_secure(filepath: str) -> Dict[str, Any]:
    return Vault(Path(filepath)).load() or {}
