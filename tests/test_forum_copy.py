"""Testes de clonagem fiel de fóruns (tópicos, ícones, ordem, multi e espelho).

O cliente simulado desta suíte modela um fórum completo: lista de tópicos na
ordem da API (mais ativo primeiro), criação de tópico com emoji recusado,
ativação do modo fórum e eventos do modo espelho.
"""

from __future__ import annotations

import asyncio
import builtins
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="telecopy_forum_")

import config  # noqa: E402

config.DATA_DIR = Path(_TMP)
config.TEMP_DIR = config.DATA_DIR / "tmp"
config.LOGS_DIR = config.DATA_DIR / "logs"
config.VAULT_DIR = config.DATA_DIR / "vault"
config.EXPORT_DIR = config.DATA_DIR / "exports"
config.BACKUP_DIR = config.DATA_DIR / "backups"
config.SESSION_FILE = config.DATA_DIR / "session"
config.DB_FILE = config.DATA_DIR / "forum.db"
config.STATS_FILE = config.DATA_DIR / "stats.json"
config.DAILY_FILE = config.DATA_DIR / "daily.json"
config.LOG_FILE = config.LOGS_DIR / "telecopy.log"
config.VAULT_FILE = config.VAULT_DIR / "credentials.enc"
config.init_dirs()

import core  # noqa: E402
from database import Status  # noqa: E402
from telethon.tl import types as tl  # noqa: E402
from tests.fake_telegram import FakeClient, channel, text_message  # noqa: E402

EMOJI = 5309914538171670635
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


class EmoticonInvalidError(Exception):
    """Imita telethon.errors.EmoticonInvalidError (nome é o que importa)."""


class MessageActionTopicCreate:
    """Imita telethon.tl.types.MessageActionTopicCreate."""

    def __init__(self, title: str, icon_color: int = 0,
                 icon_emoji_id: Optional[int] = None,
                 title_missing: bool = False) -> None:
        self.title = title
        self.icon_color = icon_color
        self.icon_emoji_id = icon_emoji_id
        self.title_missing = title_missing


def teardown(entity):
    """Permite que a entidade falsa passe no isinstance(Channel) do Telethon."""
    entity.__class__ = tl.Channel
    return entity


def forum(entity_id: int, title: str, *, forum_mode: bool = True):
    return teardown(channel(entity_id, title, forum=forum_mode,
                            broadcast=False))


def topic_message(msg_id: int, text: str, topic_id: int):
    return text_message(msg_id, text, reply_to=SimpleNamespace(
        reply_to_msg_id=topic_id, reply_to_top_id=topic_id, forum_topic=True,
    ))


