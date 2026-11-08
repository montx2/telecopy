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

_DEFAULTS: Dict[str, Any] = {
    "language": None,          # None = perguntar na primeira execução
    "onboarding_done": False,
    "last_filter": "all",
    "last_source_id": None,
    "last_dest_id": None,
    "check_updates": True,
    "placeholder": True,
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
            if "placeholder" in saved:
                cfg_data["placeholder"] = bool(saved["placeholder"])
            cfg = RunConfig.from_dict(cfg_data)
            merged["config"] = cfg.to_dict()
            merged["placeholder"] = cfg.placeholder
            self.data = merged
        return self.data

    def save(self) -> bool:
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
        return RunConfig.from_dict(data)

    def save_config(self, cfg: RunConfig) -> None:
        self.data["config"] = cfg.to_dict()
        self.data["placeholder"] = cfg.placeholder
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
