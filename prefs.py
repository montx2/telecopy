"""
prefs.py — Preferências do usuário persistidas em JSON.

Ficam em data/prefs.json (texto claro, sem segredos — credenciais vão
para o cofre cifrado).  Qualquer chave desconhecida é preservada.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from config import DATA_DIR, RunConfig
from utils import read_json, write_json

PREFS_FILE = DATA_DIR / "prefs.json"

_PREFS_VERSION = 2

_DEFAULTS: Dict[str, Any] = {
    "prefs_version": _PREFS_VERSION,
    "language": None,          # None = perguntar na primeira execução
    "onboarding_done": False,
    "last_filter": "all",
    "last_source_id": None,
    "last_dest_id": None,
    "check_updates": True,
    "placeholder": False,
    "delivery": "upload",
    "server_copy": False,
    "config": RunConfig().to_dict(),
    # Clone automático
    "clone_suffix": " (Clone)",
    "clone_copy_photo": True,
    "clone_start_copy": True,
}


class Preferences:
    """Leitura/gravação tolerante a falhas das preferências."""

    def __init__(self, path: Path | str = PREFS_FILE) -> None:
        self.path = Path(path)
        self.data: Dict[str, Any] = dict(_DEFAULTS)
        self.load()

    def load(self) -> Dict[str, Any]:
        saved = read_json(self.path, {})
        if isinstance(saved, dict):
            merged = {**_DEFAULTS, **saved}
            saved_config = saved.get("config") or {}
            cfg_data = {**RunConfig().to_dict(), **saved_config}
            for key in ("placeholder", "delivery", "server_copy"):
                if key in saved:
                    cfg_data[key] = saved[key]

            try:
                old_version = int(saved.get("prefs_version", 1))
            except (TypeError, ValueError):
                old_version = 1
            if old_version < _PREFS_VERSION:
                # Valores iguais aos defaults antigos são tratados como herdados.
                # Preferências antigas não registravam se o usuário escolheu
                # explicitamente o próprio default; essa ambiguidade foi aprovada.
                inherited_defaults = {
                    "delivery": ("reference", "upload"),
                    "server_copy": (True, False),
                    "placeholder": (True, False),
                    "on_failure": ("placeholder", "abort"),
                }
                for key, (old_default, new_default) in inherited_defaults.items():
                    if cfg_data.get(key, old_default) == old_default:
                        cfg_data[key] = new_default

            cfg = RunConfig.from_dict(cfg_data)
            merged["prefs_version"] = _PREFS_VERSION
            merged["config"] = cfg.to_dict()
            merged["placeholder"] = cfg.placeholder
            merged["delivery"] = cfg.delivery
            merged["server_copy"] = cfg.server_copy
            self.data = merged
            if saved and old_version < _PREFS_VERSION:
                self.save()
        return self.data

    def save(self) -> bool:
        self.data["prefs_version"] = _PREFS_VERSION
        return write_json(self.path, self.data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self.data[key] = value
        if save:
            self.save()

    # -- conveniências ------------------------------------------------------

    @property
    def config(self) -> RunConfig:
        data = dict(self.data.get("config") or {})
        if "placeholder" in self.data:
            data["placeholder"] = bool(self.data["placeholder"])
        if "delivery" in self.data:
            data["delivery"] = self.data["delivery"]
        if "server_copy" in self.data:
            data["server_copy"] = bool(self.data["server_copy"])
        return RunConfig.from_dict(data)

    def save_config(self, cfg: RunConfig) -> None:
        self.data["config"] = cfg.to_dict()
        self.data["placeholder"] = cfg.placeholder
        self.data["delivery"] = cfg.delivery
        self.data["server_copy"] = cfg.server_copy
        self.save()

    @property
    def language(self) -> str | None:
        lang = self.data.get("language")
        return lang if lang in ("pt", "en") else None

    def set_language(self, lang: str) -> None:
        self.set("language", lang)

    # -- clone automático ---------------------------------------------------

    @property
    def clone_suffix(self) -> str:
        value = self.data.get("clone_suffix")
        return " (Clone)" if value is None else str(value)

    @property
    def clone_copy_photo(self) -> bool:
        return bool(self.data.get("clone_copy_photo", True))

    @property
    def clone_start_copy(self) -> bool:
        return bool(self.data.get("clone_start_copy", True))

    def reset(self) -> None:
        self.data = dict(_DEFAULTS)
        self.save()


prefs = Preferences()
