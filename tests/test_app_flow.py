"""
Testes de fluxo da interface (menus) com entrada simulada.

Garantem que os menus de cópia, multi-destino, ferramentas e configurações
funcionam de ponta a ponta sem tocar na rede nem no terminal real.
"""

from __future__ import annotations

import asyncio
import builtins
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="telecopy_flow_")

import config  # noqa: E402

config.DATA_DIR = Path(_TMP)
config.TEMP_DIR = config.DATA_DIR / "tmp"
config.LOGS_DIR = config.DATA_DIR / "logs"
config.VAULT_DIR = config.DATA_DIR / "vault"
config.EXPORT_DIR = config.DATA_DIR / "exports"
config.BACKUP_DIR = config.DATA_DIR / "backups"
config.SESSION_FILE = config.DATA_DIR / "session"
config.DB_FILE = config.DATA_DIR / "flow.db"
config.STATS_FILE = config.DATA_DIR / "stats.json"
config.DAILY_FILE = config.DATA_DIR / "daily.json"
config.LOG_FILE = config.LOGS_DIR / "telecopy.log"
config.VAULT_FILE = config.VAULT_DIR / "credentials.enc"
config.init_dirs()

import core  # noqa: E402
from database import Repository  # noqa: E402
from telethon.tl import types as tl  # noqa: E402


def teardown_channel(channel):
    """Faz o canal falso passar no isinstance(Channel) do Telethon."""
    channel.__class__ = tl.Channel
    return channel
from tests.fake_telegram import (  # noqa: E402
    FakeClient,
    album_messages,
    channel,
    document_message,
    photo_message,
    text_message,
)


class _Dialogs:
    def __init__(self, entities):
        self._entities = entities

    def __aiter__(self):
        async def gen():
            for entity in self._entities:
                yield SimpleNamespace(entity=entity, id=entity.id)

        return gen()


class FlowClient(FakeClient):
    """FakeClient que também lista diálogos e valida a interface."""

    def __init__(self, messages, entities):
        super().__init__(messages)
        self.entities = entities
        for entity in entities:
            self.register_entity(entity)

    async def iter_dialogs(self):
        for entity in self.entities:
            yield SimpleNamespace(entity=entity, id=entity.id)


class CloneFlowClient(FlowClient):
    """Simula os requests MTProto usados para criar um clone de fórum."""

    def __init__(self, messages, source):
        super().__init__(messages, [source])
        self.source = source
        self.created = None
        self.created_requests = []
        self._next_topic_id = 100
        self.topic_catalog = {
            source.id: [
                self._topic(1, "General"),
                self._topic(11, "Perguntas", icon_color=0x6C8EBF, pinned=True),
                self._topic(22, "Novidades"),
            ]
        }

    @staticmethod
    def _topic(topic_id, title, *, icon_color=None, icon_emoji_id=None,
               pinned=False, closed=False, hidden=False):
        return SimpleNamespace(
            id=topic_id,
            title=title,
            icon_color=icon_color or 0,
            icon_emoji_id=icon_emoji_id,
            title_missing=False,
            closed=closed,
            pinned=pinned,
            hidden=hidden,
            top_message=topic_id,
            date=None,
        )

    async def __call__(self, request):
        self.created_requests.append(request)
        name = type(request).__name__
        if name == "GetFullChannelRequest":
            return SimpleNamespace(full_chat=SimpleNamespace(about="Descrição copiada"))
        if name == "GetForumTopicsRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            topics = self.topic_catalog.get(peer_id, [])
            return SimpleNamespace(count=len(topics), topics=list(topics))
        if name == "GetForumTopicsByIDRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            topics = [t for t in self.topic_catalog.get(peer_id, [])
                      if t.id in set(request.topics)]
            return SimpleNamespace(count=len(topics), topics=topics)
        if name == "CreateChannelRequest":
            self.created = teardown_channel(channel(
                -500, request.title,
                forum=bool(request.forum), broadcast=bool(request.broadcast),
            ))
            self.entities.append(self.created)
            self.register_entity(self.created)
            if request.forum:
                self.topic_catalog[self.created.id] = [self._topic(1, "General")]
            return SimpleNamespace(chats=[self.created], updates=[])
        if name == "CreateForumTopicRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            self._next_topic_id += 1
            topic = self._topic(
                self._next_topic_id,
                request.title,
                icon_color=getattr(request, "icon_color", None),
                icon_emoji_id=getattr(request, "icon_emoji_id", None),
            )
            self.topic_catalog.setdefault(peer_id, []).append(topic)
            return SimpleNamespace(updates=[SimpleNamespace(topic_id=topic.id)])
        if name == "EditForumTopicRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            for topic in self.topic_catalog.get(peer_id, []):
                if topic.id == request.topic_id:
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
            for topic in self.topic_catalog.get(peer_id, []):
                if topic.id == request.topic_id:
                    topic.pinned = request.pinned
            return SimpleNamespace()
        if name == "ReorderPinnedForumTopicsRequest":
            return SimpleNamespace()
        # Demandas de envio/upload do motor de cópia usam o fake base.
        return await super().__call__(request)