class ForumClient(FakeClient):
    """FakeClient com tópicos, datas de atividade e requests de fórum."""

    def __init__(self, messages: List[Any], entities: List[Any],
                 catalog: Optional[Dict[int, List[Any]]] = None) -> None:
        super().__init__(messages)
        self.entities = list(entities)
        for entity in self.entities:
            self.register_entity(entity)
        self.catalog: Dict[int, List[Any]] = {k: list(v) for k, v in (catalog or {}).items()}
        self.requests_log: List[Any] = []
        self.created = None
        self.created_id = -5000
        self.create_topic_requests: List[Any] = []
        self.toggle_calls = 0
        self.rejected_emojis: set = set()
        self.is_admin = True
        self._next_topic_id = 900

    # -- diálogos/permissoes ------------------------------------------------

    async def iter_dialogs(self):
        for entity in self.entities:
            yield SimpleNamespace(entity=entity, id=entity.id)

    async def get_permissions(self, entity, user=None):
        return SimpleNamespace(is_admin=self.is_admin, is_banned=False,
                               has_left=False, post_messages=True)

    # -- catálogo -----------------------------------------------------------

    @staticmethod
    def topic(topic_id: int, title: str, *, icon_color: int = 0,
              icon_emoji_id: Optional[int] = None, pinned: bool = False,
              closed: bool = False, hidden: bool = False,
              date: Optional[datetime] = None, top_message: Optional[int] = None):
        return SimpleNamespace(
            id=topic_id, title=title, icon_color=icon_color,
            icon_emoji_id=icon_emoji_id, title_missing=False, closed=closed,
            pinned=pinned, hidden=hidden, top_message=top_message or topic_id,
            date=date,
        )

    def add_topics(self, peer_id: int, topics: List[Any]) -> None:
        self.catalog[peer_id] = list(topics)

    def titles(self, peer_id: int) -> List[str]:
        return [topic.title for topic in self.catalog.get(peer_id, [])]

    def topic_id_by_title(self, peer_id: int, title: str) -> int:
        return next(topic.id for topic in self.catalog[peer_id] if topic.title == title)

    # -- requests -----------------------------------------------------------

    async def __call__(self, request):
        self.requests_log.append(request)
        name = type(request).__name__
        if name == "GetFullChannelRequest":
            return SimpleNamespace(full_chat=SimpleNamespace(about="Descrição copiada"))
        if name == "GetForumTopicsRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            topics = self.catalog.get(peer_id, [])
            return SimpleNamespace(count=len(topics), topics=list(topics))
        if name == "GetForumTopicsByIDRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            wanted = set(request.topics)
            topics = [t for t in self.catalog.get(peer_id, []) if t.id in wanted]
            return SimpleNamespace(count=len(topics), topics=topics)
        if name == "CreateChannelRequest":
            self.created = forum(self.created_id, request.title,
                                 forum_mode=bool(request.forum))
            self.created.broadcast = bool(request.broadcast)
            self.entities.append(self.created)
            self.register_entity(self.created)
            if request.forum:
                self.catalog[self.created.id] = [self.topic(1, "General")]
            return SimpleNamespace(chats=[self.created], updates=[])
        if name == "ToggleForumRequest":
            self.toggle_calls += 1
            entity = self._entities.get(str(getattr(request.channel, "id", "")))
            if entity is not None:
                entity.forum = bool(request.enabled)
                if request.enabled and not any(t.id == 1 for t in self.catalog.get(entity.id, [])):
                    self.catalog[entity.id] = [self.topic(1, "General")]
            return SimpleNamespace()
        if name == "CreateForumTopicRequest":
            if request.icon_emoji_id and int(request.icon_emoji_id) in self.rejected_emojis:
                raise EmoticonInvalidError("REACTION_INVALID")
            self.create_topic_requests.append(request)
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            self._next_topic_id += 1
            created = self.topic(
                self._next_topic_id, request.title,
                icon_color=getattr(request, "icon_color", 0) or 0,
                icon_emoji_id=getattr(request, "icon_emoji_id", None),
            )
            # Tópico novo é o mais recente: entra no topo da lista da API.
            self.catalog.setdefault(peer_id, []).insert(0, created)
            return SimpleNamespace(updates=[SimpleNamespace(topic_id=created.id)])
        if name == "EditForumTopicRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            for topic in self.catalog.get(peer_id, []):
                if topic.id != request.topic_id:
                    continue
                if getattr(request, "title", None) is not None:
                    topic.title = request.title
                if getattr(request, "icon_emoji_id", None) is not None:
                    topic.icon_emoji_id = request.icon_emoji_id or None
                if getattr(request, "closed", None) is not None:
                    topic.closed = request.closed
                if getattr(request, "hidden", None) is not None:
                    topic.hidden = request.hidden
            return SimpleNamespace()
        if name == "UpdatePinnedForumTopicRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            for topic in self.catalog.get(peer_id, []):
                if topic.id == request.topic_id:
                    topic.pinned = request.pinned
            return SimpleNamespace()
        if name == "ReorderPinnedForumTopicsRequest":
            return SimpleNamespace()
        return await super().__call__(request)


