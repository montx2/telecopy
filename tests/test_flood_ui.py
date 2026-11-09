"""Um episódio de flood: um aviso, estado legível e detalhes só no arquivo."""
from __future__ import annotations

import asyncio

from telethon.errors import FloodPremiumWaitError

import copier as copier_module
from config import RunConfig
from copier import Copier, ProgressDisplay
from fasttransfer import FastTransferPool
from i18n import get_lang, set_lang
from tests.fake_telegram import FakeClient
from tests.relogio_simulado import relogio_simulado


def test_estado_de_espera_e_agregado_sao_traduzidos() -> None:
    anterior = get_lang()
    try:
        progress = ProgressDisplay(1, "", rich_ok=False)
        progress.set_wait_state(11, 32)
        set_lang("pt")
        progress.refresh_state()
        assert "32" in progress.wait_text and "ordem preservada" in progress.wait_text
        set_lang("en")
        progress.refresh_state()
        assert "32" in progress.wait_text and "order preserved" in progress.wait_text
        assert "MB/s" in progress.aggregate_text
    finally:
        set_lang(anterior)


def test_32_erros_produzem_um_aviso_e_contador_agregado(monkeypatch, tmp_path) -> None:
    avisos = []
    detalhes = []
    monkeypatch.setattr(copier_module.ui, "warn", lambda texto: avisos.append(texto))
    monkeypatch.setattr(copier_module.logger, "debug", lambda texto: detalhes.append(texto))
    monkeypatch.setattr(copier_module, "TRANSFER_LIMITS_FILE", tmp_path / "aimd.json", raising=False)
    client = FakeClient([])
    copier = Copier(client, config=RunConfig(), rich_ok=False)
    progress = ProgressDisplay(1, "", rich_ok=False)
    copier._progress = progress
    pool = FastTransferPool(client, 32, 32)
    copier._transfer_pool = pool

    estados = []

    async def cenario(loop):
        pool.controller.observer = copier._show_flood
        for _ in range(32):
            erro = FloodPremiumWaitError(request=None, capture=11)
            pool.note_flood(erro, dc_id=2)
            await copier._record_flood(erro)
        assert pool.flood_parts == 32
        estados.append(progress.wait_text)
        await copier._handle_flood(erro)
        assert loop.time() >= 1011
        await pool.close()

    with relogio_simulado() as loop:
        loop.run_until_complete(cenario(loop))
    assert len(avisos) == 1
    assert "32" in estados[0] and "ordem preservada" in estados[0]
    assert progress.wait_text == ""


def test_countdown_nao_fica_congelado_no_valor_inicial(monkeypatch) -> None:
    progress = ProgressDisplay(1, "", rich_ok=False)
    with relogio_simulado() as loop:
        monkeypatch.setattr(copier_module.time, "monotonic", loop.time)
        progress.set_wait_state(11, 32)
        assert "11" in progress.wait_text
        loop.run_until_complete(asyncio.sleep(3))
        progress.refresh_state()
        assert "8" in progress.wait_text
        loop.run_until_complete(asyncio.sleep(8))
        progress.refresh_state()
        assert progress.wait_text == ""


def test_detalhes_do_episodio_chegam_ao_arquivo_de_log(tmp_path, monkeypatch) -> None:
    from utils import logger
    caminho = tmp_path / "detalhes.log"
    monkeypatch.setattr(logger, "path", caminho)
    logger.debug("Flood episódio=7 em download offset=4096: detalhe de diagnóstico")
    assert "episódio=7" in caminho.read_text(encoding="utf-8")
