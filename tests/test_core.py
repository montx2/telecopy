"""
Testes automatizados do TELECOPY (pytest, sem dependências externas de rede).

Execute:
    python -m pytest tests -v
ou
    python tests/test_core.py        (runner embutido, sem pytest)
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isola dados de teste ANTES de importar os módulos do app
_TMP = tempfile.mkdtemp(prefix="telecopy_tests_")
os.environ.setdefault("TELECOPY_TEST_DIR", _TMP)

import config  # noqa: E402

config.DATA_DIR = Path(_TMP)
config.TEMP_DIR = config.DATA_DIR / "tmp"
config.LOGS_DIR = config.DATA_DIR / "logs"
config.VAULT_DIR = config.DATA_DIR / "vault"
config.EXPORT_DIR = config.DATA_DIR / "exports"
config.BACKUP_DIR = config.DATA_DIR / "backups"
config.DB_FILE = config.DATA_DIR / "test.db"
config.STATS_FILE = config.DATA_DIR / "stats.json"
config.DAILY_FILE = config.DATA_DIR / "daily.json"
config.LOG_FILE = config.LOGS_DIR / "telecopy.log"
config.VAULT_FILE = config.VAULT_DIR / "credentials.enc"
config.init_dirs()

from config import WORKERS_HARD_CAP, RunConfig  # noqa: E402
from copier import Copier, add_signature, media_info, split_text  # noqa: E402
from database import Repository, Status  # noqa: E402
from dedup import BloomFilter, DedupEngine, hash_message  # noqa: E402
from filters import apply as filter_apply  # noqa: E402
from filters import resolve as resolve_filter  # noqa: E402
from security import Vault  # noqa: E402
from system_analyzer import analyzer  # noqa: E402
from utils import DailyLimit, Fmt, Stats, daily_limit  # noqa: E402

from tests.fake_telegram import (  # noqa: E402
    FakeClient,
    FakeMessage,
    album_messages,
    channel,
    document_message,
    photo_message,
    text_message,
    video_message,
)

asserts = []


def check(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    asserts.append(message)


# ── Testes unitários ─────────────────────────────────────────────────────────


def test_formatadores() -> None:
    check(Fmt.size(0) == "0 B", "Fmt.size(0)")
    check(Fmt.size(1024) == "1.0 KB", "Fmt.size(1024)")
    check(Fmt.size(1024 ** 3) == "1.0 GB", "Fmt.size(GB)")
    check(Fmt.duration(65) == "1m 05s", "Fmt.duration(65)")
    check(Fmt.duration(3700) == "1h 01m", "Fmt.duration(3700)")
    check(Fmt.number(1234567) == "1.234.567", "Fmt.number")
    check(Fmt.trunc("abcdefgh", 5) == "abcd…", "Fmt.trunc")
    check(Fmt.eta(0, 10, 1) == "—", "Fmt.eta sem dados")


def test_bloom_filter() -> None:
    bloom = BloomFilter(capacity=10_000, fp_rate=0.001)
    for i in range(1_000):
        bloom.add(f"chave-{i}")
    check(all(f"chave-{i}" in bloom for i in range(1_000)), "Bloom: sem falsos negativos")
    ausentes = sum(1 for i in range(10_000, 20_000) if f"chave-{i}" in bloom)
    check(ausentes < 40, f"Bloom: falsos positivos aceitáveis ({ausentes}/10000)")
    bloom.clear()
    check("chave-1" not in bloom, "Bloom: clear()")


def test_split_texto() -> None:
    check(len(split_text("curto")) == 1, "split_text curto")
    longo = ("palavra " * 2000).strip()
    partes = split_text(longo)
    check(all(len(p) <= 4096 for p in partes), "split_text respeita o limite")
    check("".join(p for p in partes).replace(" ", "") == longo.replace(" ", ""),
          "split_text não perde conteúdo")


def test_assinatura() -> None:
    check(add_signature("oi", "") == "oi", "assinatura vazia")
    check(add_signature("oi", "@canal").endswith("@canal"), "assinatura aplicada")
    grande = "x" * 2000
    assinado = add_signature(grande, "@canal")
    check(len(assinado) <= 1024, "assinatura respeita o limite de legenda")


def test_hash_msg() -> None:
    a = photo_message(1, "legenda")
    b = photo_message(2, "legenda")
    c = photo_message(3, "outra")
    check(hash_message(a) != hash_message(b), "hash considera o id da mídia")
    check(hash_message(a) != hash_message(c), "hash considera a legenda")
    t1 = text_message(10, "bom dia")
    t2 = text_message(11, "bom dia")
    check(hash_message(t1) == hash_message(t2), "texto idêntico no mesmo dia = mesmo hash")


def test_filtros() -> None:
    foto = photo_message(1)
    doc = document_message(2)
    video = video_message(3)
    texto = text_message(4, "olá")
    check(filter_apply("photos", foto), "filtro fotos")
    check(not filter_apply("photos", doc), "filtro fotos ignora documento")
    check(filter_apply("documents", doc), "filtro documentos")
    check(filter_apply("videos", video), "filtro vídeos")
    check(filter_apply("text", texto), "filtro texto")
    check(not filter_apply("text", foto), "filtro texto ignora mídia")
    check(not filter_apply("photos", None), "filtro tolera objeto inválido")
    check(resolve_filter("0") == "all", "resolve todas no número 0")
    check(resolve_filter("1") == "photos", "resolve primeiro filtro do menu")
    check(resolve_filter("all") == "all", "resolve código")
    check(resolve_filter("lixo") is None, "resolve inválido")


def test_vault_roundtrip() -> None:
    caminho = config.VAULT_DIR / "teste.enc"
    vault = Vault(caminho)
    dados = {"api_id": 123456, "api_hash": "a" * 32, "lang": "pt"}
    vault.save(dados)
    check(vault.exists(), "vault gravado")
    check(b"api_hash" not in caminho.read_bytes(), "vault realmente cifrado")
    check(vault.load() == dados, "vault descriptografa corretamente")
    check(vault.is_readable(), "vault legível")
    vault.delete()
    check(not vault.exists(), "vault removido")


def test_vault_portatil() -> None:
    """Trocar a pasta de dados não pode inutilizar as credenciais."""
    origem = config.VAULT_DIR / "portatil.enc"
    Vault(origem).save({"api_id": 1, "api_hash": "b" * 32})
    copia_dir = Path(_TMP) / "outro_pc" / "vault"
    copia_dir.mkdir(parents=True, exist_ok=True)
    destino = copia_dir / "portatil.enc"
    destino.write_bytes(origem.read_bytes())
    (copia_dir / "local.key").write_bytes((config.VAULT_DIR / "local.key").read_bytes())
    check(Vault(destino).load() == {"api_id": 1, "api_hash": "b" * 32},
          "vault funciona após mover a pasta")


def test_repositorio() -> None:
    caminho = Path(_TMP) / "repo_test.db"
    repo = Repository(caminho)
    repo.upsert_checkpoint(1, 2, 10, Status.PENDING)
    check(not repo.is_done(1, 2, 10), "checkpoint pendente")
    repo.mark_done(1, 2, 10, "hash", 100, "photo")
    check(repo.is_done(1, 2, 10), "checkpoint concluído")
    check(repo.done_ids(1, 2) == {10}, "ids concluídos")
    check(repo.last_processed_id(1, 2) == 10, "último id processado")

    repo.register_dedup("hash", 2, 99, 100, 1)
    check(repo.check_dedup("hash", 2) == 99, "dedup por hash")
    check(repo.check_dedup("hash", 3) is None, "dedup é por destino")
    check(repo.dedup_count() == 1, "contagem de hashes")

    repo.mark_processed(1, 10, "hash", 2)
    check(repo.is_processed(1, 10, 2), "processed por par")
    check(repo.processed_ids(1, 2) == {10}, "processed ids")

    repo.register_session("s1", 1, 2, "origem", "destino")
    repo.update_stats("s1", success=5, errors=1, duplicates=2, bytes_total=1000)
    stats = repo.global_stats()
    check(stats["success"] == 5 and stats["errors"] == 1, "estatísticas globais")

    repo.add_failure(1, 2, 10, "erro")
    repo.add_failure(1, 2, 10, "erro de novo")
    falhas = repo.failures()
    check(len(falhas) == 1 and falhas[0]["attempts"] == 2, "falhas acumulam tentativas")
    repo.clear_failure(1, 2, 10)
    check(repo.failures() == [], "falha removida")

    repo.set_kv("k", "v")
    check(repo.get_kv("k") == "v", "chave/valor")

    resumo = repo.pair_summary(1, 2)
    check(resumo["done"] == 1, "resumo do par")

    saude = repo.health()
    check(saude["integrity"] == "ok", "integridade do banco")

    removidos = repo.prune(days=0)
    check(isinstance(removidos, dict), "prune retorna contagens")
    repo.vacuum()
    repo.close()
    check(repo.global_stats()["success"] == 5, "dados persistem após reabrir")


def test_dedup_engine() -> None:
    caminho = Path(_TMP) / "dedup_test.db"
    repo = Repository(caminho)
    engine = DedupEngine(repo)
    check(engine.check("x", 1) is None, "dedup: novo")
    engine.register("x", 1, 42, 10, 1)
    check(engine.check("x", 1) == 42, "dedup: registrado no LRU")
    engine.reset()
    check(engine.check("x", 1) is None, "dedup: cache limpo")
    engine.warm_up(1)
    check(engine.check("x", 1) == 42, "dedup: warm-up recupera do banco")
    check(engine.check("x", 2) is None, "dedup: outro destino")
    desativado = DedupEngine(repo, enabled=False)
    check(desativado.check("x", 1) is None, "dedup desativado")
    repo.close()


def test_daily_and_stats() -> None:
    limite = DailyLimit(Path(_TMP) / "daily_test.json")
    limite.reset()
    limite.add(5)
    check(limite.count == 5, "limite diário conta")
    nivel, contagem, restante = limite.status()
    check(nivel == "ok" and restante > 0, "status do limite")

    stats = Stats(Path(_TMP) / "stats_test.json")
    stats.new_session()
    stats.inc("photos", 3)
    stats.add_bytes(2048)
    stats.add_time(1.5)
    stats.save()
    recarregado = Stats(Path(_TMP) / "stats_test.json")
    check(recarregado.data["total"] == 3, "stats acumulam total")
    check(recarregado.data["sessions"] == 1, "stats contam sessões")


def test_runconfig() -> None:
    cfg = RunConfig(delay=-1, workers=99, memory_profile=1.5)
    check(cfg.delay == 0.0, "delay mínimo (0 = sem espera)")
    check(cfg.workers == WORKERS_HARD_CAP, "workers limitado pelo teto de hardware")
    check(cfg.memory_profile == 0.95, "memory_profile limitado")
    clone = RunConfig.from_dict(cfg.to_dict())
    check(clone.to_dict() == cfg.to_dict(), "serialização de RunConfig")


def test_analyzer() -> None:
    resumo = analyzer.summary()
    check("workers" in resumo, "analyzer recomenda workers")
    estimativa = analyzer.estimate_for_channel(1.0)
    check(estimativa.recommended_workers >= 1, "estimativa do canal")
    check(analyzer.recommended() >= 1, "workers recomendados")


def test_media_info() -> None:
    check(media_info(text_message(1, "oi")) == (False, "text", 0), "media_info texto")
    tem, tipo, tamanho = media_info(photo_message(2))
    check(tem and tipo == "photo" and tamanho > 0, "media_info foto")
    tem, tipo, _ = media_info(document_message(3))
    check(tem and tipo == "document", "media_info documento")
    tem, tipo, _ = media_info(video_message(4))
    check(tem and tipo == "video", "media_info vídeo")


# ── Testes de integração do motor ───────────────────────────────────────────


_RUN_COUNTER = {"n": 0}


def _fresh_db(name: str) -> Path:
    """Caminho de banco exclusivo por execução (evita vazamento entre testes)."""
    _RUN_COUNTER["n"] += 1
    return Path(_TMP) / f"{name}_{os.getpid()}_{_RUN_COUNTER['n']}.db"


async def _run_copy(messages, *, fail_ids=None, prev_repo=None, config=None,
                    filter_code="all", resume=True, forward=False, client=None):
    from database import Repository

    db_path = Path(prev_repo.path) if prev_repo else _fresh_db("copy")
    repo = prev_repo or Repository(db_path)
    client = client or FakeClient(messages, fail_ids=fail_ids)
    source = channel(-1001, "Origem")
    dest = channel(-2002, "Destino")
    client.register_entity(source)
    client.register_entity(dest)
    cfg = config or RunConfig(workers=2, delay=0.1)
    copier = Copier(client, repo=repo, config=cfg, rich_ok=False)
    result = await copier.run(source, dest, filter_code=filter_code,
                              resume=resume, forward=forward)
    return result, client, repo


def test_copy_simples() -> None:
    messages = [
        text_message(1, "primeira"),
        text_message(2, "segunda"),
        document_message(3, "relatorio.pdf"),
    ]
    result, client, repo = asyncio.run(_run_copy(messages))
    check(result["done"] == 3, f"cópia simples enviou 3 (obtido {result['done']})")
    check(len(client.sent) == 3, "três envios registrados")
    check(repo.is_done(-1001, -2002, 1), "checkpoint da msg 1")
    check(repo.is_done(-1001, -2002, 3), "checkpoint da msg 3")
    check(client.sent[0]["kind"] == "text", "texto enviado como texto")
    check(any(s["kind"] == "file" for s in client.sent), "documento enviado como arquivo")
    check(not any(Path(str(s.get("path"))).exists() for s in client.sent),
          "arquivos temporários removidos")
    repo.close()


def test_copy_ordem() -> None:
    messages = [text_message(i, f"msg {i}") for i in range(1, 21)]
    result, client, repo = asyncio.run(_run_copy(messages))
    enviadas = [s["text"] for s in client.sent if s["kind"] == "text"]
    check(enviadas == [f"msg {i}" for i in range(1, 21)], "ordem das mensagens preservada")
    check(result["done"] == 20, "20 mensagens copiadas")
    repo.close()


def test_copy_album() -> None:
    mensagens = album_messages(100, 3) + [text_message(200, "depois do álbum")]
    result, client, repo = asyncio.run(_run_copy(mensagens))
    albuns = [s for s in client.sent if s["kind"] == "file" and s["album"]]
    check(len(albuns) == 3, "álbum enviado em uma única chamada com 3 arquivos")
    check(result["done"] == 4, "álbum conta 3 mensagens + 1 texto")
    check(result["albums"] == 1, "contador de álbuns")
    repo.close()


def test_copy_filtro() -> None:
    mensagens = [photo_message(1), document_message(2), text_message(3, "texto")]
    result, client, repo = asyncio.run(_run_copy(mensagens, filter_code="photos"))
    check(result["done"] == 1, "filtro de fotos envia apenas 1")
    repo.close()


def test_copy_sem_duplicatas() -> None:
    mensagens = [photo_message(1, "a"), text_message(2, "b")]
    repo = Repository(Path(_TMP) / "dedup_copy.db")

    primeiro, client1, _ = asyncio.run(_run_copy(mensagens, prev_repo=repo))
    check(primeiro["done"] == 2, "primeira execução copia tudo")

    # Mesmo conteúdo (mesma mídia, ids de mensagem diferentes) → dedup bloqueia
    clone_foto = photo_message(50, "a")
    clone_foto.media = mensagens[0].media
    clone_foto.photo = mensagens[0].media.photo
    clones = [clone_foto, text_message(51, "b")]
    segundo, client2, _ = asyncio.run(_run_copy(clones, prev_repo=repo))
    check(segundo["done"] == 0, "segunda execução não duplica conteúdo")
    check(segundo["duplicates"] == 2, "duplicatas contabilizadas")
    repo.close()


def test_copy_retomada() -> None:
    mensagens = [text_message(i, f"linha {i}") for i in range(1, 11)]
    repo = Repository(Path(_TMP) / "resume.db")
    resultado, client1, _ = asyncio.run(_run_copy(mensagens, prev_repo=repo))
    check(resultado["done"] == 10, "primeira execução completa")

    segundo, client2, _ = asyncio.run(_run_copy(mensagens, prev_repo=repo))
    check(segundo["done"] == 0, "retomada não reenvia nada")
    repo.close()


def test_copy_erro_recuperavel() -> None:
    mensagens = [document_message(i, f"arq{i}.pdf") for i in range(1, 6)]
    legacy = RunConfig(workers=2, delay=0.1, delivery="upload", server_copy=False,
                       placeholder=True, on_failure="placeholder")
    resultado, client, repo = asyncio.run(
        _run_copy(mensagens, fail_ids={3}, config=legacy)
    )
    check(resultado["errors"] == 1, "mensagem com falha conta como erro")
    check(resultado["done"] == 4, "modo placeholder explícito preserva comportamento legado")
    falhas = repo.failures()
    check(len(falhas) == 1 and falhas[0]["msg_id"] == 3, "falha entra na fila de repetição")
    repo.close()


def test_copy_floodwait() -> None:
    from unittest.mock import patch

    mensagens = [text_message(1, "a"), text_message(2, "b")]
    client = FakeClient(mensagens)
    client.flood_on = {1: 1}
    source, dest = channel(-1, "O"), channel(-2, "D")
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(Path(_TMP) / "flood.db")
    copier = Copier(client, repo=repo, config=RunConfig(workers=1, delay=0.1), rich_ok=False)

    async def run():
        with patch("asyncio.sleep", new=_fast_sleep):
            return await copier.run(source, dest)

    resultado = asyncio.run(run())
    check(resultado["done"] == 2, f"FloodWait é tratado (obtido {resultado['done']})")
    repo.close()


async def _fast_sleep(*_args, **_kwargs):
    return None


def test_copy_forward() -> None:
    mensagens = [text_message(i, f"m{i}") for i in range(1, 5)]
    resultado, client, repo = asyncio.run(_run_copy(mensagens, forward=True))
    check(resultado["done"] == 4, "modo encaminhar copia tudo")
    check(len(client.forwarded) == 4, "forward_messages utilizado")
    check(client.sent == [], "modo encaminhar não baixa nem reenvia")
    check(all("as_album" not in call["kwargs"] for call in client.forwarded),
          "não passa mais o argumento de álbum obsoleto do Telethon")
    repo.close()


def test_copy_forward_album() -> None:
    mensagens = album_messages(300, 3) + [text_message(400, "depois")]
    resultado, client, repo = asyncio.run(_run_copy(mensagens, forward=True))
    check(resultado["done"] == 4, "encaminha todas as mensagens do álbum")
    check(resultado["albums"] == 1, "contabiliza o álbum encaminhado")
    check(len(client.forwarded) == 2 and len(client.forwarded[0]["ids"]) == 3,
          "encaminha as três mídias do álbum juntas")
    check("as_album" not in client.forwarded[0]["kwargs"],
          "album forward não usa parâmetro removido")
    repo.close()


def test_copy_forum_preserva_topico_de_cada_mensagem() -> None:
    mensagens = [
        text_message(1, "pergunta", reply_to_msg_id=11),
        text_message(2, "novidade", reply_to_msg_id=22),
        text_message(3, "geral"),
    ]
    source = channel(-1101, "Fórum origem", forum=True, broadcast=False)
    dest = channel(-2202, "Fórum destino", forum=True, broadcast=False)
    client = FakeClient(mensagens)
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(_fresh_db("forum_topics"))
    copier = Copier(
        client,
        repo=repo,
        config=RunConfig(workers=1, delay=0.1, use_dedup=False),
        rich_ok=False,
    )
    result = asyncio.run(copier.run(
        source, dest, topic_map={1: 901, 11: 911, 22: 922},
    ))
    routes = {item["text"]: item["reply_to"] for item in client.sent}
    check(result["done"] == 3, "copia todas as mensagens do fórum")
    check(routes == {"pergunta": 911, "novidade": 922, "geral": 901},
          f"cada mensagem vai para o tópico correspondente ({routes})")
    repo.close()


def test_forward_em_forum_usa_o_topico_destino() -> None:
    mensagens = [text_message(1, "aviso", reply_to_msg_id=11)]
    source = channel(-1103, "Fórum origem", forum=True, broadcast=False)
    dest = channel(-2204, "Fórum destino", forum=True, broadcast=False)

    class TopicForwardClient(FakeClient):
        def __init__(self, items):
            super().__init__(items)
            self.topic_forwards = []

        async def get_input_entity(self, entity):
            return entity

        async def __call__(self, request):
            if type(request).__name__ == "ForwardMessagesRequest":
                self.topic_forwards.append(request)
                # Resposta fiel: o Telegram confirma um ID por random_id. Sem
                # isso o item não pode virar DONE (invariante de ID confirmado).
                for random_id in request.random_id:
                    self._ids += 1
                    self._random_messages[int(random_id)] = FakeMessage(id=self._ids)
                return SimpleNamespace()
            raise AssertionError(f"request inesperado: {type(request).__name__}")

    client = TopicForwardClient(mensagens)
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(_fresh_db("forward_forum"))
    copier = Copier(client, repo=repo, config=RunConfig(workers=1), rich_ok=False)
    result = asyncio.run(copier.run(
        source, dest, forward=True, topic_map={1: 901, 11: 911},
    ))
    check(result["done"] == 1, "encaminha mensagem do fórum")
    check(len(client.topic_forwards) == 1
          and client.topic_forwards[0].top_msg_id == 911,
          "encaminha usando o ID do tópico de destino")
    check(not client.forwarded, "encaminhamento ao tópico usa o request MTProto direto")
    repo.close()


def test_copy_forward_para_em_erro_de_permissao() -> None:
    from telethon.errors import ChatAdminRequiredError

    mensagens = [text_message(i, f"m{i}") for i in range(1, 8)]
    client = FakeClient(mensagens)
    erro = ChatAdminRequiredError(request=None)

    async def falha_envio(entity, messages, from_peer=None, **kwargs):
        raise erro

    client.forward_messages = falha_envio
    resultado, client, repo = asyncio.run(
        _run_copy(mensagens, forward=True, client=client)
    )
    check(resultado["done"] == 0 and resultado["errors"] == 1,
          "permissão ausente é exibida após a primeira falha")
    check("administrador" in resultado["first_error"].lower(),
          "mostra como corrigir a permissão de destino")
    repo.close()


def test_copy_valida_permissao_do_destino_antes_da_coleta() -> None:
    mensagens = [text_message(i, f"m{i}") for i in range(1, 4)]
    source, dest = channel(-1001, "Origem"), channel(-2002, "Destino")
    client = FakeClient(mensagens)
    client.register_entity(source)
    client.register_entity(dest)

    async def get_me(input_peer=False):
        return object()

    async def get_permissions(entity, user):
        return type("Permissions", (), {"post_messages": False})()

    client.get_me = get_me
    client.get_permissions = get_permissions
    repo = Repository(Path(_TMP) / "preflight_permissions.db")
    copier = Copier(
        client, repo=repo,
        config=RunConfig(workers=1, delay=0.1, forward_mode=True),
        rich_ok=False,
    )
    result = asyncio.run(copier.run(source, dest, forward=True))
    check(result["done"] == 0 and result["errors"] == 1,
          "destino sem permissão bloqueia antes da coleta")
    check("administrador" in result["first_error"].lower(),
          "pré-checagem orienta a permissão necessária")
    check(not client.forwarded, "pré-checagem não tenta enviar mensagens")
    repo.close()


def test_copy_canal_protegido_copia_normalmente() -> None:
    mensagens = [
        text_message(1, "aula 1"),
        photo_message(2, "quadro"),
        document_message(3, "apostila.pdf"),
    ]
    source, dest = channel(-1001, "Origem protegida"), channel(-2002, "Destino")
    source.noforwards = True
    client = FakeClient(mensagens)
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(Path(_TMP) / "protected_source.db")
    copier = Copier(client, repo=repo, config=RunConfig(workers=1, delay=0.1), rich_ok=False)
    result = asyncio.run(copier.run(source, dest))
    check(result["done"] == 3 and result["errors"] == 0,
          f"canal protegido é copiado por download + reenvio ({result})")
    check(result["complete"] is True, "auditoria confirma cópia completa do canal protegido")
    check(len(client.sent) == 3, "todas as mensagens foram reenviadas ao destino")
    repo.close()


def test_copy_canal_protegido_em_modo_forward_faz_fallback_para_reenvio() -> None:
    mensagens = [
        text_message(1, "texto protegido"),
        photo_message(2, "foto protegida"),
    ]
    source, dest = channel(-1001, "Origem protegida"), channel(-2002, "Destino")
    source.noforwards = True
    client = FakeClient(mensagens)
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(Path(_TMP) / "protected_forward_fallback.db")
    copier = Copier(
        client, repo=repo,
        config=RunConfig(workers=1, delay=0.1, forward_mode=True),
        rich_ok=False,
    )
    result = asyncio.run(copier.run(source, dest, forward=True))
    check(result["done"] == 2 and result["errors"] == 0,
          f"modo forward alterna automaticamente para reenvio em canal protegido ({result})")
    check(not client.forwarded, "não tenta forward_messages quando origem tem noforwards")
    check(len(client.sent) == 2, "mensagens enviadas por download + upload")
    repo.close()


def test_forward_restrito_pelo_servidor_faz_fallback_para_reenvio() -> None:
    class RestrictedForwardClient(FakeClient):
        async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
            exc_cls = type("ChatForwardsRestrictedError", (Exception,), {})
            raise exc_cls("You cannot forward messages from a protected chat")

    mensagens = [
        text_message(1, "msg 1"),
        document_message(2, "lista.pdf"),
    ]
    source, dest = channel(-1001, "Origem sem flag local"), channel(-2002, "Destino")
    client = RestrictedForwardClient(mensagens)
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(Path(_TMP) / "restricted_server_fallback.db")
    copier = Copier(
        client, repo=repo,
        config=RunConfig(workers=1, delay=0.1, forward_mode=True),
        rich_ok=False,
    )
    result = asyncio.run(copier.run(source, dest, forward=True))
    check(result["done"] == 2 and result["errors"] == 0,
          f"ChatForwardsRestrictedError faz fallback para reenvio ({result})")
    check(len(client.sent) == 2, "mensagens reenviadas após restrição de forward")
    repo.close()


def test_espelho() -> None:
    client = FakeClient([])
    source, dest = channel(-11, "O"), channel(-22, "D")
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(Path(_TMP) / "mirror.db")
    copier = Copier(client, repo=repo, config=RunConfig(workers=1, delay=0.1), rich_ok=False)

    asyncio.run(copier.mirror(source, dest))
    check(len(client.handlers) == 0, "handler de espelho removido ao sair")
    repo.close()


def test_copy_limite_diario() -> None:
    """Ao atingir o limite diário a cópia para e o progresso não se perde."""
    from utils import daily_limit

    daily_limit.reset()
    daily_limit.add(10_000)
    try:
        resultado, client, repo = asyncio.run(
            _run_copy([text_message(i, f"m{i}") for i in range(1, 6)])
        )
        check(resultado["errors"] >= 1 or resultado["done"] == 0,
              "limite diário interrompe os envios")
        check(repo.pair_summary(-1001, -2002)["done"] == 0,
              "nada é marcado como concluído após o bloqueio")
        repo.close()
    finally:
        daily_limit.reset()


def test_copy_arquivo_grande_e_nome_estranho() -> None:
    mensagens = [
        document_message(1, "relatório final (v2) [ok].pdf", size=900_000),
        document_message(2, "sem-extensao", size=1000),
    ]
    resultado, client, repo = asyncio.run(_run_copy(mensagens))
    check(resultado["done"] == 2, "arquivos grandes/estranhos são copiados")
    check(all(os.path.basename(str(s["path"])).strip() for s in client.sent),
          "nomes de arquivo temporário são válidos")
    check(resultado["bytes"] > 0, "bytes contabilizados")
    repo.close()


def test_copy_album_com_falha() -> None:
    """Placeholder opt-in mantém o teste do fluxo de reserva do álbum."""
    mensagens = album_messages(300, 2) + [text_message(310, "depois")]
    legacy = RunConfig(workers=2, delay=0.1, delivery="upload", server_copy=False,
                       placeholder=True, on_failure="placeholder", use_dedup=False)
    resultado, client, repo = asyncio.run(
        _run_copy(mensagens, fail_ids={301}, config=legacy)
    )
    check(resultado["errors"] >= 1, "falha no álbum é contabilizada")
    check(resultado["done"] >= 1, "placeholder opt-in mantém o restante do histórico")
    check(len(repo.failures()) >= 1, "falha entra na fila de repetição")
    repo.close()


def test_limpeza_temporarios() -> None:
    """Após uma cópia bem-sucedida a pasta tmp/ fica vazia."""
    asyncio.run(_run_copy([photo_message(500), document_message(501)]))
    restantes = [p for p in config.TEMP_DIR.glob("*") if p.is_file()]
    check(not restantes, f"pasta tmp limpa (encontrados: {restantes})")


def test_prefs_roundtrip() -> None:
    from prefs import Preferences

    caminho = Path(_TMP) / "prefs_test.json"
    p = Preferences(caminho)
    p.set_language("en")
    p.set("onboarding_done", True)
    cfg = RunConfig(delay=2.5, workers=3, signature="@teste")
    p.save_config(cfg)
    recarregado = Preferences(caminho)
    check(recarregado.language == "en", "idioma persistido")
    check(recarregado.get("onboarding_done") is True, "onboarding persistido")
    check(recarregado.config.delay == 2.5, "delay persistido")
    check(recarregado.config.signature == "@teste", "assinatura persistida")


def test_retry_messages() -> None:
    mensagens = [document_message(i, f"arq{i}.pdf") for i in range(1, 4)]
    legacy = RunConfig(workers=2, delay=0.1, delivery="upload", server_copy=False,
                       placeholder=True, on_failure="placeholder", use_dedup=False)
    resultado, client, repo = asyncio.run(
        _run_copy(mensagens, fail_ids={2}, config=legacy)
    )
    check(resultado["errors"] == 1, "falha inicial registrada")

    client.fail_ids.clear()
    source, dest = channel(-1001, "Origem"), channel(-2002, "Destino")
    copier = Copier(client, repo=repo, config=RunConfig(workers=1, delay=0.1), rich_ok=False)
    recuperado = asyncio.run(copier.retry_messages(source, dest, [2]))
    check(recuperado["done"] == 1, "retry reenvia a mensagem com sucesso")
    check(repo.failures() == [], "fila de falhas limpa após sucesso")
    repo.close()


# ── Runner ───────────────────────────────────────────────────────────────────

def test_forum_preserva_respostas_e_general() -> None:
    from types import SimpleNamespace

    source = channel(-1201, "Fórum origem", forum=True, broadcast=False)
    dest = channel(-2201, "Fórum destino", forum=True, broadcast=False)
    messages = [
        text_message(5, "geral"),
        text_message(10, "topo do tópico", reply_to_msg_id=11),
        text_message(12, "resposta no tópico", reply_to=SimpleNamespace(
            reply_to_msg_id=10, reply_to_top_id=11, forum_topic=True)),
        text_message(13, "resposta no general", reply_to=SimpleNamespace(
            reply_to_msg_id=5, reply_to_top_id=None, forum_topic=True)),
    ]
    client = FakeClient(messages)
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(_fresh_db("forum_replies"))
    copier = Copier(client, repo=repo, config=RunConfig(workers=1, delay=0.1, use_dedup=False),
                    rich_ok=False)
    result = asyncio.run(copier.run(
        source, dest, dest_topic=901, topic_map={1: 901, 11: 911},
        topic_titles={1: "General", 11: "Perguntas"},
    ))
    check(result["complete"], "fórum com replies conclui a conferência")
    by_text = {item.get("text"): item for item in client.sent}
    check(by_text["topo do tópico"]["reply_to"] == 911, "mensagem inicial vai ao tópico")
    check(by_text["resposta no tópico"]["top_msg_id"] == 911, "reply usa top_msg_id do tópico")
    check(by_text["resposta no tópico"]["reply_to"] == by_text["topo do tópico"]["msg_id"],
          "reply aponta para a cópia da mensagem respondida")
    check(by_text["resposta no general"]["reply_to"] == by_text["geral"]["msg_id"],
          "reply no General aponta para a cópia correta")
    check({row["id"]: row["missing"] for row in result["topics"]} == {1: 0, 11: 0},
          "conferência por tópico sem faltas")
    repo.close()


def test_forum_repeticoes_nao_sao_descartadas() -> None:
    source = channel(-1202, "Fórum origem", forum=True, broadcast=False)
    dest = channel(-2202, "Fórum destino", forum=True, broadcast=False)
    messages = [
        text_message(1, "aviso repetido", reply_to_msg_id=11),
        text_message(2, "aviso repetido", reply_to_msg_id=22),
    ]
    client = FakeClient(messages)
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(_fresh_db("forum_repeat"))
    copier = Copier(client, repo=repo, config=RunConfig(workers=1, delay=0.1, use_dedup=False),
                    rich_ok=False)
    result = asyncio.run(copier.run(source, dest, topic_map={1: 1, 11: 911, 22: 922}))
    check(result["done"] == 2 and result["duplicates"] == 0,
          "conteúdo igual em tópicos diferentes é copiado duas vezes")
    check([item["reply_to"] for item in client.sent] == [911, 922], "tópicos corretos")
    repo.close()


def test_midia_nao_suportada_nao_conclui() -> None:
    dice = type("MessageMediaDice", (), {})()
    messages = [FakeMessage(id=1, media=dice)]
    from unittest.mock import patch

    with patch.object(Copier, "_prompt_failure", return_value="a"):
        result, client, repo = asyncio.run(_run_copy(
            messages, config=RunConfig(use_dedup=False, placeholder=False)
        ))
    check(result["errors"] == 1 and result["unsupported"] == 1, "mídia não reproduzível é erro")
    check(not result["complete"], "cópia incompleta não é marcada como completa")
    check(not client.sent, "nada é enviado fingindo sucesso")
    repo.close()


def test_legenda_longa_preserva_texto_e_entidades() -> None:
    from telethon.tl.types import MessageEntityBold

    text = "A" * 1500 + "📚" + "B" * 1500
    msg = photo_message(1, text, entities=[MessageEntityBold(offset=0, length=10)])
    result, client, repo = asyncio.run(_run_copy([msg], config=RunConfig(use_dedup=False)))
    check(result["done"] == 1 and result["complete"], "legenda longa conclui")
    files = [item for item in client.sent if item["kind"] == "file"]
    texts = [item["text"] for item in client.sent if item["kind"] == "text"]
    check(len(files) == 1 and len(texts) >= 1, "mídia + continuações")
    check(files[0]["caption"] + "".join(texts) == text, "nenhum caractere perdido")
    check(files[0]["formatting_entities"][0].length == 10, "entidade preservada")
    repo.close()


def test_retry_forum_usa_topico_salvo() -> None:
    source = channel(-1203, "Fórum origem", forum=True, broadcast=False)
    dest = channel(-2203, "Fórum destino", forum=True, broadcast=False)
    messages = [document_message(10, "apostila.pdf", reply_to_msg_id=11)]
    client = FakeClient(messages, fail_ids={10})
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(_fresh_db("forum_retry"))
    copier = Copier(client, repo=repo,
                    config=RunConfig(workers=1, delay=0.1, use_dedup=False, placeholder=False),
                    rich_ok=False)
    first = asyncio.run(copier.run(source, dest, topic_map={1: 1, 11: 911}))
    check(first["errors"] == 1 and not first["complete"], "falha inicial registrada")
    check(repo.checkpoint_route(source.id, dest.id, 10)["dest_topic"] == 911,
          "tópico destino persistido para retry")
    client.fail_ids.clear()
    retried = asyncio.run(copier.retry_messages(source, dest, [10]))
    check(retried["done"] == 1, "retry reenvia")
    check(client.sent[-1]["reply_to"] == 911, "retry volta ao tópico correto")
    repo.close()


def test_enquete_nativa_e_copiada() -> None:
    from types import SimpleNamespace

    poll = SimpleNamespace(question="Quanto é 2+2?", answers=[
        SimpleNamespace(text="3", option=b"0"), SimpleNamespace(text="4", option=b"1"),
    ], closed=False, public_voters=False, multiple_choice=False, quiz=False)
    media = type("MessageMediaPoll", (), {})()
    media.poll = poll
    media.results = SimpleNamespace(results=[], solution=None, solution_entities=None)
    messages = [FakeMessage(id=1, media=media)]
    result, client, repo = asyncio.run(_run_copy(messages, config=RunConfig(use_dedup=False)))
    check(result["done"] == 1, "enquete é recriada")
    check(client.sent[0]["kind"] == "native", "envio usa InputMediaPoll")
    repo.close()


def test_imagem_como_documento_preserva_arquivo() -> None:
    msg = document_message(1, "scan.png", mime="image/png", size=4096)
    check(media_info(msg)[1] == "document", "imagem em MessageMediaDocument continua documento")
    result, client, repo = asyncio.run(_run_copy([msg], config=RunConfig(use_dedup=False)))
    check(result["done"] == 1, "imagem-documento copiada")
    sent = client.sent[0]
    check(sent["kind"] == "file" and sent["force_document"], "enviada como arquivo, não recomprimida")
    check(sent["mime_type"] == "image/png", "mime original preservado")
    check(any(getattr(a, "filename", None) == "scan.png" for a in sent["attributes"] or []),
          "nome original preservado")
    repo.close()


def test_album_forum_usa_upload_media_e_topico() -> None:
    source = channel(-1204, "Fórum origem", forum=True, broadcast=False)
    dest = channel(-2204, "Fórum destino", forum=True, broadcast=False)
    messages = album_messages(20, 2)
    for msg in messages:
        msg.reply_to_msg_id = 11
        msg.reply_to = None
        msg.__post_init__()
    client = FakeClient(messages)
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(_fresh_db("forum_album"))
    copier = Copier(client, repo=repo, config=RunConfig(workers=1, delay=0.1, use_dedup=False),
                    rich_ok=False)
    result = asyncio.run(copier.run(source, dest, topic_map={1: 1, 11: 911}))
    check(result["done"] == 2 and result["albums"] == 1, "álbum do fórum copiado")
    names = [type(req).__name__ for req in client.requests]
    check("UploadMediaRequest" in names and "SendMultiMediaRequest" in names,
          "álbum usa UploadMedia antes de SendMultiMedia")
    check(all(item["reply_to"] == 911 and item["top_msg_id"] == 911 for item in client.sent),
          "álbum fica dentro do tópico correto")
    check(all(not Path(str(item["input_media"].file.name)).name.startswith("m20_")
              for item in client.sent), "nome exibido não é o prefixo temporário")
    repo.close()


def test_limite_diario_interrompe_sem_marcar_falhas() -> None:
    daily_limit.reset()
    daily_limit.add(config.DAILY_LIMIT)
    try:
        result, _client, repo = asyncio.run(_run_copy(
            [text_message(1, "a"), text_message(2, "b")],
            config=RunConfig(use_dedup=False),
        ))
        check(result["done"] == 0 and result["errors"] == 0,
              "limite diário não envia nem marca falhas")
        check(result.get("interrupted") and not result.get("complete"),
              "execução fica interrompida, não completa")
        check(repo.pair_summary(-1001, -2002)["errors"] == 0,
              "nenhum checkpoint de erro criado")
        check(repo.failure_stats()["total"] == 0, "nenhuma falha registrada")
        repo.close()
    finally:
        daily_limit.reset()


def test_reset_all_apaga_mapa_de_topicos() -> None:
    repo = Repository(_fresh_db("reset_map"))
    repo.set_kv("forum_topic_map:-1:-2", '{"1": 1}')
    repo.set_kv("preferencia_interna", "manter")
    repo.reset_all()
    check(repo.get_kv("forum_topic_map:-1:-2") is None, "mapa de tópicos apagado no reset")
    check(repo.get_kv("preferencia_interna") == "manter", "outras chaves internas preservadas")
    repo.close()


def test_limite_diario_conta_envios() -> None:
    daily_limit.reset()
    try:
        result, _client, repo = asyncio.run(_run_copy(
            [text_message(1, "a"), text_message(2, "b")],
            config=RunConfig(use_dedup=False),
        ))
        check(result["done"] == 2, "envios concluídos")
        check(daily_limit.count >= 2, "limite diário registra mensagens enviadas")
        repo.close()
    finally:
        daily_limit.reset()


TESTS = [value for name, value in sorted(globals().items())
         if name.startswith("test_") and callable(value)]


def _main() -> int:
    try:
        import pytest  # noqa: F401

        return pytest.main([__file__, "-v", "--tb=short"])
    except ImportError:
        pass

    failures = 0
    for test in TESTS:
        try:
            result = test()
            if asyncio.iscoroutine(result):
                asyncio.run(result)
            print(f"  ✓ {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {test.__name__}: {exc}")
            import traceback

            traceback.print_exc()
    total = len(TESTS)
    print(f"\n  {total - failures}/{total} testes passaram · {len(asserts)} verificações")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