class MirrorClient(ForumClient):
    """ForumClient que entrega mensagens pendentes quando o espelho espera."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pending: List[Any] = []

    async def run_until_disconnected(self):
        for message in list(self.pending):
            event = SimpleNamespace(message=message)
            for handler in list(self.handlers):
                await handler(event)


# ── utilidades de fluxo ──────────────────────────────────────────────────────

def feed(*answers: str) -> None:
    queue = list(answers)

    def fake_input(*_args, **_kwargs):
        if not queue:
            raise EOFError
        return queue.pop(0)

    builtins.input = fake_input


async def guard(coro):
    try:
        return await coro
    except EOFError:
        return None


def build_app(client):
    app = core.App()
    app.client = client
    app.picker = core.ChannelPicker(client)
    core.db.reset_all()
    app.dedup = core.DedupEngine(core.db)
    app.config.delay = 0.1
    app.config.workers = 1
    app.config.signature = "@nao_deve_aparecer"
    app.config.use_dedup = True
    return app


def full_forum(source_id: int, title: str = "Fórum Origem") -> List[Any]:
    """Tópicos na ordem da API: do mais ativo para o menos ativo."""
    return [
        ForumClient.topic(43, "SUPORTE", closed=True, date=NOW - timedelta(hours=1)),
        ForumClient.topic(42, "PROMOÇÕES", icon_color=0x6C8EBF,
                          date=NOW - timedelta(hours=2)),
        ForumClient.topic(41, "AVISOS", icon_color=0xD7A917,
                          icon_emoji_id=EMOJI, pinned=True,
                          date=NOW - timedelta(hours=3)),
        ForumClient.topic(1, "General", date=NOW - timedelta(hours=4)),
    ]


def forum_messages() -> List[Any]:
    return [
        topic_message(2, "aviso", 41),
        topic_message(3, "promoção", 42),
        topic_message(4, "suporte", 43),
        text_message(5, "no general"),
        text_message(6, "resposta no aviso", reply_to=SimpleNamespace(
            reply_to_msg_id=2, reply_to_top_id=41, forum_topic=True)),
    ]


def check(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ── 1. Destino existente ────────────────────────────────────────────────────

def test_copiar_para_forum_existente_roteia_cada_topico() -> None:
    origem = forum(-1001, "Fórum Origem")
    destino = forum(-2001, "Fórum Destino")
    client = ForumClient(forum_messages(), [origem, destino], {
        origem.id: full_forum(origem.id),
        destino.id: [ForumClient.topic(1, "General")],
    })
    app = build_app(client)

    # origem → destino existente → todos os tópicos (origem) → destino →
    # ENTER (recriar estrutura) → filtro → opções → confirmar → pause
    feed("/", "Fórum Origem", "1", "1", "", "/", "Fórum Destino", "1", "",
         "0", "n", "s", "")
    asyncio.run(guard(app._menu_copy()))

    check([req.title for req in client.create_topic_requests]
          == ["AVISOS", "PROMOÇÕES", "SUPORTE"],
          f"cria os tópicos do menos ativo ao mais ativo "
          f"({[req.title for req in client.create_topic_requests]})")
    avisos = client.topic_id_by_title(destino.id, "AVISOS")
    promocoes = client.topic_id_by_title(destino.id, "PROMOÇÕES")
    suporte = client.topic_id_by_title(destino.id, "SUPORTE")
    routes = {item["text"]: item for item in client.sent}
    check(routes["aviso"]["reply_to"] == avisos, "aviso vai para AVISOS")
    check(routes["promoção"]["reply_to"] == promocoes, "promoção vai para PROMOÇÕES")
    check(routes["suporte"]["reply_to"] == suporte, "suporte vai para SUPORTE")
    check(routes["no general"]["reply_to"] is None, "General não recebe top_msg_id")
    check(routes["resposta no aviso"]["top_msg_id"] == avisos,
          "resposta dentro do tópico preserva o tópico")
    check(routes["resposta no aviso"]["reply_to"] == routes["aviso"]["msg_id"],
          "resposta aponta para a cópia da mensagem respondida")

    avisos_topic = next(t for t in client.catalog[destino.id] if t.title == "AVISOS")
    check(avisos_topic.icon_emoji_id == EMOJI, "ícone de emoji do tópico é replicado")
    check(avisos_topic.pinned, "tópico fixado é refixado")
    suporte_topic = next(t for t in client.catalog[destino.id] if t.title == "SUPORTE")
    check(suporte_topic.closed, "tópico fechado é fechado no destino")
    check(all("@nao_deve_aparecer" not in (item.get("text") or "")
              for item in client.sent), "assinatura desativada no fórum fiel")


# ── 2. Destino criado automaticamente ───────────────────────────────────────

def test_criar_destino_reproduz_estrutura_e_roteia() -> None:
    origem = forum(-1002, "Fórum Origem")
    client = ForumClient(forum_messages(), [origem], {origem.id: full_forum(origem.id)})
    app = build_app(client)

    # origem → criar destino → título ENTER → filtro → opções → confirmar → pause
    feed("/", "Fórum Origem", "1", "2", "", "0", "n", "s", "")
    asyncio.run(guard(app._menu_copy()))

    check(client.created is not None and client.created.forum,
          "destino automático é um fórum")
    titles = [req.title for req in client.create_topic_requests]
    check(titles == ["AVISOS", "PROMOÇÕES", "SUPORTE"],
          f"tópicos criados na ordem de atividade da origem ({titles})")
    check(client.titles(client.created.id) ==
          ["SUPORTE", "PROMOÇÕES", "AVISOS", "General"],
          f"lista final do destino espelha a ordem da origem "
          f"({client.titles(client.created.id)})")
    avisos = client.topic_id_by_title(client.created.id, "AVISOS")
    routes = {item["text"]: item["reply_to"] for item in client.sent}
    check(routes["aviso"] == avisos and routes["no general"] is None,
          f"mensagens vão ao tópico certo ({routes})")
    created_avisos = next(t for t in client.catalog[client.created.id]
                          if t.title == "AVISOS")
    check(created_avisos.icon_emoji_id == EMOJI and created_avisos.pinned,
          "ícone e fixação replicados no destino novo")


# ── 3. Multi-destino ────────────────────────────────────────────────────────

def test_multi_destino_roteia_por_topico_em_cada_forum() -> None:
    origem = forum(-1003, "Fórum Origem")
    destino_a = forum(-2003, "Destino A")
    destino_b = forum(-2004, "Destino B")
    client = ForumClient(forum_messages(), [origem, destino_a, destino_b], {
        origem.id: full_forum(origem.id),
        destino_a.id: [ForumClient.topic(1, "General")],
        destino_b.id: [ForumClient.topic(1, "General")],
    })
    app = build_app(client)

    feed("/", "Fórum Origem", "1", "",
         "1", "/", "Destino A", "1", "",
         "1", "/", "Destino B", "1", "",
         "0", "0", "s", "")
    asyncio.run(guard(app._menu_multi()))

    for destino in (destino_a, destino_b):
        titles = client.titles(destino.id)
        check(titles == ["SUPORTE", "PROMOÇÕES", "AVISOS", "General"],
              f"{destino.title} recebe a estrutura completa ({titles})")
        avisos = client.topic_id_by_title(destino.id, "AVISOS")
        promo = client.topic_id_by_title(destino.id, "PROMOÇÕES")
        enviadas = {item["text"]: item["reply_to"] for item in client.sent
                    if item["dest"] == destino.id}
        check(enviadas["aviso"] == avisos and enviadas["promoção"] == promo,
              f"{destino.title}: cada mensagem no tópico certo ({enviadas})")
        check(enviadas["no general"] is None,
              f"{destino.title}: General recebe a mensagem do General")
    check(len(client.create_topic_requests) == 6,
          "cada destino criou os próprios tópicos")


def test_multi_destino_sem_forum_copia_em_sequencia() -> None:
    origem = forum(-1010, "Fórum Origem")
    destino = forum(-2010, "Destino Fórum")
    grupo = forum(-2011, "Grupo Simples", forum_mode=False)
    client = ForumClient(forum_messages(), [origem, destino, grupo], {
        origem.id: full_forum(origem.id),
        destino.id: [ForumClient.topic(1, "General")],
        grupo.id: [],
    })
    app = build_app(client)

    feed("/", "Fórum Origem", "1", "",
         "1", "/", "Destino Fórum", "1", "",
         "1", "/", "Grupo Simples", "1",
         "0", "0", "s",
         "n",              # recusa ativar tópicos no grupo simples
         "")
    asyncio.run(guard(app._menu_multi()))

    check(client.titles(destino.id) ==
          ["SUPORTE", "PROMOÇÕES", "AVISOS", "General"],
          "destino fórum recebe a estrutura")
    check(client.catalog[grupo.id] == [] and client.toggle_calls == 0,
          "grupo simples continua sem tópicos e não é convertido")
    enviadas = [item for item in client.sent if item["dest"] == grupo.id]
    check(len(enviadas) == 5, f"grupo simples recebe tudo em sequência ({len(enviadas)})")
    check(all(item["top_msg_id"] is None for item in enviadas),
          "cópia sequencial não usa top_msg_id")
    check(sum(1 for item in enviadas if item["reply_to"] is not None) == 1,
          "só a resposta real usa reply_to no grupo simples")


# ── 4. Modo espelho ─────────────────────────────────────────────────────────

def test_espelho_roteia_topicos_e_cria_topico_novo() -> None:
    origem = forum(-1004, "Fórum Origem")
    destino = forum(-2005, "Fórum Destino")
    client = MirrorClient([], [origem, destino], {
        origem.id: full_forum(origem.id),
        destino.id: [ForumClient.topic(1, "General")],
    })
    novo = text_message(50, "", action=MessageActionTopicCreate(
        "NOVIDADES", icon_color=0xABCDEF, icon_emoji_id=EMOJI,
    ))
    client.pending = [
        topic_message(51, "aviso novo", 41),
        novo,
        topic_message(52, "boas-vindas", 50),
    ]
    app = build_app(client)

    feed("/", "Fórum Origem", "1", "",
         "/", "Fórum Destino", "1", "",
         "0", "s", "")
    asyncio.run(guard(app._menu_mirror()))

    avisos = client.topic_id_by_title(destino.id, "AVISOS")
    check("NOVIDADES" in client.titles(destino.id),
          f"tópico novo da origem é criado na hora ({client.titles(destino.id)})")
    novidades = client.topic_id_by_title(destino.id, "NOVIDADES")
    enviadas = {item["text"]: item["reply_to"] for item in client.sent}
    check(enviadas["aviso novo"] == avisos, "mensagem nova vai ao tópico mapeado")
    check(enviadas["boas-vindas"] == novidades,
          "mensagem do tópico recém-criado vai para a cópia dele")
    check(all(item["text"] != "" for item in client.sent),
          "mensagem de serviço do tópico não é reenviada como texto")


# ── 5. Ícone recusado não aborta ────────────────────────────────────────────

def test_icone_recusado_cria_topico_sem_emoji() -> None:
    origem = forum(-1005, "Fórum Origem")
    client = ForumClient(forum_messages(), [origem], {origem.id: full_forum(origem.id)})
    client.rejected_emojis = {EMOJI}
    app = build_app(client)

    feed("/", "Fórum Origem", "1", "2", "", "0", "n", "s", "")
    asyncio.run(guard(app._menu_copy()))

    check(client.created is not None, "clonagem não aborta por causa do ícone")
    check([req.title for req in client.create_topic_requests]
          == ["AVISOS", "PROMOÇÕES", "SUPORTE"],
          "todos os tópicos são criados mesmo com o emoji recusado")
    avisos = next(t for t in client.catalog[client.created.id] if t.title == "AVISOS")
    check(avisos.icon_emoji_id is None, "tópico fica sem o emoji recusado")
    check(avisos.icon_color == 0xD7A917, "cor original é preservada na primeira queda")
    routes = {item["text"]: item["reply_to"] for item in client.sent}
    check(routes["aviso"] == avisos.id, "mensagens continuam roteadas")


# ── 6. Destino sem tópicos: oferece ativar ──────────────────────────────────

def test_destino_sem_forum_oferece_ativar_topicos() -> None:
    origem = forum(-1006, "Fórum Origem")
    destino = forum(-2006, "Grupo Destino", forum_mode=False)
    client = ForumClient(forum_messages(), [origem, destino], {
        origem.id: full_forum(origem.id),
        destino.id: [],
    })
    app = build_app(client)

    # ENTER no prompt de tópico do destino não aparece (não é fórum ainda);
    # o programa pergunta se deve ativar os tópicos: "s".
    feed("/", "Fórum Origem", "1", "1", "", "/", "Grupo Destino", "1",
         "s", "0", "n", "s", "")
    asyncio.run(guard(app._menu_copy()))

    check(client.toggle_calls == 1, "pede ToggleForumRequest ao usuário admin")
    check(destino.forum, "destino passa a ser fórum")
    check(client.titles(destino.id) ==
          ["SUPORTE", "PROMOÇÕES", "AVISOS", "General"],
          f"estrutura recriada depois de ativar ({client.titles(destino.id)})")
    routes = {item["text"]: item["reply_to"] for item in client.sent}
    check(routes["aviso"] == client.topic_id_by_title(destino.id, "AVISOS"),
          "mensagens roteadas após a ativação")


# ── 7. Cache desatualizado do picker ────────────────────────────────────────

def test_flag_forum_do_cache_e_rebuscado_antes_da_decisao() -> None:
    stale = forum(-1007, "Fórum Origem", forum_mode=False)
    fresca = forum(-1007, "Fórum Origem")
    destino = forum(-2007, "Fórum Destino")
    client = ForumClient(forum_messages(), [stale, destino], {
        fresca.id: full_forum(fresca.id),
        destino.id: [ForumClient.topic(1, "General")],
    })
    # O diálogo guarda a entidade velha; get_entity devolve a atual.
    client.register_entity(fresca)
    app = build_app(client)

    feed("/", "Fórum Origem", "1", "1", "", "/", "Fórum Destino", "1", "",
         "0", "n", "s", "")
    asyncio.run(guard(app._menu_copy()))

    check(client.titles(destino.id) ==
          ["SUPORTE", "PROMOÇÕES", "AVISOS", "General"],
          f"flag forum velho não faz a cópia cair no General "
          f"({client.titles(destino.id)})")
    routes = {item["text"]: item["reply_to"] for item in client.sent}
    check(routes["aviso"] == client.topic_id_by_title(destino.id, "AVISOS"),
          "mensagens roteadas com a entidade relida")


# ── 8. Checkpoints antigos sem tópico ───────────────────────────────────────

def test_checkpoints_antigos_avisam_e_podem_ser_zerados() -> None:
    origem = forum(-1008, "Fórum Origem")
    destino = forum(-2008, "Fórum Destino")
    client = ForumClient(forum_messages(), [origem, destino], {
        origem.id: full_forum(origem.id),
        destino.id: [ForumClient.topic(1, "General")],
    })
    app = build_app(client)
    # Execução antiga: mensagem 2 concluída sem tópico (foi ao General).
    core.db.upsert_checkpoint(origem.id, destino.id, 2, Status.DONE)

    feed("/", "Fórum Origem", "1", "1", "", "/", "Fórum Destino", "1", "",
         "s", "0", "n", "s", "")
    asyncio.run(guard(app._menu_copy()))

    avisos = client.topic_id_by_title(destino.id, "AVISOS")
    check(core.db.checkpoint_route(origem.id, destino.id, 2)["dest_topic"] == avisos,
          "checkpoint antigo é reescrito com o tópico correto após zerar")
    enviadas = {item["text"] for item in client.sent}
    check("aviso" in enviadas, "mensagem antes no General é copiada de novo")


# ── 9. Origem inteira + tópico único no destino ─────────────────────────────

def test_escolher_topico_unico_no_destino_avisa_e_confirma() -> None:
    origem = forum(-1009, "Fórum Origem")
    destino = forum(-2009, "Fórum Destino")
    catalog = {
        origem.id: full_forum(origem.id),
        destino.id: [ForumClient.topic(1, "General"), ForumClient.topic(700, "ARQUIVO")],
    }
    client = ForumClient(forum_messages(), [origem, destino], catalog)
    app = build_app(client)

    feed("/", "Fórum Origem", "1", "1", "", "/", "Fórum Destino", "1", "700",
         "n")
    asyncio.run(guard(app._menu_copy()))
    check(not client.sent, "recusar o aviso cancela a cópia")
    check(not client.create_topic_requests, "nada é criado ao cancelar")

    feed("/", "Fórum Origem", "1", "1", "", "/", "Fórum Destino", "1", "700",
         "s", "0", "n", "s", "")
    asyncio.run(guard(app._menu_copy()))
    check(not client.create_topic_requests,
          "com tópico único escolhido a estrutura não é recriada")
    check(all(item["reply_to"] == 700 or item["top_msg_id"] == 700
              for item in client.sent),
          "todas as mensagens vão para o tópico escolhido")


# ── 10. Ordem de criação (unidade) ──────────────────────────────────────────

def test_ordem_de_criacao_usa_atividade_crescente() -> None:
    from channel_clone import ChannelCloner, ForumTopicSpec

    topics = [
        ForumTopicSpec(id=1, title="General", date=NOW),
        ForumTopicSpec(id=30, title="Recente", date=NOW),
        ForumTopicSpec(id=20, title="Antigo", date=NOW - timedelta(days=5)),
        ForumTopicSpec(id=40, title="Meio", date=NOW - timedelta(days=1)),
    ]
    order = [topic.title for topic in ChannelCloner._creation_order(topics)]
    check(order == ["General", "Antigo", "Meio", "Recente"],
          f"criação do menos ativo ao mais ativo ({order})")

    sem_datas = [
        ForumTopicSpec(id=1, title="General"),
        ForumTopicSpec(id=11, title="Primeiro da API"),
        ForumTopicSpec(id=22, title="Último da API"),
    ]
    order = [topic.title for topic in ChannelCloner._creation_order(sem_datas)]
    check(order == ["General", "Último da API", "Primeiro da API"],
          f"sem datas, a ordem da API é invertida ({order})")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
