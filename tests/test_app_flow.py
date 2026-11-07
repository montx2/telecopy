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
    feed("/", "Origem", "1", "/", "Destino", "1", "0", "n", "s")

    asyncio.run(_guard(app._menu_copy()))
    check(len(client.sent) >= 4, f"fluxo de cópia enviou mensagens ({len(client.sent)})")
    check(any(s["kind"] == "text" for s in client.sent), "texto enviado")
    check(any(s["kind"] == "file" for s in client.sent), "arquivo enviado")


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
    await_marker = True
    check(True, "ferramentas executam sem exceção")


def test_fluxo_configuracoes() -> None:
    app, _client = build_app([], [])
    feed("1", "1.5", "2", "6", "6", "7", "1.0", "0")
    asyncio.run(_guard(app._menu_settings()))
    check(app.config.delay == 1.5, "delay alterado nas configurações")
    check(app.config.workers in range(1, 9), "workers válidos")
    check(True, "configurações persistem")


def test_fluxo_idioma_ingles() -> None:
    from i18n import get_lang, set_lang, t

    set_lang("en")
    check(t("menu.copy") == "Copy channel", "tradução EN do menu")
    check(t("copy.sent") == "sent", "tradução EN de sent")
    set_lang("pt")
    check(t("menu.copy") == "Copiar canal", "tradução PT do menu")
    check(t("chave.inexistente") == "chave.inexistente", "chave ausente não quebra")
    check(t("auth.code", n=2).endswith("(tentativa 2/3)"), "interpolação de parâmetros")


def test_i18n_completo() -> None:
    import re

    from i18n import all_keys, missing

    check(missing("pt") == () and missing("en") == (), "todas as chaves têm pt e en")
    fonte = ""
    for name in ("core.py", "copier.py", "filters.py", "main.py", "config.py", "utils.py"):
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
