"""Clonagem de canal de curso em fórum: ordem idêntica à origem com turbo ligado.

Cenário do usuário: aulas numeradas (Aula 321, 322, 323...), álbuns e tópicos.
Os workers terminam FORA de ordem de propósito; o destino ainda tem que sair
na sequência exata da origem, cada aula no seu tópico.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
import copier as copier_module
from config import RunConfig, turbo_settings
from copier import Copier
from database import Repository
from fasttransfer import PART_SIZE
from tests.fake_telegram import FakeClient, channel
from tests.test_ordem import tl_video
from utils import DailyLimit

TOPIC_A, TOPIC_B = 10, 20
DEST_A, DEST_B = 110, 220


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(copier_module, "daily_limit", DailyLimit(tmp_path / "daily.json"))
    repo = Repository(tmp_path / "curso.db")
    try:
        yield SimpleNamespace(root=tmp_path, repo=repo)
    finally:
        repo.close()


def aula(msg_id: int, numero: int, topic: int, *, grouped: int | None = None,
         size: int = PART_SIZE + 64):
    """Aula em vídeo dentro de um tópico de fórum."""
    msg = tl_video(msg_id, f"Aula {numero}", size=size)
    msg.reply_to = SimpleNamespace(
        reply_to_msg_id=topic, reply_to_top_id=topic, forum_topic=True,
    )
    if grouped is not None:
        msg.grouped_id = grouped
    return msg


def curso():
    """Aulas 321..332: singles, um álbum de 3 e troca de tópico no meio."""
    items = []
    items.append(aula(1, 321, TOPIC_A))
    items.append(aula(2, 322, TOPIC_A))
    # álbum: 3 arquivos da mesma aula, precisam continuar juntos e na ordem
    items.append(aula(3, 323, TOPIC_A, grouped=900))
    items.append(aula(4, 323, TOPIC_A, grouped=900))
    items.append(aula(5, 323, TOPIC_A, grouped=900))
    items.append(aula(6, 324, TOPIC_A))
    items.append(aula(7, 325, TOPIC_B))          # muda de tópico
    items.append(aula(8, 326, TOPIC_B))
    items.append(aula(9, 327, TOPIC_B, grouped=901))
    items.append(aula(10, 327, TOPIC_B, grouped=901))
    items.append(aula(11, 328, TOPIC_A))         # volta para o tópico A
    items.append(aula(12, 329, TOPIC_A))
    return items


def montar(env, messages, **overrides):
    client = FakeClient(messages, temp_dir=str(env.root / "dl"))
    for msg in messages:
        block = bytes(range(256))
        size = int(msg.media.document.size)
        client.file_data[msg.media.document.id] = (block * (size // 256 + 1))[:size]
    source = channel(-1001, "Curso Origem", forum=True, broadcast=False)
    dest = channel(-2002, "Curso Destino", forum=True, broadcast=False)
    client.register_entity(source)
    client.register_entity(dest)
    values = {k: v for k, v in turbo_settings().items() if k != "delay"}
    values.update({"delay": 0, "use_dedup": False, "workers": 6})
    values.update(overrides)
    copier = Copier(client, repo=env.repo, config=RunConfig(**values), rich_ok=False)
    return SimpleNamespace(client=client, copier=copier, source=source, dest=dest)


def preparar_fora_de_ordem(total: int):
    """Faz os ÚLTIMOS itens ficarem prontos primeiro (pior caso para a ordem)."""
    original = Copier._prepare
    finished: list[int] = []

    async def prep(self, index, item, *a, **k):
        await asyncio.sleep((total - index) * 0.004)
        result = await original(self, index, item, *a, **k)
        finished.append(index)
        return result

    return patch.object(Copier, "_prepare", prep), finished


def test_curso_forum_sai_identico_a_origem_com_turbo(env) -> None:
    messages = curso()
    ctx = montar(env, messages)
    patcher, finished = preparar_fora_de_ordem(len(messages))

    with patcher:
        result = asyncio.run(ctx.copier.run(
            ctx.source, ctx.dest, topic_map={TOPIC_A: DEST_A, TOPIC_B: DEST_B},
        ))

    assert result["done"] == len(messages)
    assert result["errors"] == 0 and result["complete"] is True

    # A preparação foi realmente paralela e fora de ordem...
    assert finished != sorted(finished), "o teste precisa preparar fora de ordem"
    # ...mas o destino recebeu na ordem exata da origem.
    captions = [row["caption"] for row in ctx.client.sent]
    assert captions == [m.message for m in messages]
    assert captions[:3] == ["Aula 321", "Aula 322", "Aula 323"]

    # Cada aula caiu no tópico certo do destino.
    for row, msg in zip(ctx.client.sent, messages):
        esperado = DEST_A if msg.reply_to.reply_to_top_id == TOPIC_A else DEST_B
        assert row["top_msg_id"] == esperado, f"{row['caption']} foi para o tópico errado"

    # Os álbuns continuaram agrupados e contíguos.
    album_flags = [row["album"] for row in ctx.client.sent]
    assert album_flags == [False, False, True, True, True, False,
                           False, False, True, True, False, False]
    assert ctx.client.upload_media_calls == 5, "álbuns usam UploadMedia + SendMultiMedia"


def test_ordem_por_topico_e_checkpoints_batem_com_a_origem(env) -> None:
    messages = curso()
    ctx = montar(env, messages)
    patcher, _ = preparar_fora_de_ordem(len(messages))
    with patcher:
        asyncio.run(ctx.copier.run(ctx.source, ctx.dest,
                                   topic_map={TOPIC_A: DEST_A, TOPIC_B: DEST_B}))

    source_id, dest_id = ctx.source.id, ctx.dest.id
    done = ctx.copier.repo.done_ids(source_id, dest_id)
    assert done == {m.id for m in messages}
    # Nenhum checkpoint marcado antes da confirmação: todos têm ID de destino.
    for msg in messages:
        row = ctx.copier.repo.checkpoint_route(source_id, dest_id, msg.id)
        assert row and row["status"] == "done"
        assert row["dest_msg_id"], "done sem ID de destino quebraria a retomada"
        esperado = DEST_A if msg.reply_to.reply_to_top_id == TOPIC_A else DEST_B
        assert row["dest_topic"] == esperado, "checkpoint precisa guardar o tópico certo"


def test_retomada_apos_interrupt_nao_repete_nem_inverte(env) -> None:
    messages = curso()
    ctx = montar(env, messages)
    original_send = Copier._send
    enviadas: list[str] = []

    async def send_and_stop(self, payload, *a, **k):
        result = await original_send(self, payload, *a, **k)
        enviadas.extend(m.message for m in payload["messages"])
        if len(enviadas) >= 5:
            self.request_stop()          # simula Ctrl+C no meio
        return result

    with patch.object(Copier, "_send", send_and_stop):
        asyncio.run(ctx.copier.run(ctx.source, ctx.dest,
                                   topic_map={TOPIC_A: DEST_A, TOPIC_B: DEST_B}))
    primeira = list(enviadas)
    assert primeira == [m.message for m in messages[:len(primeira)]]

    # Retoma do checkpoint: continua de onde parou, sem repetir e sem inverter.
    resto = montar(env, messages)
    second = Copier(resto.client, repo=env.repo, config=ctx.copier.cfg, rich_ok=False)
    asyncio.run(second.run(ctx.source, ctx.dest,
                           topic_map={TOPIC_A: DEST_A, TOPIC_B: DEST_B}))
    retomadas = [row["caption"] for row in resto.client.sent]
    assert primeira + retomadas == [m.message for m in messages]
    assert not (env.root / "tmp").exists() or not list((env.root / "tmp").glob("m*_*_*_*")), \
        "não pode sobrar temporário órfão depois da interrupção"


def test_aula_repetida_com_mesmo_arquivo_nao_e_descartada_no_modo_fiel(env) -> None:
    """Curso real repete material: a 2ª ocorrência TEM que ir para o destino.

    Com dedup ligado o hash (mesmo doc + mesma legenda) descartaria a segunda
    aula. É por isso que a cópia de fórum desliga dedup e assinatura.
    """
    messages = [
        aula(1, 321, TOPIC_A),
        aula(2, 322, TOPIC_A),
    ]
    # mesmo documento e mesma legenda da Aula 321, reaproveitada em outro tópico
    clone = aula(3, 321, TOPIC_B)
    clone.media = messages[0].media
    messages.append(clone)

    ctx = montar(env, messages, use_dedup=False, signature="")
    result = asyncio.run(ctx.copier.run(ctx.source, ctx.dest,
                                        topic_map={TOPIC_A: DEST_A, TOPIC_B: DEST_B}))
    assert result["done"] == 3
    assert [r["caption"] for r in ctx.client.sent] == ["Aula 321", "Aula 322", "Aula 321"]
    assert ctx.client.sent[2]["top_msg_id"] == DEST_B

    # Contraste: com dedup ligado a repetição some — não serve para clone fiel.
    # Banco novo para não confundir com a retomada por checkpoint.
    # workers=1 torna a detecção determinística: com vários workers o dedup é
    # checado na preparação, antes de a 1ª ocorrência ser confirmada, e pode
    # pular ou não — mais um motivo para não usar dedup em clone fiel.
    outro = montar(env, messages, use_dedup=True, workers=1)
    repo2 = Repository(env.root / "dedup.db")
    try:
        com_dedup = Copier(outro.client, repo=repo2, config=outro.copier.cfg, rich_ok=False)
        asyncio.run(com_dedup.run(outro.source, outro.dest,
                                  topic_map={TOPIC_A: DEST_A, TOPIC_B: DEST_B}))
    finally:
        repo2.close()
    assert [r["caption"] for r in outro.client.sent] == ["Aula 321", "Aula 322"]


def test_legenda_longa_preserva_texto_integral_mas_vira_mais_de_uma_mensagem(env) -> None:
    """Limite do Telegram (1024 na legenda): o texto inteiro chega, em partes."""
    from config import CAPTION_LIMIT

    longa = "Conteúdo da aula. " * 120        # > 1024 caracteres
    msg = aula(1, 321, TOPIC_A)
    msg.message = longa
    ctx = montar(env, [msg], use_dedup=False, signature="")
    result = asyncio.run(ctx.copier.run(ctx.source, ctx.dest,
                                        topic_map={TOPIC_A: DEST_A, TOPIC_B: DEST_B}))
    assert result["done"] == 1 and result["errors"] == 0
    textos = [(r["caption"] or r["text"] or "") for r in ctx.client.sent]
    assert len(textos) > 1, "legenda acima do limite precisa de continuação"
    assert "".join(textos) == longa, "o texto tem que chegar integral e na ordem"
    assert all(len(x) <= CAPTION_LIMIT for x in textos[:1])
    assert all(r["top_msg_id"] == DEST_A for r in ctx.client.sent)
