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
    "config": RunConfig().to_dict(),
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
            cfg = RunConfig.from_dict({**RunConfig().to_dict(), **(saved.get("config") or {})})
            merged["config"] = cfg.to_dict()
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
        return RunConfig.from_dict(self.data.get("config") or {})

    def save_config(self, cfg: RunConfig) -> None:
        self.data["config"] = cfg.to_dict()
        self.save()

    @property
    def language(self) -> str | None:
        lang = self.data.get("language")
        return lang if lang in ("pt", "en") else None

    def set_language(self, lang: str) -> None:
        self.set("language", lang)

    def reset(self) -> None:
        self.data = dict(_DEFAULTS)
        self.save()


prefs = Preferences()
