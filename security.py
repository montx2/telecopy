"""
security.py — Cofre de credenciais com AES-256-GCM.

Formato do arquivo:
    magic(8) | salt(32) | nonce(12) | ciphertext+tag

A chave é derivada com PBKDF2-HMAC-SHA256 (ou scrypt, se disponível) a
partir de um segredo local persistente (data/vault/local.key, permissão 600),
o que torna o cofre portável: ao mover a pasta para outro computador as
credenciais continuam legíveis.

Compatibilidade: cofres antigos (derivados do hardware) não podem ser
recuperados em outra máquina — o usuário apenas refaz o login uma vez.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:  # criptografia forte (obrigatória)
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    _CRYPTO_OK = True
except ImportError:  # pragma: no cover - dependência declarada em requirements
    _CRYPTO_OK = False

MAGIC = b"TCOPY3\x00\x01"
_KDF_ITERATIONS = 260_000
_LOCAL_KEY_NAME = "local.key"


class VaultError(Exception):
    """Falha ao ler/gravar o cofre."""


# ── Segredo local (portável) ──────────────────────────────────────────────────


def _local_key_path(vault_dir: Path) -> Path:
    return vault_dir / _LOCAL_KEY_NAME


def _load_or_create_local_key(vault_dir: Path) -> bytes:
    """Chave aleatória de 32 bytes; falha de disco é erro, nunca chave previsível."""
    path = _local_key_path(vault_dir)
    try:
        if path.exists():
            data = path.read_bytes()
            if len(data) >= 32:
                return data[:32]
        vault_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        # Criação exclusiva evita corrida entre processos na primeira execução.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return key
    except FileExistsError:
        data = path.read_bytes()
        if len(data) >= 32:
            return data[:32]
        raise VaultError("Chave local inválida; restaure o arquivo ou use TELECOPY_VAULT_KEY")
    except OSError as exc:
        raise VaultError(
            "Não foi possível criar/ler a chave local do cofre. "
            "Defina TELECOPY_VAULT_KEY ou corrija as permissões do diretório."
        ) from exc


def _legacy_hardware_secret() -> bytes:
    """Derivação usada pela versão 3.0 (mantida só para leitura)."""
    candidates = [
        Path("/etc/machine-id"),
        Path("/var/lib/dbus/machine-id"),
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "MachineGuid",
    ]
    for path in candidates:
        if path.exists():
            try:
                return path.read_bytes()[:64]
            except OSError:
                continue
    anchor = f"{sys.argv[0]}:{os.environ.get('USERNAME', 'user')}"
    return hashlib.sha256(anchor.encode()).digest()


def _derive_key(salt: bytes, secret: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    )
    return kdf.derive(secret)


# ── Cofre ─────────────────────────────────────────────────────────────────────


class Vault:
    """Armazena um dicionário JSON cifrado no disco."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- helpers ------------------------------------------------------------

    def _secrets(self) -> list[bytes]:
        """Segredos candidatos, do mais moderno ao legado."""
        primary = _load_or_create_local_key(self.path.parent)
        candidates = [
            hashlib.sha256(primary).digest(),
            hashlib.sha256(primary + b"TELECOPY").digest(),
        ]
        legacy = _legacy_hardware_secret()
        candidates.append(hashlib.sha256(legacy).digest())
        candidates.append(
            hashlib.sha256(
                legacy + b"TxCpy_v3_M2x_9KpL4vQz_2024_Rnd_7HmN3xW8"
            ).digest()
        )
        return candidates

    # -- API ----------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def save(self, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if not _CRYPTO_OK:  # pragma: no cover
            raise VaultError(
                "A biblioteca 'cryptography' não está instalada — "
                "não é possível gravar credenciais com segurança."
            )
        salt = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        key = _derive_key(salt, self._secrets()[0])
        blob = MAGIC + salt + nonce + AESGCM(key).encrypt(nonce, payload, None)
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_bytes(blob)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError as exc:
            raise VaultError(f"Não foi possível gravar o cofre: {exc}") from exc

    def load(self) -> Optional[Dict[str, Any]]:
        """Retorna o dicionário salvo ou None se não existir/ilegível."""
        if not self.path.exists():
            return None
        try:
            raw = self.path.read_bytes()
        except OSError:
            return None

        if raw.startswith(MAGIC):
            body = raw[len(MAGIC):]
            if len(body) < 44:
                return None
            salt, nonce, ciphertext = body[:32], body[32:44], body[44:]
            for secret in self._secrets():
                try:
                    key = _derive_key(salt, secret)
                    plain = AESGCM(key).decrypt(nonce, ciphertext, None)
                    return json.loads(plain.decode("utf-8"))
                except Exception:
                    continue
            return None

        # Formato 3.0 (salt b64 + nonce + ct, chave presa ao hardware)
        try:
            decoded = base64.urlsafe_b64decode(raw)
            salt, nonce, ciphertext = decoded[:32], decoded[32:44], decoded[44:]
            for secret in self._secrets():
                try:
                    key = _derive_key(salt, secret)
                    plain = AESGCM(key).decrypt(nonce, ciphertext, None)
                    return json.loads(plain.decode("utf-8"))
                except Exception:
                    continue
        except Exception:
            pass

        # JSON em texto puro (instalações muito antigas)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def delete(self) -> None:
        for target in (self.path, self.path.with_suffix(".tmp")):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass

    # -- conveniências ------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        data = self.load() or {}
        return data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        data = self.load() or {}
        data[key] = value
        self.save(data)

    def update(self, **kwargs: Any) -> Dict[str, Any]:
        data = self.load() or {}
        data.update(kwargs)
        self.save(data)
        return data

    def is_readable(self) -> bool:
        """True se o cofre existe e pode ser aberto nesta máquina."""
        if not self.path.exists():
            return False
        return self.load() is not None


def save_secure(data: Dict[str, Any], filepath: str | Path) -> None:
    Vault(filepath).save(data)


def load_secure(filepath: str | Path) -> Dict[str, Any]:
    return Vault(filepath).load() or {}