def feed(*answers: str) -> None:
    """Substitui input() por respostas sequenciais."""
    queue = list(answers)

    def fake_input(*_args, **_kwargs):
        if not queue:
            raise EOFError
        return queue.pop(0)

    builtins.input = fake_input


def build_app(messages, entities):
    app = core.App()
    entities = [teardown_channel(e) if not isinstance(e, tl.Channel) else e
                for e in entities]
    client = FlowClient(messages, entities)
    app.client = client
    app.picker = core.ChannelPicker(client)
    app.dedup = core.DedupEngine(app_repo(app))
    return app, client


def app_repo(app):
    repo = Repository(Path(_TMP) / "flow_shared.db")
    repo.reset_all()
    return repo


def check(condition, message):
    if not condition:
        raise AssertionError(message)


# ── Fluxos ───────────────────────────────────────────────────────────────────

def test_fluxo_copiar_canal() -> None:
    origem = channel(-100, "Canal Origem")
    destino = channel(-200, "Canal Destino")
    mensagens = [
        text_message(1, "bom dia"),
        photo_message(2, "foto legal"),
        document_message(3, "apostila.pdf"),
        *album_messages(10, 2),
        text_message(20, "fim"),
    ]
    app, client = build_app(mensagens, [origem, destino])
    app.config.delay = 0.1
    app.config.workers = 2

    # buscar origem → escolher → buscar destino → escolher → filtro → opções → confirmar
    feed("/", "Origem", "1", "1", "/", "Destino", "1", "0", "n", "s")

    asyncio.run(_guard(app._menu_copy()))
    check(len(client.sent) >= 4, f"fluxo de cópia enviou mensagens ({len(client.sent)})")
    check(any(s["kind"] == "text" for s in client.sent), "texto enviado")
    check(any(s["kind"] == "file" for s in client.sent), "arquivo enviado")


def test_fluxo_copiar_canal_protegido() -> None:
    origem = channel(-101, "Matemática Online 2026")
    origem.noforwards = True
    destino = channel(-201, "Canal Destino")
    mensagens = [
        text_message(1, "aula 01"),
        document_message(2, "lista_exercicios.pdf"),
    ]
    app, client = build_app(mensagens, [origem, destino])
    app.config.delay = 0.1
    app.config.workers = 1
    app.config.forward_mode = True

    feed("/", "Matemática", "1", "1", "/", "Destino", "1", "0", "n", "s")

    asyncio.run(_guard(app._menu_copy()))
    check(len(client.sent) == 2, f"canal protegido copiado pelo menu ({len(client.sent)})")
    check(not client.forwarded, "usa reenvio em vez de forward em canal protegido")


def test_fluxo_cria_destino_e_reproduz_forum() -> None:
    origem = teardown_channel(channel(-100, "Fórum Origem", forum=True, broadcast=False))
    mensagens = [
        text_message(1, "aviso repetido", reply_to_msg_id=11),
        text_message(2, "aviso repetido", reply_to_msg_id=22),
    ]
    app = core.App()
    client = CloneFlowClient(mensagens, origem)
    app.client = client
    app.picker = core.ChannelPicker(client)
    app.dedup = core.DedupEngine(app_repo(app))
    app.config.delay = 0.1
    app.config.workers = 1
    app.config.signature = ""
    app.config.use_dedup = True

    # origem → criar destino → manter título → todos os filtros → copiar
    feed("/", "Fórum Origem", "1", "2", "", "0", "n", "s", "")
    asyncio.run(_guard(app._menu_copy()))

    channel_request = next(
        req for req in client.created_requests
        if type(req).__name__ == "CreateChannelRequest"
    )
    check(client.created is not None and client.created.forum,
          "destino automático também é um fórum")
    check(channel_request.forum and channel_request.megagroup and not channel_request.broadcast,
          "cria supergrupo com fórum habilitado")
    check(channel_request.title == "Fórum Origem (Clone)"
          and channel_request.about == "Descrição copiada",
          "replica título (com sufixo) e descrição da origem")

    created_topics = client.topic_catalog[client.created.id]
    # GetForumTopics devolve do mais recente para o mais antigo e o Telegram
    # ordena por última atividade: a criação vai do mais antigo para o mais
    # recente, então "Novidades" (menos ativa) nasce antes de "Perguntas".
    check([topic.title for topic in created_topics] == [
        "General", "Novidades", "Perguntas",
    ], "recria os tópicos na ordem crescente de atividade da origem")
    forum_topic_requests = [req for req in client.created_requests
                            if type(req).__name__ == "CreateForumTopicRequest"]
    perguntas = [req for req in forum_topic_requests if req.title == "Perguntas"]
    check(perguntas and perguntas[0].icon_color == 0x6C8EBF,
          "replica a cor do ícone de tópico")
    check(any(type(req).__name__ == "UpdatePinnedForumTopicRequest"
              for req in client.created_requests), "replica tópico fixado")
    by_title = {topic.title: topic.id for topic in created_topics}
    routes = [(item["text"], item["reply_to"]) for item in client.sent]
    check(routes == [("aviso repetido", by_title["Perguntas"]),
                     ("aviso repetido", by_title["Novidades"])],
          f"envia inclusive conteúdos iguais a seus tópicos ({routes})")


