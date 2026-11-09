"""doctor --json deve emitir JSON puro e estruturado, não texto humano."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import doctor


def _run_json(monkeypatch) -> dict:
    # Evita rede real no diagnóstico.
    import socket

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _Conn())
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = doctor.run_doctor(json_mode=True)
    out = buf.getvalue()
    data = json.loads(out)  # não pode levantar: stdout tem de ser JSON puro
    assert isinstance(data, dict)
    assert "ok" in data and "checks" in data
    assert isinstance(data["checks"], list) and data["checks"]
    assert all({"check", "status"} <= set(c) for c in data["checks"])
    assert code in (0, 1)
    assert data["ok"] == (code == 0)
    return data


def test_doctor_json_puro(monkeypatch) -> None:
    data = _run_json(monkeypatch)
    labels = {c["check"] for c in data["checks"]}
    assert "Python" in labels
    assert any(c["check"] == "Telegram (rede)" for c in data["checks"])


def test_doctor_json_reporta_falha(monkeypatch) -> None:
    import socket

    def sem_rede(*a, **k):
        raise OSError("sem rede")

    monkeypatch.setattr(socket, "create_connection", sem_rede)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = doctor.run_doctor(json_mode=True)
    data = json.loads(buf.getvalue())
    assert code == 1 and data["ok"] is False
    assert data["problems"]
    assert any(c["status"] == "fail" for c in data["checks"])


def test_cli_doctor_json_via_main(monkeypatch) -> None:
    """Regressão do bug real: main() chamava run_doctor() sem json_mode."""
    import socket

    import telecopy_cli

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _Conn())
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = telecopy_cli.main(["doctor", "--json"])
    data = json.loads(buf.getvalue())
    assert data["ok"] == (code == 0)
    assert len(data["checks"]) >= 5


def test_doctor_texto_padrao_inalterado(monkeypatch) -> None:
    import socket

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _Conn())
    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor.run_doctor()
    out = buf.getvalue()
    assert "TELECOPY — diagnóstico" in out
    assert "Python" in out
