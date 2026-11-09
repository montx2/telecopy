"""Flood global, AIMD, jitter e fallback — rede simulada, relógio determinístico."""
from __future__ import annotations

import hashlib
import json
import random

from telethon.errors import FloodPremiumWaitError, FloodWaitError

import copier as copier_module
from config import RunConfig
from copier import Copier
from database import Repository
from fasttransfer import PART_SIZE, FastTransferPool, fast_download
from tests.fake_telegram import FakeClient, channel
from tests.relogio_simulado import relogio_simulado
from tests.test_ordem import tl_video


def dados(tamanho: int) -> bytes:
    bloco = bytes(range(256))
    return (bloco * (tamanho // 256 + 1))[:tamanho]


def floods(segundos: int = 10, sempre: bool = False):
    def fabricar(nome, chave, tentativa, request):
        if sempre or tentativa == 1:
            return FloodPremiumWaitError(request=None, capture=segundos)
        return None
    return fabricar


def test_32_partes_premium_compartilham_uma_espera_e_recuperam(tmp_path) -> None:
    tamanho = 32 * PART_SIZE
    conteudo = dados(tamanho)
    msg = tl_video(1, size=tamanho)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = conteudo
    client.part_error_factory = floods()
    avisos = []
    pool = FastTransferPool(client, 32, 32, rng=random.Random(1234))
    destino = tmp_path / "curso.bin"

    async def on_flood(exc):
        avisos.append(exc)

    async def cenario(loop):
        client.clock = loop.time
        await fast_download(pool, msg, destino, on_flood=on_flood)
        assert pool.capacity == 16
        assert pool.flood_parts == 32
        for _ in range(48):
            pool.note_success()
        assert pool.capacity == 17
        await pool.close()

    with relogio_simulado() as loop:
        inicio = loop.time()
        loop.run_until_complete(cenario(loop))
        decorrido = loop.time() - inicio
    assert 10 <= decorrido < 14, "uma espera compartilhada, não uma por parte"
    assert len(avisos) == 1
    assert pool.halvings == 1 and pool.recoveries == 1
    assert hashlib.sha256(destino.read_bytes()).digest() == hashlib.sha256(conteudo).digest()
    retomadas = [t for t, _, _, tentativa in client.part_attempt_times if tentativa == 2]
    assert len(retomadas) == 32
    assert all(t >= inicio + 10 for t in retomadas)
    assert len(set(retomadas)) > 1, "jitter precisa escalonar a volta"
    assert client.active_parts == 0 and pool._leased == 0
    assert all(s.disconnected for s in client.created_senders)


def test_premium_reduz_capacidade_e_nao_repete_callback_por_parte(tmp_path) -> None:
    """Reprodução comportamental usando apenas a API que já existia."""
    tamanho = 4 * PART_SIZE
    msg = tl_video(1, size=tamanho)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = dados(tamanho)
    client.part_error_factory = floods()
    pool = FastTransferPool(client, 4, 4)
    avisos = []

    async def on_flood(exc):
        avisos.append(exc)

    async def cenario():
        await fast_download(pool, msg, tmp_path / "premium.bin", on_flood=on_flood)
        await pool.close()

    with relogio_simulado() as loop:
        loop.run_until_complete(cenario())
    assert len(avisos) == 1
    assert pool.capacity == 2


def test_um_episodio_nao_penaliza_duas_vezes(tmp_path) -> None:
    pool = FastTransferPool(FakeClient([]), 32, 32, controller_path=tmp_path / "aimd.json",
                            account_id="conta-de-teste")
    erro = FloodPremiumWaitError(request=None, capture=10)
    async def registrar():
        return pool.note_flood(erro, dc_id=2), pool.note_flood(erro, dc_id=2)

    with relogio_simulado() as loop:
        primeiro, segundo = loop.run_until_complete(registrar())
    assert primeiro.first is True and segundo.first is False
    assert primeiro.episode_id == segundo.episode_id
    assert pool.capacity == 16 and pool.halvings == 1
    assert pool.flood_parts == 1


async def _registrar_flood(pool) -> None:
    pool.note_flood(FloodWaitError(request=None, capture=2), dc_id=2)


def test_persistencia_por_conta_dc_nao_expoe_identificador(tmp_path) -> None:
    caminho = tmp_path / "aimd.json"
    pool = FastTransferPool(FakeClient([]), 8, 2, controller_path=caminho,
                            account_id="+5511999999999")
    with relogio_simulado() as loop:
        loop.run_until_complete(_registrar_flood(pool))
    texto = caminho.read_text(encoding="utf-8")
    assert "5511999999999" not in texto
    assert json.loads(texto)["version"] == 1
    outro = FastTransferPool(FakeClient([]), 8, 2, controller_path=caminho,
                             account_id="+5511999999999")
    assert outro.capacity == 4
    independente = FastTransferPool(FakeClient([]), 8, 2, controller_path=caminho,
                                    account_id="outra-conta")
    assert independente.capacity == 8


def test_orcamento_esgotado_cai_para_padroes_sem_burlar_cooldown(tmp_path) -> None:
    conteudo = dados(4096)
    msg = tl_video(1, size=4096)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = conteudo
    client.part_error_factory = floods(segundos=30, sempre=True)
    pool = FastTransferPool(client, 4, 4, block_wait_budget=5)
    copier = Copier(client, config=RunConfig(), rich_ok=False)
    copier._transfer_pool = pool
    inicio_fallback = []

    async def download_file(location, filename, **kwargs):
        inicio_fallback.append(client.clock())
        with open(filename, "wb") as handle:
            handle.write(conteudo)
        return filename

    client.download_file = download_file
    destino = tmp_path / "fallback.bin"
    with relogio_simulado() as loop:
        client.clock = loop.time
        inicio = loop.time()
        resultado = loop.run_until_complete(copier._download_media(msg, str(destino), lambda c, t: None))
        loop.run_until_complete(pool.close())
        assert loop.time() - inicio >= 30
    assert resultado == str(destino) and destino.read_bytes() == conteudo
    assert inicio_fallback and inicio_fallback[0] >= inicio + 30


def test_flood_na_exportacao_de_autorizacao_nao_vira_referencia_expirada(tmp_path) -> None:
    conteudo = dados(4096)
    msg = tl_video(1, size=4096)
    msg.media.document.dc_id = 2
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = conteudo
    client.export_errors[2] = [FloodWaitError(request=None, capture=7)]
    pool = FastTransferPool(client, 8, 2)
    avisos = []

    async def cenario(loop):
        await fast_download(pool, msg, tmp_path / "dc.bin", on_flood=lambda exc: avisos.append(exc))
        await pool.close()

    with relogio_simulado() as loop:
        inicio = loop.time()
        loop.run_until_complete(cenario(loop))
        assert loop.time() - inicio >= 7
    assert len(avisos) == 1 and pool.halvings == 1
    assert (tmp_path / "dc.bin").read_bytes() == conteudo


def test_cancelamento_durante_cooldown_devolve_leases(tmp_path) -> None:
    import asyncio
    tamanho = 32 * PART_SIZE
    msg = tl_video(1, size=tamanho)
    client = FakeClient([msg])
    client.file_data[msg.media.document.id] = dados(tamanho)
    client.part_error_factory = floods()
    pool = FastTransferPool(client, 32, 32)

    async def cenario():
        tarefa = asyncio.create_task(fast_download(pool, msg, tmp_path / "cancel.bin"))
        await asyncio.sleep(0)
        tarefa.cancel()
        resultado = await asyncio.gather(tarefa, return_exceptions=True)
        assert isinstance(resultado[0], asyncio.CancelledError)
        await pool.close()

    with relogio_simulado() as loop:
        loop.run_until_complete(cenario())
    assert pool._leased == 0 and client.active_parts == 0
    assert all(s.disconnected for s in client.created_senders)


def test_ordem_do_sender_permanece_com_flood_na_preparacao(tmp_path, monkeypatch) -> None:
    mensagens = [tl_video(i, f"aula {i}", size=4096) for i in range(1, 5)]
    client = FakeClient(mensagens)
    for i, msg in enumerate(mensagens):
        client.file_data[msg.media.document.id] = dados(4096)
        client.part_delays[("GetFileRequest", 0)] = 0.02 if i == 0 else 0
    client.part_error_factory = floods()
    origem, destino = channel(-1, "S"), channel(-2, "D")
    client.register_entity(origem), client.register_entity(destino)
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "TRANSFER_LIMITS_FILE", tmp_path / "aimd.json", raising=False)
    repo = Repository(tmp_path / "ordem.db")
    try:
        copier = Copier(client, repo=repo, config=RunConfig(
            workers=4, connections_per_file=4, max_total_connections=32,
            delay=0, use_dedup=False,
        ), rich_ok=False)
        with relogio_simulado() as loop:
            client.clock = loop.time
            resultado = loop.run_until_complete(copier.run(origem, destino))
        assert resultado["done"] == 4 and resultado["errors"] == 0
        assert [r["caption"] for r in client.sent] == [m.message for m in mensagens]
    finally:
        repo.close()


def test_callback_duplicado_apos_prazo_nao_cria_outro_episodio() -> None:
    pool = FastTransferPool(FakeClient([]), 8, 2)
    erro = FloodWaitError(request=None, capture=1)

    async def cenario(loop):
        primeiro = pool.note_flood(erro, dc_id=2)
        await asyncio.sleep(1.1)
        repetido = pool.note_flood(erro, dc_id=2)
        assert repetido.episode_id == primeiro.episode_id
        assert repetido.first is False
        assert pool.capacity == 4 and pool.halvings == 1 and pool.flood_parts == 1
        await pool.close()

    import asyncio
    with relogio_simulado() as loop:
        loop.run_until_complete(cenario(loop))


def test_falha_no_observer_durante_countdown_nao_derruba_transferencia() -> None:
    pool = FastTransferPool(FakeClient([]), 4, 2)

    def observer_quebrado(*_args):
        raise OSError("terminal indisponível")

    async def cenario():
        pool.note_flood(FloodWaitError(request=None, capture=2))
        pool.controller.observer = observer_quebrado
        await pool.wait_cooldown()
        await pool.close()

    with relogio_simulado() as loop:
        loop.run_until_complete(cenario())
    assert pool.capacity == 2
