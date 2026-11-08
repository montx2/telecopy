"""Verificador de ordem offline ([5] → 7) sem rede."""

import core
from database import Repository, Status


def _silence_ui(monkeypatch):
    for name in ("clear", "header", "pause", "warn", "ok", "info", "err"):
        monkeypatch.setattr(core.ui, name, lambda *a, **k: None)


def test_tool_order_reporta_violacao(tmp_path, monkeypatch) -> None:
    repo = Repository(tmp_path / "r.db")
    monkeypatch.setattr(core, "db", repo)
    repo.upsert_checkpoint(1, 2, 1, Status.ERROR)
    repo.upsert_checkpoint(1, 2, 2, Status.DONE, dest_msg_id=10)
    captured = {}
    _silence_ui(monkeypatch)
    monkeypatch.setattr(core.ui, "box",
                        lambda title, rows, color=None: captured.update(rows=rows))
    app = core.App.__new__(core.App)
    app._tool_order()
    assert captured["rows"][0][1] == "1"
    assert captured["rows"][1][1] == "1"


def test_tool_order_sem_historico(tmp_path, monkeypatch) -> None:
    repo = Repository(tmp_path / "r.db")
    monkeypatch.setattr(core, "db", repo)
    messages = []
    _silence_ui(monkeypatch)
    monkeypatch.setattr(core.ui, "info", lambda msg, *a, **k: messages.append(msg))
    app = core.App.__new__(core.App)
    app._tool_order()
    assert messages and "Nenhum par" in messages[0]
