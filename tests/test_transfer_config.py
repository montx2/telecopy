"""Etapa D/E: penalidade de flood, cryptg, config, prefs e menu."""

from __future__ import annotations

import asyncio
import builtins
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
import copier as copier_module
import core
from config import RunConfig
from copier import Copier
from database import Repository
from i18n import get_lang, set_lang
from prefs import Preferences
from tests.fake_telegram import FakeClient, album_messages, channel
from utils import DailyLimit


@pytest.fixture
def env(tmp_path, monkeypatch):
    previous = get_lang()
    set_lang("pt")
    monkeypatch.setattr(config, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "test.db")
    try:
        yield SimpleNamespace(repo=repo, root=tmp_path)
    finally:
        repo.close()
        set_lang(previous)


def test_runconfig_valida_e_persiste_campos_novos() -> None:
    cfg = RunConfig(connections_per_file=99, lookahead=-1, max_tmp_gb=-2,
                    max_total_connections=0, fast_transfer=0)
    assert cfg.fast_transfer is False
    assert cfg.connections_per_file == 8
    assert cfg.lookahead == 0
    assert cfg.max_tmp_gb == 8.0
    assert cfg.max_total_connections == 1
    data = cfg.to_dict()
    assert RunConfig.from_dict(data).to_dict() == data
    bad = RunConfig.from_dict({"connections_per_file": "abc", "lookahead": "x",
                               "max_total_connections": None, "max_tmp_gb": "nan"})
    assert bad.connections_per_file == 4
    assert bad.lookahead == 0
    assert bad.max_total_connections == 12
    assert bad.max_tmp_gb == 8.0


def test_prefs_v2_ganham_campos_novos_sem_perder_escolhas(tmp_path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({
        "prefs_version": 2,
        "config": {"delivery": "upload", "workers": 3, "delay": 1.5},
    }), encoding="utf-8")
    loaded = Preferences(path)
    assert loaded.get("prefs_version") == 2
    assert loaded.config.workers == 3 and loaded.config.delay == 1.5
    assert loaded.config.fast_transfer is True
    assert loaded.config.connections_per_file == 4
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["config"]["max_tmp_gb"] == 8.0
    assert saved["config"]["lookahead"] == 0


def test_penalidade_de_flood_decai_e_run_zera(env) -> None:
    client = FakeClient([])
    source, dest = channel(-1, "S"), channel(-2, "D")
    client.register_entity(source)
    client.register_entity(dest)
    copier = Copier(client, repo=env.repo, config=RunConfig(use_dedup=False), rich_ok=False)
    copier._flood_penalty = 2.0
    for _ in range(10):
        copier._note_successful_send()
    assert copier._flood_penalty == 1.5
    for _ in range(30):
        copier._note_successful_send()
    assert copier._flood_penalty == 0.0

    copier._flood_penalty = 9.0
    asyncio.run(copier.run(source, dest))
    assert copier._flood_penalty == 0.0


def test_fast_transfer_false_reproduz_caminho_legado(env, monkeypatch) -> None:
    messages = album_messages(1, 2)
    client = FakeClient(messages)
    source, dest = channel(-1, "S"), channel(-2, "D")
    client.register_entity(source)
    client.register_entity(dest)

    async def forbidden(*args, **kwargs):
        raise AssertionError("caminho rápido não deve ser usado com fast_transfer=False")

    monkeypatch.setattr(copier_module, "fast_download", forbidden)
    monkeypatch.setattr(copier_module, "fast_upload", forbidden)
    copier = Copier(client, repo=env.repo,
                    config=RunConfig(fast_transfer=False, workers=2, use_dedup=False, delay=0.1),
                    rich_ok=False)
    async def no_sleep(_delay):
        return None

    with patch("copier.asyncio.sleep", new=no_sleep):
        result = asyncio.run(copier.run(source, dest))
    assert result["done"] == 2 and result["errors"] == 0
    assert len(client.upload_attempts) == 2
    assert client.upload_media_calls == 2
    assert copier._transfer_pool is None


def test_aviso_cryptg_quando_import_falha(capsys, monkeypatch) -> None:
    original = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "cryptg":
            raise ImportError("sem cryptg")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    Copier._warn_cryptg()
    assert "cryptg" in capsys.readouterr().out


def test_menu_settings_expoe_e_salva_opcoes_de_transferencia(tmp_path, monkeypatch) -> None:
    app = core.App.__new__(core.App)
    app.config = RunConfig()
    app.prefs = Preferences(tmp_path / "prefs.json")
    monkeypatch.setattr(app, "_push", lambda *_: None)
    monkeypatch.setattr(app, "_pop", lambda: None)
    monkeypatch.setattr(app, "_trail_line", lambda: None)
    shown = {}
    answers = iter(["n", "5", "0"])
    monkeypatch.setattr(core.ui, "clear", lambda: None)
    monkeypatch.setattr(core.ui, "header", lambda *_: None)
    monkeypatch.setattr(core.ui, "box", lambda title, rows, **_: shown.update(rows=rows))
    monkeypatch.setattr(core.ui, "menu", lambda *_: None)
    monkeypatch.setattr(core.ui, "ask", lambda *_a, **_k: next(answers))
    monkeypatch.setattr(core.ui, "info", lambda *_: None)
    monkeypatch.setattr(core.ui, "ok", lambda *_: None)
    monkeypatch.setattr(core.ui, "warn", lambda *_: None)
    monkeypatch.setattr(core.ui, "err", lambda *_: None)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(core.asyncio, "sleep", no_sleep)
    asyncio.run(app._menu_settings())

    labels = [row[0] for row in shown["rows"]]
    assert any("Transferência rápida" in label for label in labels)
    assert any("Conexões por arquivo" in label for label in labels)
    assert any("Janela" in label for label in labels)
    assert any("temporários" in label for label in labels)
    assert app.config.connections_per_file == 5
    assert Preferences(tmp_path / "prefs.json").config.connections_per_file == 5
