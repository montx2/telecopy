"""Testes do modo "Clone automático" e da criação automática de destino.

O cliente simulado reaproveita o fórum de ``test_forum_copy`` e acrescenta o
que o clone usa: foto de perfil, proteção de conteúdo e exclusão do canal
criado. Nada aqui toca em ``data/``: tudo roda em diretório temporário.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="telecopy_autoclone_")

import config  # noqa: E402

config.DATA_DIR = Path(_TMP)
config.TEMP_DIR = config.DATA_DIR / "tmp"
config.LOGS_DIR = config.DATA_DIR / "logs"
config.VAULT_DIR = config.DATA_DIR / "vault"
config.EXPORT_DIR = config.DATA_DIR / "exports"
config.BACKUP_DIR = config.DATA_DIR / "backups"
config.SESSION_FILE = config.DATA_DIR / "session"
config.DB_FILE = config.DATA_DIR / "clone.db"
config.STATS_FILE = config.DATA_DIR / "stats.json"
config.DAILY_FILE = config.DATA_DIR / "daily.json"
config.LOG_FILE = config.LOGS_DIR / "telecopy.log"
config.VAULT_FILE = config.VAULT_DIR / "credentials.enc"
config.init_dirs()

import core  # noqa: E402
from telethon.tl import types as tl  # noqa: E402
from tests.fake_telegram import channel, text_message  # noqa: E402
from tests.test_forum_copy import (  # noqa: E402
    ForumClient,
    forum,
    full_forum,
    topic_message,
)

CREATED_ID = -5000


class ChatAdminRequiredError(Exception):
    """Imita telethon.errors.ChatAdminRequiredError (o nome é o que importa)."""


class CloneClient(ForumClient):
    """ForumClient com foto, proteção e exclusão do canal criado."""

    def __init__(self, messages, entities, catalog=None) -> None:
        super().__init__(messages, entities, catalog)
        # Entidades falsas precisam passar no isinstance(Channel) do Telethon.
        for entity in self.entities:
            if not isinstance(entity, (tl.Channel, tl.Chat)):
                entity.__class__ = tl.Channel
        self.deleted: list = []
        self.photo_requests: list = []
        self.toggles: list = []
        self.protected_calls: list = []
        self.photo_available = True
        self.photo_error: Exception | None = None
        self.uploaded: list = []
        self.fail_topic_at = None      # índice (1-based) do tópico que falha
        self.topic_attempts = 0

    async def __call__(self, request):
        name = type(request).__name__
        if name == "CreateChannelRequest":
            response = await super().__call__(request)
            # Quem cria o canal é o dono: necessário para validar o reuso.
            self.created.creator = True
            self.created.broadcast = bool(request.broadcast)
            return response
        if name == "CreateForumTopicRequest":
            self.topic_attempts += 1
            if self.fail_topic_at and self.topic_attempts >= self.fail_topic_at:
                self.fail_topic_at = None
                # Erro "fatal": não é problema de ícone, então não é repetido.
                raise ChatAdminRequiredError("falha simulada ao criar tópico")
            return await super().__call__(request)
        if name == "DeleteChannelRequest":
            self.deleted.append(int(request.channel.channel_id))
            self.entities = [e for e in self.entities
                             if int(getattr(e, "id", 0)) != int(request.channel.channel_id)]
            return True
        if name == "EditPhotoRequest":
            self.photo_requests.append(request)
            self.created.photo = getattr(request, "photo", None)
            return True
        if name == "ToggleNoForwardsRequest":
            self.protected_calls.append(request)
            self._entities[str(getattr(request.peer, "id", ""))].noforwards = True
            return True
        return await super().__call__(request)

    async def download_profile_photo(self, entity, file=None, **_):
        if self.photo_error is not None:
            raise self.photo_error
        if not self.photo_available:
            return None
        with open(str(file), "wb") as handle:
            handle.write(b"photo")
        self.photo_path = str(file)
        return str(file)

    async def upload_file(self, path, file_name=None, **_):
        self.uploaded.append(str(path))
        return f"upload:{Path(path).name}"


# ── utilidades de fluxo ─────────────────────────────────────────────────────

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


def build_app(client) -> core.App:
    app = core.App()
    app.client = client
    app.picker = core.ChannelPicker(client)
    core.db.reset_all()
    app.dedup = core.DedupEngine(core.db)
    app.config.delay = 0.1
    app.config.workers = 1
    app.config.signature = ""
    app.config.use_dedup = True
    core.prefs.set("clone_copy_photo", True)
    core.prefs.set("clone_start_copy", True)
    core.prefs.set("clone_suffix", " (Clone)")
    return app


def check(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def channel_request(client):
    return next(req for req in client.requests_log
                if type(req).__name__ == "CreateChannelRequest")


def clone_kv(source_id: int):
    raw = core.db.get_kv(f"auto_clone:{source_id}")
    return json.loads(raw) if raw else None


# ── 1. Canal, grupo e fórum criados automaticamente ─────────────────────────

def test_clone_automatico_cria_canal_de_transmissao() -> None:
    origem = channel(-1100, "Canal Origem")
    client = CloneClient([text_message(1, "olá"), text_message(2, "tchau")], [origem])
    app = build_app(client)

    # origem → confirmação única → ENTER do relatório
    feed("/", "Canal Origem", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    request = channel_request(client)
    check(request.title == "Canal Origem (Clone)", "título com sufixo configurável")
    check(request.broadcast and not request.megagroup and not request.forum,
          "canal de transmissão → canal de transmissão")
    check(len(client.sent) == 2, f"copiou as mensagens ({len(client.sent)})")
    check(clone_kv(origem.id)["dest_id"] == CREATED_ID,
          "registro auto_clone gravado com o destino criado")


def test_clone_automatico_cria_grupo() -> None:
    origem = channel(-1200, "Grupo Origem", broadcast=False)
    client = CloneClient([text_message(1, "oi")], [origem])
    app = build_app(client)

    feed("/", "Grupo Origem", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    request = channel_request(client)
    check(request.megagroup and not request.broadcast and not request.forum,
          "grupo → supergrupo sem fórum")
    check(len(client.sent) == 1, "copiou a mensagem do grupo")


def test_clone_automatico_cria_forum_com_topicos() -> None:
    origem = forum(-1300, "Fórum Origem")
    client = CloneClient(
        [topic_message(2, "aviso", 41), topic_message(3, "promo", 42)],
        [origem], {origem.id: full_forum(origem.id)},
    )
    app = build_app(client)

    feed("/", "Fórum Origem", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    request = channel_request(client)
    check(request.forum and request.megagroup, "fórum → fórum com tópicos")
    # GetForumTopics devolve do mais recente para o mais antigo.
    titles = [topic.title for topic in client.catalog[CREATED_ID]]
    check(titles == ["SUPORTE", "PROMOÇÕES", "AVISOS", "General"],
          f"todos os tópicos recriados ({titles})")
    check([req.title for req in client.create_topic_requests]
          == ["AVISOS", "PROMOÇÕES", "SUPORTE"],
          "tópicos criados na ordem de atividade, do mais antigo ao mais novo")
    check(len(client.sent) >= 2, "copiou as mensagens nos tópicos")
    sent_topics = {s.get("reply_to") for s in client.sent if s.get("reply_to")}
    check(len(sent_topics) >= 1, "mensagens roteadas para o tópico equivalente")


# ── 2. Idempotência: reutilizar ou criar outro ──────────────────────────────

def test_clone_automatico_reutiliza_clone_existente() -> None:
    origem = channel(-1400, "Canal Origem")
    anterior = channel(CREATED_ID, "Canal Origem (Clone)")
    anterior.creator = True
    client = CloneClient([text_message(1, "seguir")], [origem, anterior])
    app = build_app(client)
    core.db.set_kv(f"auto_clone:{origem.id}",
                   json.dumps({"dest_id": anterior.id, "criado_em": "2026-11-01T10:00:00"}))

    # origem → reutilizar → confirmação → ENTER
    feed("/", "Canal Origem", "1", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(not any(type(r).__name__ == "CreateChannelRequest"
                  for r in client.requests_log),
          "não cria um segundo canal quando o clone existe")
    check(client.sent and int(client.sent[0]["dest"]) == anterior.id,
          "copia para o clone existente")


def test_clone_automatico_cria_outro_quando_escolhido() -> None:
    origem = channel(-1500, "Canal Origem")
    anterior = channel(CREATED_ID, "Canal Origem (Clone)")
    anterior.creator = True
    client = CloneClient([text_message(1, "de novo")], [origem, anterior])
    app = build_app(client)
    core.db.set_kv(f"auto_clone:{origem.id}", json.dumps({"dest_id": anterior.id}))

    # origem → criar outro → confirmação → ENTER
    feed("/", "Canal Origem", "1", "2", "s", "")
    asyncio.run(guard(app._menu_clone()))

    titles = [r.title for r in client.requests_log
              if type(r).__name__ == "CreateChannelRequest"]
    check(titles == ["Canal Origem (Clone)"], "cria um destino novo quando pedido")
    check(clone_kv(origem.id)["dest_id"] == CREATED_ID,
          "o registro passa a apontar para o clone novo")


def test_clone_registrado_sem_admin_nao_e_reutilizado() -> None:
    origem = channel(-1600, "Canal Origem")
    alheio = channel(CREATED_ID, "Clone antigo")
    alheio.creator = False
    client = CloneClient([text_message(1, "x")], [origem, alheio])
    client.is_admin = False          # get_permissions confirma que não somos admin
    app = build_app(client)
    core.db.set_kv(f"auto_clone:{origem.id}", json.dumps({"dest_id": alheio.id}))

    # Sem admin, o clone antigo é descartado: segue direto para a confirmação.
    feed("/", "Canal Origem", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(any(type(r).__name__ == "CreateChannelRequest"
              for r in client.requests_log),
          "cria um destino quando não é admin do clone registrado")


# ── 3. Falha parcial: continuar ou excluir só o criado agora ────────────────

def test_falha_parcial_permite_continuar_no_destino_criado() -> None:
    origem = forum(-1700, "Fórum Origem")
    client = CloneClient([topic_message(2, "aviso", 41)],
                         [origem], {origem.id: full_forum(origem.id)})
    client.fail_topic_at = 1
    app = build_app(client)

    # origem → confirmação → continuar → confirmação do plano → ENTER
    feed("/", "Fórum Origem", "1", "s", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(clone_kv(origem.id) is not None,
          "registro gravado antes dos tópicos, mesmo com falha parcial")
    check(any(type(r).__name__ == "CreateForumTopicRequest"
              for r in client.requests_log), "recomeça a criação dos tópicos")
    check(client.sent, "a cópia continua no destino criado")


def test_falha_parcial_exclui_apenas_o_canal_criado() -> None:
    origem = forum(-1800, "Fórum Origem")
    antigo = forum(-2500, "Destino antigo")
    client = CloneClient([topic_message(2, "aviso", 41)],
                         [origem, antigo], {origem.id: full_forum(origem.id)})
    client.fail_topic_at = 1
    app = build_app(client)

    # origem → confirmação → excluir → confirma a exclusão → ENTER
    feed("/", "Fórum Origem", "1", "s", "2", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(client.deleted == [CREATED_ID],
          f"apaga somente o canal criado nesta execução ({client.deleted})")
    check(antigo.id not in client.deleted, "nunca apaga chats alheios")
    check(clone_kv(origem.id) is None, "registro do clone excluído é removido")
    check(not client.sent, "nada é copiado quando o destino é excluído")


# ── 4. Extras: foto e proteção de conteúdo ──────────────────────────────────

def test_foto_copiada_para_o_novo_destino() -> None:
    origem = channel(-1900, "Canal Origem")
    client = CloneClient([text_message(1, "x")], [origem])
    app = build_app(client)

    feed("/", "Canal Origem", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(client.photo_requests and client.uploaded, "foto enviada no EditPhoto")
    check(not Path(client.photo_path).exists(), "temporário da foto removido")


def test_foto_com_falha_apenas_avisa() -> None:
    origem = channel(-1950, "Canal Origem")
    client = CloneClient([text_message(1, "x"), text_message(2, "y")], [origem])
    client.photo_error = OSError("sem foto")
    app = build_app(client)

    feed("/", "Canal Origem", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(len(client.sent) == 2, f"a cópia continua sem a foto ({len(client.sent)})")
    check(not client.photo_requests, "nenhuma foto é aplicada quando falha")


def test_origem_protegida_aplica_toggle_no_destino() -> None:
    origem = channel(-1970, "Canal Protegido")
    origem.noforwards = True
    client = CloneClient([text_message(1, "x")], [origem])
    app = build_app(client)
    app.config.forward_mode = True

    feed("/", "Canal Protegido", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(len(client.protected_calls) == 1, "ToggleNoForwards aplicado no destino")
    check(not client.forwarded, "usa reenvio em vez de forward")


def test_clone_sem_iniciar_copia_cria_so_a_estrutura() -> None:
    origem = forum(-1980, "Fórum Origem")
    client = CloneClient([topic_message(2, "aviso", 41)],
                         [origem], {origem.id: full_forum(origem.id)})
    app = build_app(client)
    core.prefs.set("clone_start_copy", False)

    feed("/", "Fórum Origem", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(any(type(r).__name__ == "CreateChannelRequest"
              for r in client.requests_log), "o destino é criado")
    check(not client.sent, "a cópia não começa com a preferência desligada")


# ── 5. [A] Criar destino automaticamente no seletor ─────────────────────────

def test_picker_sem_chats_oferece_criacao_automatica() -> None:
    client = CloneClient([], [])
    build_app(client)
    picker = core.ChannelPicker(client, allow_create=True)

    feed("s")
    result = asyncio.run(picker.pick("Destino"))
    check(result is core.AUTO_CREATE, "lista vazia oferece criar o destino")


def test_picker_tecla_a_devolve_pedido_de_criacao() -> None:
    client = CloneClient([], [channel(-1990, "Algum chat")])
    build_app(client)
    picker = core.ChannelPicker(client)

    feed("a")
    result = asyncio.run(picker.pick("Destino", allow_create=True))
    check(result is core.AUTO_CREATE, "[A] devolve o pedido de criação")


def test_copiar_canal_cria_destino_pelo_picker() -> None:
    origem = channel(-1995, "Canal Origem")
    client = CloneClient([text_message(1, "oi")], [origem])
    app = build_app(client)

    # origem → destino existente → [A] no seletor → filtro → opções →
    # confirmação → ENTER do relatório
    feed("/", "Canal Origem", "1", "1", "a", "", "n", "s", "")
    asyncio.run(guard(app._menu_copy()))

    check(any(type(r).__name__ == "CreateChannelRequest"
              for r in client.requests_log),
          "Copiar canal cria o destino pelo seletor")
    check(len(client.sent) == 1, "copia para o destino recém-criado")


def test_multi_destino_aceita_destino_criado_na_hora() -> None:
    origem = channel(-2100, "Canal Origem")
    existente = channel(-2200, "Destino existente")
    client = CloneClient([text_message(1, "oi"), text_message(2, "tudo bem")],
                         [origem, existente])
    app = build_app(client)

    # origem → adicionar destino pela lista → [A] → começar → filtro →
    # confirmar → ENTER
    feed("/", "Canal Origem", "1", "1", "a", "0", "", "s", "")
    asyncio.run(guard(app._menu_multi()))

    check(any(type(r).__name__ == "CreateChannelRequest"
              for r in client.requests_log),
          "multi-destino cria o destino escolhido com [A]")
    destinos = {s["dest"] for s in client.sent}
    check(CREATED_ID in destinos, "copiou para o destino recém-criado")
    check(existente.id not in destinos, "não copiou para o destino não escolhido")


def test_espelho_aceita_destino_criado_na_hora() -> None:
    origem = channel(-2300, "Canal Origem")
    client = CloneClient([text_message(1, "oi")], [origem])
    app = build_app(client)

    # origem → seletor de destino → [A] → filtro → confirmação → ENTER
    feed("/", "Canal Origem", "1", "a", "", "s", "")
    asyncio.run(guard(app._menu_mirror()))

    check(any(type(r).__name__ == "CreateChannelRequest"
              for r in client.requests_log),
          "modo espelho cria o destino escolhido com [A]")
    check(clone_kv(origem.id)["dest_id"] == CREATED_ID,
          "registro do clone gravado no modo espelho")


# ── 6. Configurações do clone ───────────────────────────────────────────────

def test_configuracoes_do_clone() -> None:
    client = CloneClient([], [])
    app = build_app(client)

    # sufixo → foto (desliga) → copiar após criar (desliga) → sair
    feed("a", "[clone]", "f", "k", "0")
    asyncio.run(guard(app._menu_settings()))

    check(core.prefs.clone_suffix == "[clone]", "sufixo salvo nas preferências")
    check(core.prefs.clone_copy_photo is False, "copiar foto desligado")
    check(core.prefs.clone_start_copy is False, "copiar após criar desligado")

    # O sufixo entra no título sugerido pelo cloner.
    from channel_clone import ChannelCloner

    origem = channel(-2400, "Canal Origem")
    check(ChannelCloner.default_title(origem, core.prefs.clone_suffix)
          == "Canal Origem [clone]", "sufixo aplicado com o espaço automático")
    check(ChannelCloner.default_title(origem, "") == "Canal Origem",
          "sem sufixo o título fica igual ao da origem")
    longo = channel(-2401, "x" * 130)
    titulo = ChannelCloner.default_title(longo, " (Clone)")
    check(len(titulo.encode("utf-16-le")) // 2 == 128,
          "título respeita o limite de 128 unidades UTF-16")


def test_sufixo_longo_demais_e_recusado_na_hora() -> None:
    client = CloneClient([], [])
    app = build_app(client)
    anterior = core.prefs.clone_suffix

    # Um sufixo que não cabe no título é recusado antes de qualquer clone.
    feed("a", "z" * 80, "0")
    asyncio.run(guard(app._menu_settings()))
    check(core.prefs.clone_suffix == anterior,
          "sufixo impossível não é salvo")


def test_clone_existente_sem_copiar_avisa_reuso() -> None:
    origem = channel(-2500, "Canal Origem")
    anterior = channel(CREATED_ID, "Canal Origem (Clone)")
    anterior.creator = True
    client = CloneClient([text_message(1, "x")], [origem, anterior])
    app = build_app(client)
    core.db.set_kv(f"auto_clone:{origem.id}", json.dumps({"dest_id": anterior.id}))
    core.prefs.set("clone_start_copy", False)

    # origem → reutilizar → confirmação → ENTER
    feed("/", "Canal Origem", "1", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(not client.sent, "não copia com a preferência desligada")
    check(not any(type(r).__name__ == "CreateChannelRequest"
                  for r in client.requests_log), "reaproveita sem criar outro")


# ── 7. Auditoria: casos de borda encontrados na simulação ───────────────────

def test_kv_corrompido_nao_impede_o_clone() -> None:
    for raw in ("{lixo", "5", "[]", '{"dest_id": "abc"}', '{"dest_id": null}'):
        origem = channel(-3100, "Canal Audit")
        client = CloneClient([text_message(1, "x")], [origem])
        app = build_app(client)
        core.db.set_kv(f"auto_clone:{origem.id}", raw)

        feed("1", "s", "")
        asyncio.run(guard(app._menu_clone()))

        check(any(type(r).__name__ == "CreateChannelRequest"
                  for r in client.requests_log),
              f"registro corrompido ({raw!r}) não impede criar o destino")
        check(clone_kv(origem.id)["dest_id"] == CREATED_ID,
              f"registro corrompido ({raw!r}) é substituído pelo novo")


def test_reuso_usa_permissoes_da_api_quando_faltam_flags() -> None:
    origem = channel(-3200, "Canal Origem")
    antigo = channel(CREATED_ID, "Canal Origem (Clone)")
    # A entidade relida não traz creator/admin_rights: só get_permissions sabe.
    antigo.creator = False
    client = CloneClient([text_message(1, "x")], [origem, antigo])
    client.is_admin = True
    app = build_app(client)
    core.db.set_kv(f"auto_clone:{origem.id}", json.dumps({"dest_id": antigo.id}))

    feed("1", "1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(not any(type(r).__name__ == "CreateChannelRequest"
                  for r in client.requests_log),
          "reutiliza o clone quando a API confirma que somos admin")
    check({s["dest"] for s in client.sent} == {antigo.id},
          "copia para o clone registrado")


def test_reuso_descarta_registro_de_chat_apagado() -> None:
    origem = channel(-3300, "Canal Origem")
    client = CloneClient([text_message(1, "x")], [origem])
    app = build_app(client)
    core.db.set_kv(f"auto_clone:{origem.id}",
                   json.dumps({"dest_id": -999999, "criado_em": "2026-01-01"}))

    feed("1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(any(type(r).__name__ == "CreateChannelRequest"
              for r in client.requests_log),
          "chat apagado não bloqueia a criação de um clone novo")
    check(clone_kv(origem.id)["dest_id"] == CREATED_ID,
          "registro órfão é substituído")


def test_multi_retoma_destino_com_falha_parcial() -> None:
    origem = forum(-3400, "Fórum Origem")
    client = CloneClient([topic_message(2, "aviso", 41)], [origem],
                         {origem.id: full_forum(origem.id)})
    client.fail_topic_at = 1
    app = build_app(client)

    # origem → todos os tópicos → lista → [A] → começar → filtro → confirmar →
    # continuar no destino criado → ENTER
    feed("1", "", "1", "a", "0", "", "s", "1", "")
    asyncio.run(guard(app._menu_multi()))

    check([req.title for req in client.create_topic_requests]
          == ["AVISOS", "PROMOÇÕES", "SUPORTE"],
          "multi recria os tópicos que faltaram")
    check({s["dest"] for s in client.sent} == {CREATED_ID},
          "multi copia para o destino retomado")


def test_espelho_exclui_destino_com_falha_parcial() -> None:
    origem = forum(-3500, "Fórum Origem")
    client = CloneClient([topic_message(2, "aviso", 41)], [origem],
                         {origem.id: full_forum(origem.id)})
    client.fail_topic_at = 1
    app = build_app(client)

    # origem → todos os tópicos → [A] → filtro → confirmar → excluir → confirmar
    feed("1", "", "a", "", "s", "2", "s", "")
    asyncio.run(guard(app._menu_mirror()))

    check(client.deleted == [CREATED_ID],
          f"espelho apaga somente o canal criado nesta execução ({client.deleted})")
    check(clone_kv(origem.id) is None, "registro do clone excluído é removido")


def test_clone_sem_sufixo_usa_o_titulo_da_origem() -> None:
    origem = channel(-3600, "Canal Origem")
    client = CloneClient([text_message(1, "x")], [origem])
    app = build_app(client)
    core.prefs.set("clone_suffix", "")

    feed("1", "s", "")
    asyncio.run(guard(app._menu_clone()))

    check(channel_request(client).title == "Canal Origem",
          "sem sufixo o destino repete o título da origem")


def test_sufixo_longo_demais_e_recusado() -> None:
    client = CloneClient([], [])
    app = build_app(client)
    anterior = core.prefs.clone_suffix
    try:
        feed("a", "x" * 80, "0")
        asyncio.run(guard(app._menu_settings()))
        check(core.prefs.clone_suffix == anterior,
              "sufixo maior que o limite não é salvo")
    finally:
        core.prefs.set("clone_suffix", anterior)


def test_jornada_pelo_menu_principal() -> None:
    """[4] clona, [6] abre as configurações e [0] encerra o programa."""
    origem = channel(-3700, "Canal Origem")
    client = CloneClient([text_message(1, "oi")], [origem])
    app = build_app(client)
    app._running = True
    antes = core.prefs.clone_copy_photo

    # [4] → origem → confirmar → ENTER do relatório → [6] → [f] → [0] → [0]
    feed("4", "1", "s", "", "6", "f", "0", "0")
    asyncio.run(guard(app._main_menu()))

    check(app._running is False, "[0] encerra o menu principal")
    check(any(type(r).__name__ == "CreateChannelRequest"
              for r in client.requests_log), "[4] é o clone automático")
    check(len(client.sent) == 1, "a cópia do clone rodou pelo menu")
    check(core.prefs.clone_copy_photo is not antes,
          "[6] abre as configurações e [f] alterna a foto")
    core.prefs.set("clone_copy_photo", antes)
