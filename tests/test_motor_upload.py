"""Defaults de entrega: download + upload, sem referência ou placeholder."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import config
import copier as copier_module
import core
from config import RunConfig
from copier import Copier
from database import Repository
from i18n import get_lang, set_lang
from prefs import Preferences
from tests.fake_telegram import FakeClient, channel
from tests.test_ordem import fast_sleep, make_copier, tl_video
from utils import DailyLimit


@pytest.fixture
def env(tmp_path, monkeypatch):
    previous_lang = get_lang()
    set_lang("pt")
    temp = tmp_path / "tmp"
    monkeypatch.setattr(config, "TEMP_DIR", temp)
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.db")
    monkeypatch.setattr(config, "STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr(copier_module, "TEMP_DIR", temp)
    limit = DailyLimit(tmp_path / "daily.json")
    monkeypatch.setattr(copier_module, "daily_limit", limit)
    repo = Repository(tmp_path / "test.db")
    try:
        yield SimpleNamespace(repo=repo, temp=temp, root=tmp_path, limit=limit)
    finally:
        repo.close()
        set_lang(previous_lang)


def test_runconfig_defaults_sao_upload_sem_reserva_e_ordem_estrita() -> None:
    cfg = RunConfig()
    assert cfg.delivery == "upload"
    assert cfg.server_copy is False
    assert cfg.placeholder is False
    assert cfg.on_failure == "abort"
    assert cfg.strict_order is True
    assert cfg.to_dict()["strict_order"] is True
    assert RunConfig(workers=99).workers == 6
    assert RunConfig(strict_order=False).strict_order is True


def test_motor_padrao_baixa_e_envia_sem_referencia_ou_marcador(env) -> None:
    msg = tl_video(1, "vídeo", size=1024)
    client = FakeClient([msg], temp_dir=str(env.temp))
    source, dest = channel(-1001, "Origem"), channel(-2002, "Destino")
    client.register_entity(source)
    client.register_entity(dest)
    cfg = RunConfig(workers=1, delay=0.1, use_dedup=False)
    copier = Copier(client, repo=env.repo, config=cfg, rich_ok=False)

    with fast_sleep():
        result = asyncio.run(copier.run(source, dest))

    assert result["done"] == 1 and result["errors"] == 0
    assert client.downloads == [1]
    assert len(client.sent) == 1 and client.sent[0]["kind"] == "file"
    assert client.sent[0]["path"]
    assert client.reference_attempts == []
    assert result["placeholders"] == 0
    assert env.repo.get_placeholder(source.id, dest.id, 1) is None


def test_settings_mostra_alerta_de_fidelidade_e_quatro_campos(tmp_path, monkeypatch) -> None:
    app = core.App.__new__(core.App)
    app.config = RunConfig()
    app.prefs = Preferences(tmp_path / "prefs.json")
    monkeypatch.setattr(app, "_push", lambda *_: None)
    monkeypatch.setattr(app, "_pop", lambda: None)
    monkeypatch.setattr(app, "_trail_line", lambda: None)
    shown = {}
    warnings = []
    monkeypatch.setattr(core.ui, "clear", lambda: None)
    monkeypatch.setattr(core.ui, "header", lambda *_: None)
    monkeypatch.setattr(core.ui, "box", lambda title, rows, **_: shown.update(rows=rows))
    monkeypatch.setattr(core.ui, "menu", lambda *_: None)
    monkeypatch.setattr(core.ui, "ask", lambda *_args, **_kwargs: "0")
    monkeypatch.setattr(core.ui, "warn", warnings.append)

    asyncio.run(app._menu_settings())

    labels = [row[0] for row in shown["rows"]]
    assert "settings.fidelity.warning" in warnings[0] or "Referência" in warnings[0]
    assert any("posição" in str(label).lower() for label in labels)
    assert any("entrega de mídia" in str(label).lower() for label in labels)
    assert any("servidor" in str(label).lower() for label in labels)
    assert any("política de falha" in str(label).lower() for label in labels)


def test_reference_permanece_disponivel_quando_escolhida(env) -> None:
    msg = tl_video(1, "vídeo", size=1024)
    setup = make_copier(env, [msg], delivery="reference", server_copy=True)
    with fast_sleep():
        result = asyncio.run(setup.copier.run(setup.source, setup.dest))
    assert result["done"] == 1 and result["server_copies"] == 1
    assert setup.client.downloads == []
    assert setup.client.reference_attempts == [1001]