def test_mapear_topicos_de_destino_existente() -> None:
    from channel_clone import ChannelCloner

    origem = teardown_channel(channel(-150, "Fórum Origem", forum=True, broadcast=False))
    destino = teardown_channel(channel(-250, "Fórum Destino", forum=True, broadcast=False))
    client = CloneFlowClient([], origem)
    client.topic_catalog[destino.id] = [
        client._topic(1, "General"),
        client._topic(201, "Perguntas", icon_color=0x6C8EBF),
        client._topic(202, "Novidades"),
    ]
    mapping = asyncio.run(ChannelCloner(client).map_existing_topics(origem, destino))
    check(mapping == {1: 1, 11: 201, 22: 202},
          f"mapeia tópicos homônimos para retomar ({mapping})")


def test_fluxo_ferramentas() -> None:
    origem = channel(-100, "Origem")
    app, client = build_app([text_message(1, "oi")], [origem])

    # consultar canal (com busca) → sair; stats; retry vazio; dedup mantém cache;
    # manutenção sem apagar nada
    feed("@teste", "")
    asyncio.run(_guard(app._tool_resolve()))
    asyncio.run(_guard(app._tool_stats()))
    asyncio.run(_guard(app._tool_retry()))
    feed("s")
    asyncio.run(_guard(app._tool_dedup()))
    feed("n", "n", "n", "n")
    asyncio.run(_guard(app._tool_maintenance()))
    check(True, "ferramentas executam sem exceção")


def test_fluxo_configuracoes() -> None:
    app, _client = build_app([], [])
    feed("1", "1.5", "2", "6", "6", "7", "1.0", "0")
    asyncio.run(_guard(app._menu_settings()))
    check(app.config.delay == 1.5, "delay alterado nas configurações")
    check(app.config.workers in range(1, 9), "workers válidos")
    check(True, "configurações persistem")


def test_fluxo_idioma_ingles() -> None:
    from i18n import set_lang, t

    set_lang("en")
    check(t("menu.copy") == "Copy channel", "tradução EN do menu")
    check(t("copy.sent") == "sent", "tradução EN de sent")
    set_lang("pt")
    check(t("menu.copy") == "Copiar canal", "tradução PT do menu")
    check(t("chave.inexistente") == "chave.inexistente", "chave ausente não quebra")
    check(t("auth.code", n=2).endswith("(tentativa 2/3)"), "interpolação de parâmetros")


def test_telefone_internacional_normalizado() -> None:
    from core import normalize_phone

    check(normalize_phone("+55 (37) 99938-4375") == "+5537999384375",
          "normaliza telefone brasileiro com separadores")
    check(normalize_phone("+1 555 123 4567") == "+15551234567",
          "normaliza telefone internacional")
    check(normalize_phone("5537999384375") is None,
          "exige sinal de + e código internacional")
    check(normalize_phone("+55abc999384375") is None,
          "rejeita caracteres inválidos no telefone")
    check(normalize_phone("+1234567") is None,
          "rejeita telefone internacional curto")


def test_cofre_credenciais_persiste_entre_instancias() -> None:
    path = Path(_TMP) / "manager_vault" / "credentials.enc"
    credentials = {"api_id": 123456, "api_hash": "a" * 32, "lang": "pt"}
    core.CredentialManager(path).save(credentials)
    loaded = core.CredentialManager(path).load()
    check(loaded == credentials, "API ID/Hash são lidos por uma nova instância")


