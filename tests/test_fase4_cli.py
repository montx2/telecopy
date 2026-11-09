from __future__ import annotations

import asyncio
import json
import signal
from types import SimpleNamespace

import pytest

import copier as copier_module
import telecopy_cli
from config import RunConfig
from copier import Copier
from database import Repository, Status
from prefs import Preferences
from relatorio import escrever_relatorio, montar_relatorio
from tests.fake_telegram import FakeClient, channel, text_message
from utils import DailyLimit
from verificacao import verificar_par


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "cli.db")
    try:
        yield SimpleNamespace(root=tmp_path, repo=repo)
    finally:
        repo.close()


def test_verify_repara_apenas_correspondencia_unica(env) -> None:
    source, dest = channel(-1, "S"), channel(-2, "D")
    client = FakeClient([text_message(1, "única")])
    client.register_entity(source), client.register_entity(dest)
    msg = asyncio.run(client.send_message(dest, "única"))
    env.repo.upsert_checkpoint(source.id, dest.id, 1, Status.DONE)
    copier = Copier(client, repo=env.repo, config=RunConfig(), rich_ok=False)
    fp = copier._intent_fingerprint(kind="text", text="única", entities=None, dest_topic=None, part_index=0)
    env.repo.begin_intent(source.id, dest.id, 1, 0, fingerprint=fp, kind="text")
    result = asyncio.run(verificar_par(client, env.repo, source, dest, reparar=True))
    assert result.ok == 1 and result.reparados == [1]
    assert env.repo.checkpoint_route(source.id, dest.id, 1)["dest_msg_id"] == msg.id


def test_rehearsal_limita_mensagens_reais_do_fake(env) -> None:
    messages = [text_message(i, f"m{i}") for i in range(1, 6)]
    client = FakeClient(messages)
    source, dest = channel(-1, "S"), channel(-2, "D")
    client.register_entity(source), client.register_entity(dest)
    copier = Copier(client, repo=env.repo, config=RunConfig(delay=0, use_dedup=False), rich_ok=False)
    copier.max_messages = 2
    result = asyncio.run(copier.run(source, dest))
    assert result["done"] == 2
    assert [r["text"] for r in client.sent] == ["m1", "m2"]


def test_cli_json_exit_codes_e_relatorio(env, tmp_path) -> None:
    messages = [text_message(1, "ok")]
    client = FakeClient(messages)
    source, dest = channel(-1, "S"), channel(-2, "D")
    client.register_entity(source), client.register_entity(dest)
    args = telecopy_cli.build_parser().parse_args([
        "clone", "--src", "-1", "--dst", "-2", "--perfil", "recomendado", "--retomar", "--json",
        "--report-dir", str(tmp_path / "reports"),
    ])
    code = asyncio.run(telecopy_cli.executar(args, client, env.repo))
    assert code == 0
    reports = list((tmp_path / "reports").glob("relatorio-*.json"))
    assert reports and json.loads(reports[0].read_text(encoding="utf-8"))["resultado"]["done"] == 1
    assert list((tmp_path / "reports").glob("relatorio-*.html"))


@pytest.mark.skipif(not hasattr(signal, "raise_signal"), reason="raise_signal indisponível nesta plataforma")
def test_sinal_interrompe_sem_deixar_tarefa_pendente(env) -> None:
    client = FakeClient([])
    copier = Copier(client, repo=env.repo, config=RunConfig(), rich_ok=False)

    async def lento():
        await asyncio.sleep(30)
        return {"complete": True}

    async def cenario():
        task = asyncio.ensure_future(telecopy_cli._com_sinais(lento(), copier))
        await asyncio.sleep(0)
        signal.raise_signal(signal.SIGTERM)
        return await task

    result = asyncio.run(cenario())
    assert result["interrupted"] is True


def test_prefs_corrompidas_e_futuras_preservam_backup(tmp_path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text("{corrupt", encoding="utf-8")
    prefs = Preferences(path)
    assert prefs.config.resume is True
    assert list(tmp_path.glob("prefs.json.corrupt-*"))
    path.write_text(json.dumps({"prefs_version": 999, "config": {"workers": 99}}), encoding="utf-8")
    prefs = Preferences(path)
    assert prefs.config.workers <= 16
    assert list(tmp_path.glob("prefs.json.future-*"))


def test_relatorio_html_escapa_texto(tmp_path) -> None:
    rel = montar_relatorio({"first_error": "<script>x</script>", "done": 0})
    paths = escrever_relatorio(rel, tmp_path)
    html = paths["html"].read_text(encoding="utf-8")
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


def test_relatorio_html_inclui_listas_aninhadas(tmp_path) -> None:
    from relatorio import escrever_relatorio, montar_relatorio

    rel = montar_relatorio(
        {"done": 2, "topicos": {"1": 2}},
        verificacao={"ok": 1, "ausentes": [7, 8], "detalhe": {"x": "<b>"}},
    )
    paths = escrever_relatorio(rel, tmp_path)
    body = paths["html"].read_text(encoding="utf-8")
    assert "ausentes" in body and "[7, 8]" in body
    assert "topicos" in body and "&lt;b&gt;" in body
    assert "<b>" not in body