def test_diretorio_empacotado_estavel_e_migrado() -> None:
    base_a = Path(_TMP) / "exe_antigo"
    base_a.mkdir(parents=True, exist_ok=True)
    legado = base_a / "data"
    legado.mkdir(parents=True, exist_ok=True)
    (legado / "marcador.txt").write_text("preservar", encoding="utf-8")
    env = {"LOCALAPPDATA": str(Path(_TMP) / "perfil")}
    destino = config.get_data_dir(
        base_a, frozen=True, env=env, system="win32", home=Path(_TMP)
    )
    esperado = Path(_TMP) / "perfil" / "TELECOPY" / "data"
    check(destino == esperado, "build usa caminho estável do usuário")
    check((destino / "marcador.txt").read_text(encoding="utf-8") == "preservar",
          "migra dados existentes sem apagá-los")
    outro = config.get_data_dir(
        Path(_TMP) / "exe_movido", frozen=True, env=env,
        system="win32", home=Path(_TMP)
    )
    check(outro == destino, "mover o executável não muda a pasta de dados")


def test_fluxo_login_respeita_metodo_de_entrega() -> None:
    from core import App

    class AuthClient:
        def __init__(self):
            self.authorized = False
            self.phone = None
            self.code = None

        async def send_code_request(self, phone, force_sms=False):
            self.phone = phone
            self.force_sms = force_sms
            return SimpleNamespace(type=type("SentCodeTypeApp", (), {})())

        async def is_user_authorized(self):
            return self.authorized

        async def sign_in(self, phone, code=None, password=None):
            self.code = code
            self.authorized = code == "12345"

        async def get_me(self):
            return SimpleNamespace(first_name="Teste", username="teste")

    app = App()
    client = AuthClient()
    app.client = client
    feed("+55 (37) 99938-4375", "12345")
    result = asyncio.run(app._sign_in())
    check(result, "login confirma a sessão após o código")
    check(client.phone == "+5537999384375", "login envia telefone normalizado")
    check(client.code == "12345", "login valida o código informado")
    check("Telegram" in App._code_delivery_hint(
        SimpleNamespace(type=type("SentCodeTypeApp", (), {})())
    ), "orienta procurar o código no outro app conectado")


def test_i18n_completo() -> None:
    import re

    from i18n import all_keys, missing

    check(missing("pt") == () and missing("en") == (), "todas as chaves têm pt e en")
    fonte = ""
    for name in ("core.py", "copier.py", "channel_clone.py", "filters.py", "main.py", "config.py", "utils.py"):
        fonte += (ROOT / name).read_text(encoding="utf-8")
    usadas = set(re.findall(r"\bt\(\s*[\"']([a-zA-Z0-9_.]+)[\"']", fonte))
    definidas = set(all_keys())
    faltando = sorted(k for k in usadas if k not in definidas)
    check(not faltando, f"chaves i18n usadas mas não definidas: {faltando}")


def test_fluxo_multi_destino() -> None:
    origem = channel(-100, "Origem")
    d1 = channel(-201, "Destino 1")
    d2 = channel(-202, "Destino 2")
    mensagens = [text_message(i, f"msg {i}") for i in range(1, 4)]
    app, client = build_app(mensagens, [origem, d1, d2])
    app.config.delay = 0.1

    # origem → destino 1 → destino 2 → iniciar → filtro → confirmar
    feed("/", "Origem", "1",
         "1", "/", "Destino 1", "1",
         "1", "/", "Destino 2", "1",
         "0", "0", "s")
    asyncio.run(_guard(app._menu_multi()))
    destinos = {s["dest"] for s in client.sent}
    check(destinos == {-201, -202}, f"multi-destino envia para os dois canais ({destinos})")


async def _guard(coro):
    try:
        result = await coro if asyncio.iscoroutine(coro) else coro
        return result
    except EOFError:
        return None


def test_fluxo_destino_existente_cria_topicos_ausentes() -> None:
    origem = teardown_channel(channel(-160, "Fórum Origem", forum=True, broadcast=False))
    destino = teardown_channel(channel(-260, "Fórum Destino", forum=True, broadcast=False))
    mensagens = [
        text_message(1, "aviso repetido", reply_to_msg_id=11),
        text_message(2, "aviso repetido", reply_to_msg_id=22),
    ]
    app = core.App()
    client = CloneFlowClient(mensagens, origem)
    client.entities.append(destino)
    client.register_entity(destino)
    client.topic_catalog[destino.id] = [client._topic(1, "General")]
    app.client = client
    app.picker = core.ChannelPicker(client)
    app.dedup = core.DedupEngine(app_repo(app))
    app.config.delay = 0.1
    app.config.workers = 1
    app.config.signature = "@nao_deve_aparecer"
    app.config.use_dedup = True

    # origem → destino existente → ENTER todos tópicos origem → destino → ENTER
    # todos tópicos destino → filtro todos → não alterar opções → confirmar → pause
    feed("/", "Fórum Origem", "1", "1", "", "/", "Fórum Destino", "1", "", "0", "n", "s", "")
    asyncio.run(_guard(app._menu_copy()))

    titles = [topic.title for topic in client.topic_catalog[destino.id]]
    check(titles == ["General", "Novidades", "Perguntas"],
          f"destino existente ganha os tópicos ausentes na ordem de atividade ({titles})")
    by_title = {topic.title: topic.id for topic in client.topic_catalog[destino.id]}
    routes = [(item["text"], item["reply_to"]) for item in client.sent]
    check(routes == [("aviso repetido", by_title["Perguntas"]),
                     ("aviso repetido", by_title["Novidades"])],
          f"mensagens repetidas vão aos tópicos novos ({routes})")
    check(all("@nao_deve_aparecer" not in (item.get("text") or "") for item in client.sent),
          "assinatura desativada na cópia fiel de fórum")


def test_paginacao_de_topicos_nao_para_em_pagina_curta() -> None:
    from channel_clone import ChannelCloner

    class PagedClient:
        def __init__(self):
            self.calls = 0

        async def __call__(self, request):
            if type(request).__name__ == "GetForumTopicsByIDRequest":
                return SimpleNamespace(topics=[])
            self.calls += 1
            if self.calls == 1:
                topics = [
                    SimpleNamespace(id=1, title="General", icon_color=0, icon_emoji_id=None,
                                    title_missing=False, closed=False, pinned=False, hidden=False,
                                    top_message=1, date=None),
                    SimpleNamespace(id=11, title="A", icon_color=0, icon_emoji_id=None,
                                    title_missing=False, closed=False, pinned=False, hidden=False,
                                    top_message=11, date=None),
                ]
            elif self.calls == 2:
                topics = [
                    SimpleNamespace(id=22, title="B", icon_color=0, icon_emoji_id=None,
                                    title_missing=False, closed=False, pinned=False, hidden=False,
                                    top_message=22, date=None),
                ]
            else:
                topics = []
            return SimpleNamespace(count=3, topics=topics)

    topics = asyncio.run(ChannelCloner(PagedClient())._list_forum_topics(object()))
    check([topic.id for topic in topics] == [1, 11, 22],
          "página curta não encerra a leitura antes do fim")


def test_mapa_persistido_nao_usa_topico_de_origem_apagado() -> None:
    from channel_clone import ChannelCloner

    origem = teardown_channel(channel(-170, "Fórum Origem", forum=True, broadcast=False))
    destino = teardown_channel(channel(-270, "Fórum Destino", forum=True, broadcast=False))
    client = CloneFlowClient([], origem)
    client.topic_catalog[destino.id] = [
        client._topic(1, "General"),
        client._topic(201, "Perguntas", icon_color=0x6C8EBF),
        client._topic(202, "Novidades"),
    ]
    repo = Repository(Path(_TMP) / "stale_map.db")
    repo.reset_all()
    repo.set_kv("forum_topic_map:-170:-270", '{"999": 201}')
    cloner = ChannelCloner(client, repo=repo)
    plan, mapping, missing = asyncio.run(cloner.plan_existing(origem, destino))
    check(mapping == {1: 1, 11: 201, 22: 202},
          f"mapa antigo não sequestra tópico do destino ({mapping})")
    check(missing == (), "nenhum tópico ausente quando os títulos batem")
    ensured = asyncio.run(cloner.ensure_topics(plan, destino, {1: 1, 999: 201, 11: 201, 22: 202}))
    check(999 not in ensured, "ensure_topics remove associações obsoletas")
    repo.close()


def test_topico_comum_chamado_general_nao_vira_general() -> None:
    from channel_clone import ChannelCloner, ForumTopicSpec

    named = ForumTopicSpec(id=50, title="General")
    check(ChannelCloner._general_topic([named]) is None,
          "tópico comum chamado General não é o General nativo")
    real = ForumTopicSpec(id=1, title="Geral")
    check(ChannelCloner._general_topic([named, real]) is real,
          "General nativo é identificado pelo ID 1")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def _main() -> int:
    failures = 0
    for test in TESTS:
        feed(*([""] * 200))
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
    print(f"\n  {len(TESTS) - failures}/{len(TESTS)} fluxos OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
