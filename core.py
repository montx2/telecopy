"""
core.py — Núcleo da aplicação: autenticação, navegação e menus.

Estrutura
    App.start()          → idioma, splash, login, menu principal
    ChannelPicker        → seletor paginado de canais com busca
    _menu_*()            → fluxos de cópia, multi-destino, espelho
    _tool_*()            → ferramentas de diagnóstico e manutenção
    _menu_settings()     → preferências
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.types import Channel, Chat

import app_meta as _app
from config import (
    APP_AUTHOR,
    BACKUP_DIR,
    C,
    DAILY_LIMIT,
    DATA_DIR,
    EXPORT_DIR,
    LOG_FILE,
    MAX_CONNECTIONS_PER_FILE,
    MAX_TOTAL_CONNECTIONS_CAP,
    SESSION_FILE,
    TEMP_DIR,
    VAULT_FILE,
    WORKERS_HARD_CAP,
    RunConfig,
    ui,
)
from database import db
from dedup import DedupEngine
from filters import get_name as filter_name
from filters import list_filters, resolve as resolve_filter
from i18n import get_lang, set_lang, t
from prefs import prefs
from retry_queue import RetryQueue
from security import Vault
from utils import Fmt, Stats, daily_limit, logger

PAGE_SIZE = 9

AUTO_CREATE = object()
"""Retorno do picker quando o usuário escolhe [A] Criar destino automaticamente."""

CLONE_SUFFIX_MAX = 60
"""Sufixo do clone: deixa pelo menos ~68 unidades para o título da origem."""


@dataclass
class _ForumSetup:
    """Estrutura de tópicos planejada para um destino da cópia."""

    dest: Any
    dest_topic: Optional[int] = None
    cloner: Any = None
    plan: Any = None
    topic_map: Optional[Dict[int, int]] = None
    missing: Tuple[str, ...] = ()

    @property
    def faithful(self) -> bool:
        """True quando cada mensagem será roteada para o tópico equivalente."""
        return bool(self.topic_map)

    def titles(self) -> Optional[Dict[int, str]]:
        if self.plan is None:
            return None
        return {1: "General", **{topic.id: topic.title for topic in self.plan.topics}}


def normalize_phone(raw: str) -> Optional[str]:
    """Normaliza telefone internacional sem adivinhar nem remover o DDI."""
    if not isinstance(raw, str):
        return None
    value = re.sub(r"[\s().-]", "", raw.strip())
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
        return None
    return value


# ==============================================================================
# Seletor de canais
# ==============================================================================


class ChannelPicker:
    """Lista paginada dos canais/grupos da conta, com busca por nome."""

    def __init__(self, client: TelegramClient, allow_create: bool = False) -> None:
        self.client = client
        self.allow_create = allow_create
        self._items: List[Dict[str, Any]] = []
        self._loaded = False

    # -- carregamento -------------------------------------------------------

    async def load(self, force: bool = False) -> List[Dict[str, Any]]:
        if self._loaded and not force:
            return self._items
        ui.clear()
        print(f"\n  {C.CYAN}·{C.RST}  {t('picker.loading')}…", end="", flush=True)
        items: List[Dict[str, Any]] = []
        try:
            async for dialog in self.client.iter_dialogs():
                ent = dialog.entity
                if not isinstance(ent, (Channel, Chat)):
                    continue
                is_forum = bool(getattr(ent, "forum", False))
                is_broadcast = isinstance(ent, Channel) and bool(getattr(ent, "broadcast", False))
                kind = ("picker.type_forum" if is_forum
                        else "picker.type_channel" if is_broadcast
                        else "picker.type_group")
                items.append({
                    "entity": ent,
                    "name": getattr(ent, "title", None) or str(ent.id),
                    "id": int(ent.id),
                    "type": t(kind),
                    "members": getattr(ent, "participants_count", None),
                    "forum": is_forum,
                })
        except Exception as exc:  # pragma: no cover - depende da rede
            logger.error(f"Falha ao listar diálogos: {exc}")
        items.sort(key=lambda x: x["name"].casefold())
        self._items = items
        self._loaded = True
        print(f"\r  {C.GREEN}✓{C.RST}  {len(items)} {t('picker.found')}                    ")
        if not items:
            ui.warn(t("picker.none"))
        return items

    # -- render -------------------------------------------------------------

    def _render(self, rows: List[Dict[str, Any]], page: int, pages: int,
                query: str, highlight_id: Optional[int],
                allow_create: bool = False) -> None:
        ui.clear()
        subtitle = f"{t('picker.page')} {page + 1}/{pages}"
        if query:
            subtitle = f"{t('picker.search')}: \"{query}\"  ·  {subtitle}"
        ui.header(t("picker.title"), subtitle)

        header = (f"  {C.DARK}{'#':<6}{t('picker.header_type'):<10}"
                  f"{t('picker.header_id'):<15}{t('picker.header_members'):<10}"
                  f"{t('picker.header_name')}{C.RST}")
        print(header)
        print(f"  {C.DARK}{'─' * ui.W}{C.RST}")

        for offset, item in enumerate(rows):
            number = page * PAGE_SIZE + offset + 1
            is_source = highlight_id is not None and item["id"] == highlight_id
            color = C.GREEN if is_source else C.VIOLET
            members = Fmt.number(item["members"]) if item["members"] else "—"
            name = item["name"][:32]
            if is_source:
                name += f"  {t('picker.source_hl')}"
            print(
                f"  {C.AMBER}[{number:>2}]{C.RST} "
                f"{color}{item['type']:<10}{C.RST}"
                f"{C.GRAY}{str(item['id']):<15}{C.RST}"
                f"{C.SILVER}{members:<10}{C.RST}"
                f"{C.WHITE}{name}{C.RST}"
            )

        print(f"  {C.DARK}{'─' * ui.W}{C.RST}")
        nav = []
        if page > 0:
            nav.append(f"{C.PURPLE}[P]{C.RST} {t('picker.prev')}")
        if page < pages - 1:
            nav.append(f"{C.PURPLE}[N]{C.RST} {t('picker.next')}")
        nav.append(f"{C.CYAN}[/]{C.RST} {t('picker.search_btn')}")
        if allow_create:
            nav.append(f"{C.GREEN}[A]{C.RST} {t('picker.auto_create_btn')}")
        nav.append(f"{C.GRAY}[0]{C.RST} {t('menu.back')}")
        print(f"\n  {'   '.join(nav)}\n")

    # -- seleção ------------------------------------------------------------

    async def _fresh(self, item: Dict[str, Any]):
        """Rebusca a entidade selecionada; o cache pode ter o flag forum velho."""
        try:
            entity = await self.client.get_entity(int(item["id"]))
        except Exception as exc:
            logger.warning(f"Entidade {item.get('id')} não pôde ser relida: {exc}")
            return item["entity"]
        if entity is not None:
            item["entity"] = entity
            forum = bool(getattr(entity, "forum", False))
            item["forum"] = forum
            if forum:
                item["type"] = t("picker.type_forum")
            elif isinstance(entity, Channel) and bool(getattr(entity, "broadcast", False)):
                item["type"] = t("picker.type_channel")
            else:
                item["type"] = t("picker.type_group")
            return entity
        return item["entity"]

    async def pick(self, label: str = "", highlight_id: Optional[int] = None,
                   allow_create: Optional[bool] = None):
        allow = self.allow_create if allow_create is None else bool(allow_create)
        items = await self.load()
        if not items and allow:
            if ui.confirm(t("picker.auto_create_empty"), default=True):
                return AUTO_CREATE
            return None
        if not items:
            ui.pause()
            return None

        query = ""
        page = 0
        while True:
            subset = (
                [i for i in items if query.casefold() in i["name"].casefold()]
                if query else items
            )
            pages = max(1, math.ceil(len(subset) / PAGE_SIZE))
            page = max(0, min(page, pages - 1))
            rows = subset[page * PAGE_SIZE: (page + 1) * PAGE_SIZE]

            self._render(rows, page, pages, query, highlight_id, allow)
            raw = ui.ask(label or t("picker.select"), required=False).lower()

            if raw in ("0", ""):
                return None
            if raw == "a" and allow:
                return AUTO_CREATE
            if raw == "n" and page < pages - 1:
                page += 1
                continue
            if raw == "p" and page > 0:
                page -= 1
                continue
            if raw == "/":
                query = ui.ask(t("picker.search_prompt"), required=False)
                page = 0
                continue
            if raw.isdigit():
                index = int(raw) - 1
                if 0 <= index < len(subset):
                    return await self._fresh(subset[index])
            ui.warn(t("picker.invalid"))
            time.sleep(0.6)


# ==============================================================================
# Credenciais
# ==============================================================================


class CredentialManager:
    """Acesso ao cofre cifrado de credenciais, com leitura após gravação."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._vault = Vault(path or VAULT_FILE)

    @property
    def path(self) -> Path:
        return self._vault.path

    def load(self) -> Optional[Dict[str, Any]]:
        data = self._vault.load()
        if not isinstance(data, dict):
            return None
        try:
            api_id = int(data.get("api_id", 0))
            api_hash = str(data.get("api_hash", "")).strip()
        except (TypeError, ValueError):
            return None
        if api_id <= 0 or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            return None
        return {**data, "api_id": api_id, "api_hash": api_hash}

    def save(self, data: Dict[str, Any]) -> None:
        self._vault.save(data)
        if self._vault.load() != data:
            raise RuntimeError("as credenciais gravadas não passaram na verificação de leitura")

    def update(self, **kwargs: Any) -> Dict[str, Any]:
        data = self._vault.load() or {}
        data.update(kwargs)
        self.save(data)
        return data

    def exists(self) -> bool:
        return self._vault.exists()

    def readable(self) -> bool:
        return self._vault.is_readable()

    def clear(self) -> None:
        self._vault.delete()


# ==============================================================================
# Aplicação
# ==============================================================================


class App:
    def __init__(self) -> None:
        self.client: Optional[TelegramClient] = None
        self.creds = CredentialManager()
        self.stats = Stats()
        self.config: RunConfig = prefs.config
        self.prefs = prefs
        if not self.prefs.get("auto_profile_done", False):
            # Autoconfiguração: aplica uma vez o perfil recomendado para este PC
            # (só campos de hardware; modo de envio, dedup etc. não são tocados).
            try:
                from config import recommended_settings

                for key, value in recommended_settings().items():
                    setattr(self.config, key, value)
                self.config.__post_init__()
                self.prefs.save_config(self.config)
            except Exception as exc:  # nunca impede o programa de abrir
                logger.warning(f"Autoconfiguração de hardware ignorada: {exc}")
            self.prefs.set("auto_profile_done", True)
        self.retry = RetryQueue(db)
        self.dedup = DedupEngine(db, enabled=True)
        self.picker: Optional[ChannelPicker] = None
        self.me = None
        self._trail: List[str] = [t("app.tagline")]
        self._running = True

    # ── ciclo de vida ───────────────────────────────────────────────────────

    async def start(self) -> None:
        self._load_preferences()
        ui.splash(
            _app.__version__,
            f"{t('app.tagline')}  ·  v{_app.__version__}",
            t("app.tech"),
        )
        ui.loading_bar(t("init.loading"))
        self.stats.new_session()
        from copier import sweep_temp

        removed = sweep_temp()
        if removed:
            logger.info(f"Temporários antigos removidos na inicialização: {removed}")
        logger.info(f"Sessão iniciada — v{_app.__version__} [{get_lang()}]")

        if not self.prefs.get("onboarding_done"):
            self._onboarding()

        if not await self._authenticate():
            ui.blank()
            ui.err(t("auth.fail"))
            ui.pause()
            return

        self.picker = ChannelPicker(self.client)
        if self.prefs.get("check_updates"):
            await self._check_updates()
        await self._main_menu()
        await self._shutdown()

    def _load_preferences(self) -> None:
        """Idioma salvo (prefs → cofre) ou pergunta na primeira execução."""
        lang = self.prefs.language
        if lang is None:
            creds = self.creds.load() or {}
            lang = creds.get("lang")
        if lang in ("pt", "en"):
            set_lang(lang)
        else:
            lang = self._ask_language()
            set_lang(lang)
        self.prefs.set_language(lang)

    def _ask_language(self) -> str:
        ui.clear()
        ui.blank()
        print(f"  {C.ORANGE}{C.BOLD}TELECOPY{C.RST}  {C.GRAY}v{_app.__version__}{C.RST}\n")
        print(f"  {C.SILVER}{t('lang.welcome')}{C.RST}\n")
        print(f"  {C.AMBER}[1]{C.RST}  {C.WHITE}{t('lang.pt')}{C.RST}")
        print(f"  {C.AMBER}[2]{C.RST}  {C.WHITE}{t('lang.en')}{C.RST}\n")
        for _ in range(5):
            raw = (ui.ask(t("lang.select"), required=False) or "1").strip()
            if raw in ("1", "pt"):
                return "pt"
            if raw in ("2", "en"):
                return "en"
            ui.warn(t("menu.invalid"))
        return "pt"

    def _onboarding(self) -> None:
        ui.clear()
        ui.header(f"{t('onboard.title')} — {_app.__version__}", t("onboard.subtitle"))
        steps = [
            (t("onboard.step1.title"), t("onboard.step1.body")),
            (t("onboard.step2.title"), t("onboard.step2.body")),
            (t("onboard.step3.title"), t("onboard.step3.body")),
        ]
        for index, (title, body) in enumerate(steps, start=1):
            print(f"  {C.PURPLE}{C.BOLD}{index}.  {title}{C.RST}")
            for line in self._wrap(body, 70):
                print(f"      {C.SILVER}{line}{C.RST}")
            print()
        print(f"  {C.CYAN}·{C.RST}  {t('onboard.tip')}")
        ui.pause(t("onboard.continue"))
        self.prefs.set("onboarding_done", True)

    @staticmethod
    def _wrap(text: str, width: int = 70) -> List[str]:
        lines, current = [], ""
        for word in str(text).split():
            if len(current) + len(word) + 1 > width:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
        return lines

    async def _shutdown(self) -> None:
        ui.clear()
        ui.blank()
        ui.info(t("shutdown.saving"))
        self.stats.save()
        self.prefs.save_config(self.config)
        ui.info(t("shutdown.db"))
        try:
            db.close()
        except Exception:
            pass
        if self.client is not None:
            ui.info(t("shutdown.disc"))
            try:
                await self.client.disconnect()
            except Exception:
                pass
        ui.blank()
        ui.ok(t("shutdown.bye"))
        ui.blank()

    # ── autenticação ────────────────────────────────────────────────────────

    @staticmethod
    async def _network_ok(host: str = "149.154.167.50", port: int = 443,
                          timeout: float = 5.0) -> bool:
        """Teste rápido de alcance aos servidores do Telegram."""
        import socket

        def probe() -> bool:
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except OSError:
                return False

        try:
            return await asyncio.to_thread(probe)
        except Exception:
            return False

    async def _authenticate(self) -> bool:
        creds = self.creds.load()

        if creds and creds.get("api_id") and creds.get("api_hash"):
            api_id = int(creds["api_id"])
            api_hash = str(creds["api_hash"])
        else:
            if creds is None and self.creds.exists():
                ui.warn(t("auth.credentials_recovered", path=self.creds.path))
                ui.pause()
            pair = self._ask_credentials()
            if not pair:
                return False
            api_id, api_hash = pair
            try:
                self.creds.save({
                    "api_id": api_id,
                    "api_hash": api_hash,
                    "lang": get_lang(),
                })
                ui.ok(t("auth.creds_saved"))
                ui.info(t("auth.credentials_location", path=self.creds.path))
            except Exception as exc:
                # A falha de disco não impede o usuário de tentar conectar
                # nesta execução, mas deixa claro que o próximo início pedirá
                # as credenciais de novo.
                logger.error(f"Falha ao salvar o cofre de credenciais: {exc}")
                ui.warn(t(
                    "auth.credentials_save_fail",
                    path=self.creds.path,
                    reason=str(exc) or type(exc).__name__,
                ))

        if not await self._network_ok():
            ui.blank()
            ui.err(t("auth.conn_failed"))
            ui.info(t("auth.conn_hint"))
            logger.error("Sem conexão com os servidores do Telegram")
            ui.pause()
            return False

        for attempt in range(1, 3):
            try:
                self.client = TelegramClient(
                    str(SESSION_FILE), api_id, api_hash,
                    device_model=f"TELECOPY {_app.__version__}",
                    system_version=platform.platform()[:60],
                    app_version=_app.__version__,
                    connection_retries=1,
                    retry_delay=0,
                    request_retries=5,
                    # timeout do TelegramClient é timeout de conexão no Telethon;
                    # mantido em 12 s. Requisições de parte do caminho rápido usam
                    # PART_TIMEOUT=30 s (fasttransfer.py) sem mudar a conexão.
                    timeout=12,
                    auto_reconnect=True,
                )
                # timeout defensivo: nunca deixa a interface travada
                await asyncio.wait_for(self.client.connect(), timeout=45)
                break
            except Exception as exc:
                logger.error(f"Conexão (tentativa {attempt}/2): {exc}")
                reason = (t("auth.conn_timeout") if isinstance(exc, asyncio.TimeoutError)
                          else str(exc)) or t("err.generic")
                ui.blank()
                ui.err(f"{t('auth.conn_failed')}: {reason}")
                ui.info(t("auth.conn_hint"))
                if attempt == 2 or not ui.confirm(t("auth.try_again")):
                    return False
                ui.blank()
        else:  # pragma: no cover - laço sempre termina em break/return
            return False

        if await self.client.is_user_authorized():
            self.me = await self.client.get_me()
            label = getattr(self.me, "first_name", None) or getattr(self.me, "username", "")
            ui.ok(f"{t('auth.signed_in')} {C.WHITE}{label}{C.RST}")
            await asyncio.sleep(0.5)
            return True

        return await self._sign_in()

    def _ask_credentials(self) -> Optional[Tuple[int, str]]:
        ui.clear()
        ui.header(t("auth.first_time.title"), t("auth.first_time.subtitle"))
        print(f"  {C.AMBER}{C.BOLD}{t('auth.creds_guide.title')}{C.RST}\n")
        for step in t("auth.creds_guide.steps").split("|"):
            print(f"  {C.CYAN}›{C.RST}  {C.SILVER}{step.strip()}{C.RST}")
        print()
        ui.divider()
        ui.blank()

        for _ in range(3):
            raw_id = ui.ask(t("auth.api_id"))
            if raw_id.isdigit() and int(raw_id) > 0:
                break
            ui.err(t("auth.api_id_invalid"))
        else:
            return None
        api_id = int(raw_id)

        for _ in range(3):
            api_hash = ui.ask(t("auth.api_hash")).strip()
            if re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
                break
            ui.err(t("auth.api_hash_invalid"))
        else:
            return None

        return api_id, api_hash

    @staticmethod
    def _code_delivery_hint(sent_code: Any) -> str:
        """Traduz o método de entrega escolhido pelo Telegram para uma dica."""
        code_type = getattr(sent_code, "type", None)
        kind = type(code_type).__name__
        if kind == "SentCodeTypeApp":
            return t("auth.code_delivery_app")
        if kind in {"SentCodeTypeSms", "SentCodeTypeFirebaseSms", "SentCodeTypeFragmentSms"}:
            return t("auth.code_delivery_sms")
        if kind in {"SentCodeTypeCall", "SentCodeTypeFlashCall", "SentCodeTypeMissedCall"}:
            return t("auth.code_delivery_call")
        if "Email" in kind:
            return t("auth.code_delivery_email")
        return t("auth.code_delivery_other")

    def _report_code_request_error(self, exc: Exception) -> None:
        """Mostra falhas comuns sem exigir que o usuário decifre erro RPC."""
        name = type(exc).__name__
        logger.error(f"Falha ao solicitar código do Telegram ({name}): {exc}")
        if isinstance(exc, FloodWaitError):
            ui.warn(t("auth.flood", sec=int(exc.seconds)))
            ui.info(t("auth.code_flood"))
        elif isinstance(exc, PhoneNumberInvalidError):
            ui.err(t("auth.phone_invalid"))
        elif isinstance(exc, asyncio.TimeoutError):
            ui.err(t("auth.code_send_timeout"))
        elif name in {"ApiIdInvalidError", "ApiIdPublishedFloodError"}:
            ui.err(t("auth.code_api_invalid"))
        elif name in {"PhoneNumberBannedError", "UserDeactivatedBanError"}:
            ui.err(t("auth.code_phone_banned"))
        elif name in {"PhoneNumberFloodError", "PhoneCodeFloodError"}:
            ui.warn(t("auth.code_flood"))
        else:
            detail = str(exc).strip() or name
            ui.err(f"{t('auth.code_send_fail')}: {detail}")

    async def _finish_sign_in(self) -> bool:
        self.me = await self.client.get_me()
        label = getattr(self.me, "first_name", None) or getattr(self.me, "username", "")
        ui.ok(f"{t('auth.signed_in')} {C.WHITE}{label}{C.RST}")
        await asyncio.sleep(0.6)
        return True

    async def _sign_in(self) -> bool:
        ui.clear()
        ui.header(t("auth.sign_in.title"), t("auth.sign_in.subtitle"))

        phone = None
        for _ in range(3):
            phone = normalize_phone(ui.ask(t("auth.phone")))
            if phone:
                break
            ui.err(t("auth.phone_invalid"))
        else:
            return False

        ui.info(t("auth.sending_code"))
        try:
            sent_code = await asyncio.wait_for(
                self.client.send_code_request(phone), timeout=45
            )
        except Exception as exc:
            # Telethon pode concluir o login automaticamente em um fluxo raro.
            completed_inline = (
                isinstance(exc, RuntimeError)
                and "logged in right after" in str(exc).lower()
            )
            if completed_inline:
                try:
                    if await self.client.is_user_authorized():
                        return await self._finish_sign_in()
                except Exception:
                    pass
            self._report_code_request_error(exc)
            return False

        ui.ok(t("auth.request_accepted"))
        ui.info(self._code_delivery_hint(sent_code))
        ui.info(t("auth.code_hint"))

        # Alguns fluxos do Telegram podem concluir a autorização sem pedir
        # um código digitado, então confirme antes de mostrar o prompt.
        if await self.client.is_user_authorized():
            return await self._finish_sign_in()

        for attempt in range(1, 4):
            ui.blank()
            code = re.sub(r"\s+", "", ui.ask(t("auth.code", n=attempt)))
            try:
                await self.client.sign_in(phone, code)
                break
            except SessionPasswordNeededError:
                ui.blank()
                ui.warn(t("auth.2fa_hint"))
                password = ui.secret(t("auth.2fa"))
                try:
                    await self.client.sign_in(password=password)
                    break
                except Exception as exc:
                    logger.error(f"Falha na senha 2FA ({type(exc).__name__}): {exc}")
                    ui.err(f"{t('auth.2fa_fail')}: {exc}")
                    return False
            except PhoneCodeInvalidError:
                ui.warn(t("auth.code_wrong"))
            except PhoneCodeExpiredError:
                ui.warn(t("auth.code_expired"))
                try:
                    # O Telethon reconhece o hash da solicitação anterior e
                    # pede um novo código sem criar outra autenticação.
                    sent_code = await asyncio.wait_for(
                        self.client.send_code_request(phone), timeout=45
                    )
                    ui.ok(t("auth.request_accepted"))
                    ui.info(self._code_delivery_hint(sent_code))
                except Exception as exc:
                    self._report_code_request_error(exc)
                    return False
            except FloodWaitError as exc:
                self._report_code_request_error(exc)
                return False
            except Exception as exc:
                logger.error(f"Falha ao validar o código ({type(exc).__name__}): {exc}")
                ui.err(str(exc) or type(exc).__name__)
                return False
        else:
            ui.err(t("auth.too_many"))
            return False

        return await self._finish_sign_in()

    async def _check_updates(self) -> None:
        """Aviso opcional de versão nova (silencioso se sem internet)."""
        import json
        import urllib.request

        def fetch() -> Optional[str]:
            try:
                request = urllib.request.Request(
                    "https://api.github.com/repos/montx2/telecopy/releases/latest",
                    headers={"Accept": "application/vnd.github+json",
                             "User-Agent": "TELECOPY"},
                )
                with urllib.request.urlopen(request, timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return str(data.get("tag_name") or "").lstrip("vV") or None
            except Exception:
                return None

        try:
            latest = await asyncio.to_thread(fetch)
        except Exception:
            return
        if latest and self._version_tuple(latest) > self._version_tuple(_app.__version__):
            ui.blank()
            ui.warn(f"Nova versão disponível: v{latest} (instalada: v{_app.__version__})")
            ui.info("https://github.com/montx2/telecopy/releases")
            ui.blank()

    @staticmethod
    def _version_tuple(value: str) -> Tuple[int, ...]:
        parts = re.findall(r"\d+", str(value))
        return tuple(int(p) for p in parts[:4]) or (0,)

    # ── menu principal ──────────────────────────────────────────────────────

    async def _main_menu(self) -> None:
        while self._running:
            ui.clear()
            ui.header(
                "TELECOPY",
                f"v{_app.__version__}  ·  {t('app.by')} {APP_AUTHOR}  ·  "
                f"{getattr(self.me, 'first_name', '') or ''}",
            )
            self._limit_banner()
            ui.menu([
                ("1", t("menu.copy"), t("menu.copy.desc")),
                ("2", t("menu.multi"), t("menu.multi.desc")),
                ("3", t("menu.mirror"), t("menu.mirror.desc")),
                ("4", t("menu.clone"), t("menu.clone.desc")),
                ("5", t("menu.tools"), t("menu.tools.desc")),
                ("6", t("menu.settings"), t("menu.settings.desc")),
                ("0", t("menu.exit"), ""),
            ])
            choice = (ui.ask(t("menu.option"), required=False) or "0").strip()
            try:
                if choice == "1":
                    await self._menu_copy()
                elif choice == "2":
                    await self._menu_multi()
                elif choice == "3":
                    await self._menu_mirror()
                elif choice == "4":
                    await self._menu_clone()
                elif choice == "5":
                    await self._menu_tools()
                elif choice == "6":
                    await self._menu_settings()
                elif choice == "0":
                    self._running = False
                elif choice:
                    ui.warn(t("menu.invalid"))
                    time.sleep(0.6)
            except KeyboardInterrupt:
                ui.warn(t("copy.cancelled"))
                await asyncio.sleep(0.6)
            except Exception as exc:  # nunca derruba o programa por um menu
                logger.exception("Erro em menu")
                ui.err(f"{t('err.generic')}: {exc}")
                ui.pause()

    def _limit_banner(self) -> None:
        cap = int(getattr(self.config, "daily_limit", DAILY_LIMIT))
        level, count, remaining = daily_limit.status(cap)
        if level == "limit":
            ui.warn(t("limit.reached", count=count, limit=cap))
        elif level == "warn":
            ui.warn(t("limit.warn", count=count, limit=cap, remaining=remaining))

    # ── navegação ───────────────────────────────────────────────────────────

    def _push(self, name: str) -> None:
        if not self._trail or self._trail[-1] != name:
            self._trail.append(name)

    def _pop(self) -> None:
        if len(self._trail) > 1:
            self._trail.pop()

    def _trail_line(self) -> None:
        if len(self._trail) > 1:
            sep = f" {C.DARK}›{C.RST} "
            print(f"  {C.DARK}" + sep.join(self._trail) + C.RST)
            ui.blank()

    # ── seleção de entidades ────────────────────────────────────────────────

    async def _pick_entity(self, label: str, hint: str,
                           highlight_id: Optional[int] = None):
        ui.info(hint)
        ui.menu([
            ("1", t("picker.from_list"), t("picker.from_list.desc")),
            ("2", t("picker.manual"), t("picker.manual.desc")),
            ("0", t("menu.back"), ""),
        ])
        mode = (ui.ask(t("picker.how"), required=False) or "0").strip()

        if mode == "0":
            return None
        if mode == "1":
            return await self.picker.pick(label, highlight_id=highlight_id)
        if mode == "2":
            raw = ui.ask(f"{label} (@usuario, link ou ID)")
            if not raw:
                return None
            return await self._resolve_entity(raw)
        ui.warn(t("menu.invalid"))
        return None

    async def _resolve_entity(self, raw: str):
        """Aceita @user, t.me/..., ID numérico e links privados t.me/c/..."""
        value = raw.strip()
        try:
            if re.fullmatch(r"-?\d+", value):
                return await self.client.get_entity(int(value))
            return await self.client.get_entity(value)
        except Exception as exc:
            logger.warning(f"Entidade não encontrada ({value}): {exc}")
            ui.err(f"{t('entity.not_found')}")
            ui.pause()
            return None

    async def _refresh_entity(self, entity):
        """Relê a entidade antes de decidir se é fórum (o cache pode enganar)."""
        if entity is None:
            return None
        try:
            fresh = await self.client.get_entity(int(getattr(entity, "id", 0) or 0))
        except Exception as exc:
            logger.warning(
                f"Entidade {getattr(entity, 'id', '?')} não pôde ser relida: {exc}"
            )
            return entity
        return fresh if fresh is not None else entity

    async def _resolve_topic(self, entity, for_destination: bool = False
                             ) -> Optional[int]:
        """Retorna o id do tópico (ou None para todos/estrutura completa)."""
        if not getattr(entity, "forum", False):
            return None
        ui.blank()
        ui.warn(t("picker.forum_warn"))
        if for_destination:
            ui.info(t("picker.topic_full_hint"))
            prompt = t("picker.topic_prompt_dest")
        else:
            prompt = t("picker.topic_prompt")
        raw = ui.ask(prompt, required=False)
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        match = re.search(r"t\.me/(?:c/)?[^/]+/(?:(\d+)/)?(\d+)", raw)
        if match:
            return int(match.group(1) or match.group(2))
        ui.err(t("picker.topic_invalid"))
        return None

    async def _offer_enable_forum(self, dest) -> bool:
        """Supergrupo sem tópicos: oferece ativar o modo fórum em vez de falhar."""
        if getattr(dest, "broadcast", False):
            return False
        ui.warn(t("copy.clone.destination_not_forum"))
        if not ui.confirm(t("copy.clone.enable_forum_prompt")):
            return False
        permissions = None
        try:
            me = await self.client.get_me()
            permissions = await self.client.get_permissions(dest, me)
        except Exception as exc:
            logger.warning(f"Permissões do destino não puderam ser conferidas: {exc}")
        if permissions is not None and not getattr(permissions, "is_admin", False):
            ui.err(t("copy.clone.enable_forum_needs_admin"))
            return False
        from channel_clone import ChannelCloner

        try:
            await ChannelCloner(self.client, repo=db).enable_forum(dest)
        except Exception as exc:
            logger.exception(f"Falha ao ativar tópicos no destino: {exc}")
            ui.err(t("copy.clone.enable_forum_failed", reason=exc))
            return False
        self.picker._loaded = False
        ui.ok(t("copy.clone.enable_forum_done"))
        return True

    async def _plan_forum_structure(self, source, dest,
                                    source_topic: Optional[int],
                                    dest_topic: Optional[int],
                                    require_forum: bool = True
                                    ) -> Optional[_ForumSetup]:
        """Planeja os tópicos quando a origem é um fórum inteiro.

        Devolve None se o usuário recusar mandar tudo para um tópico só.
        ``require_forum=False`` (multi-destino e espelho) mantém a cópia
        sequencial em um destino que não seja fórum, sem abortar o fluxo.
        """
        from channel_clone import ChannelCloneError, ChannelCloner

        source = await self._refresh_entity(source)
        dest = await self._refresh_entity(dest)
        setup = _ForumSetup(dest=dest, dest_topic=dest_topic)
        if not getattr(source, "forum", False) or source_topic:
            return setup
        if not getattr(dest, "forum", False):
            if not await self._offer_enable_forum(dest):
                if require_forum:
                    raise ChannelCloneError(
                        t("copy.clone.destination_forum_required")
                    )
                ui.warn(t("copy.clone.sequential_copy"))
                return setup
            setup.dest = await self._refresh_entity(setup.dest)
        if setup.dest_topic:
            ui.warn(t("copy.clone.single_topic_warn"))
            if not ui.confirm(t("copy.clone.single_topic_confirm")):
                return None
            return setup
        cloner = ChannelCloner(self.client, repo=db)
        plan, topic_map, missing = await cloner.plan_existing(source, setup.dest)
        setup.cloner = cloner
        setup.plan = plan
        setup.topic_map = dict(topic_map)
        setup.missing = missing
        setup.dest_topic = topic_map.get(1)
        ui.info(t("copy.clone.map_topics"))
        await self._offer_checkpoint_reset(source, setup.dest)
        return setup

    @staticmethod
    def _setup_label(setup: "_ForumSetup") -> str:
        """Rótulo do destino, inclusive quando ele ainda será criado."""
        if setup.dest is not None:
            return str(getattr(setup.dest, "title", None) or getattr(setup.dest, "id", "?"))
        if setup.plan is not None:
            return str(setup.plan.title)
        return "?"

    async def _apply_forum_structure(self, setup: _ForumSetup) -> None:
        """Cria o destino ou completa os tópicos e deixa o mapa pronto."""
        if setup.plan is None or setup.cloner is None:
            return
        cloner = setup.cloner
        if setup.plan.existing:
            ui.info(t("copy.clone.preparing_existing"))
            setup.topic_map = await cloner.ensure_topics(
                setup.plan, setup.dest, setup.topic_map or {}
            )
            ui.ok(t("copy.clone.ready_existing"))
        else:
            ui.info(t("copy.clone.creating"))
            plan = setup.plan
            setup.dest, setup.topic_map = await cloner.create(
                plan, on_created=lambda dest: self._remember_clone(plan.source, dest),
            )
            self.picker._loaded = False
            ui.ok(t("copy.clone.created", title=setup.plan.title,
                    id=getattr(setup.dest, "id", "?")))
            self._show_entity(setup.dest, t("entity.dest"))
            # Foto e proteção são acessórios: falhar aqui não aborta a cópia.
            await self._clone_extras(setup.plan.source, setup.dest)
        setup.dest_topic = (setup.topic_map or {}).get(1)
        for warning in cloner.warnings:
            ui.warn(warning)
        cloner.warnings.clear()

    async def _offer_checkpoint_reset(self, source, dest) -> None:
        """Checkpoints antigos sem tópico fariam a retomada pular mensagens."""
        source_id = int(getattr(source, "id", 0) or 0)
        dest_id = int(getattr(dest, "id", 0) or 0)
        try:
            pending = db.untopiced_checkpoints(source_id, dest_id)
        except Exception as exc:
            logger.warning(f"Checkpoints antigos não puderam ser conferidos: {exc}")
            return
        if not pending:
            return
        ui.warn(t("copy.clone.legacy_checkpoints", count=pending))
        if not ui.confirm(t("copy.clone.legacy_reset_prompt")):
            return
        try:
            removed = db.reset_pair(source_id, dest_id)
            ui.ok(t("copy.clone.legacy_reset_done", count=removed))
        except Exception as exc:
            logger.exception(f"Falha ao zerar checkpoints do par: {exc}")
            ui.err(t("copy.clone.legacy_reset_failed", reason=exc))

    def _mirror_topic_creator(self, source, setup: _ForumSetup):
        """Callback do espelho: cria no destino um tópico novo da origem."""
        from channel_clone import ForumTopicSpec

        async def ensure(msg) -> int:
            topic_id = int(getattr(msg, "id", 0) or 0)
            action = getattr(msg, "action", None)
            if type(action).__name__ == "MessageActionTopicCreate":
                spec = ForumTopicSpec(
                    id=topic_id,
                    title=str(getattr(action, "title", "") or "")[:128],
                    icon_color=getattr(action, "icon_color", None),
                    icon_emoji_id=getattr(action, "icon_emoji_id", None),
                    title_missing=bool(getattr(action, "title_missing", False)),
                )
            else:
                listing = await setup.cloner.list_topics(source)
                spec = next((item for item in listing if item.id == topic_id), None)
                if spec is None:
                    raise RuntimeError(t("copy.topic_unmapped", topic=topic_id))
            created = await setup.cloner.ensure_topic(
                setup.plan, setup.dest, spec, setup.topic_map or {}
            )
            for warning in setup.cloner.warnings:
                ui.warn(warning)
            setup.cloner.warnings.clear()
            ui.ok(t("mirror.topic_created", topic=spec.title))
            return created

        return ensure

    async def _pick_filter(self) -> Optional[str]:
        ui.clear()
        ui.header(t("filter.title"))
        self._trail_line()
        items = [("0", t("filter.all"), t("filter.all.desc"))]
        next_number = 1
        for code, name, desc in list_filters():
            if code == "all":
                continue
            items.append((str(next_number), name, desc))
            next_number += 1
        ui.menu(items)
        ui.info(t("filter.back_hint"))
        ui.blank()
        choice = (ui.ask(t("filter.default"), required=False) or "0").strip()
        if choice in ("", "0"):
            return "all"
        code = resolve_filter(choice)
        if not code:
            ui.warn(t("menu.invalid"))
            return "all"
        self.prefs.set("last_filter", code)
        return code

    # ── resumo de entidade ──────────────────────────────────────────────────

    def _show_entity(self, entity, label: str) -> None:
        rows = [
            (t("entity.name"), getattr(entity, "title", None) or str(entity.id)),
            (t("entity.id"), str(entity.id)),
            (t("entity.type"), type(entity).__name__),
        ]
        username = getattr(entity, "username", None)
        if username:
            rows.append((t("entity.username"), f"@{username}"))
        members = getattr(entity, "participants_count", None)
        if members:
            rows.append((t("entity.members"), Fmt.number(members)))
        ui.box(label, rows)

    # ── fluxo: copiar canal ─────────────────────────────────────────────────

    async def _menu_copy(self) -> None:
        self._push(t("menu.copy"))
        ui.clear()
        ui.header(t("menu.copy"))
        self._trail_line()

        source = await self.picker.pick(t("entity.source"))
        if source is None:
            self._pop()
            return
        self._show_entity(source, t("entity.source"))

        ui.menu([
            ("1", t("copy.dest.existing"), t("copy.dest.existing.desc")),
            ("2", t("copy.dest.create"), t("copy.dest.create.desc")),
            ("0", t("menu.back"), ""),
        ])
        destination_mode = (ui.ask(t("copy.dest.mode"), required=False) or "1").strip()
        if destination_mode not in {"1", "2"}:
            self._pop()
            return

        source_topic: Optional[int] = None
        setup: Optional[_ForumSetup] = None

        if destination_mode == "1":
            source_topic = await self._resolve_topic(source)
            dest = None
            for _ in range(3):
                candidate = await self.picker.pick(
                    t("entity.dest"), highlight_id=source.id, allow_create=True
                )
                if candidate is None:
                    self._pop()
                    return
                if candidate is AUTO_CREATE:
                    # [A] no seletor: cria um destino novo a partir da origem,
                    # mantendo o tópico que o usuário já escolheu (se escolheu).
                    setup = await self._prepare_auto_clone(source)
                    if setup is None:
                        self._pop()
                        return
                    break
                if candidate.id == source.id:
                    ui.err(t("entity.same"))
                    ui.pause()
                    ui.clear()
                    ui.header(t("menu.copy"))
                    self._trail_line()
                    self._show_entity(source, t("entity.source"))
                    continue
                dest = candidate
                break
            if setup is None:
                if dest is None:
                    self._pop()
                    return
                dest_topic = await self._resolve_topic(dest, for_destination=True)
                from channel_clone import ChannelCloneError

                try:
                    setup = await self._plan_forum_structure(
                        source, dest, source_topic, dest_topic
                    )
                except ChannelCloneError as exc:
                    logger.exception(f"Falha ao planejar a estrutura do destino: {exc}")
                    ui.err(t("copy.clone.failed", reason=str(exc)))
                    ui.pause()
                    self._pop()
                    return
                if setup is None:
                    # O usuário recusou enviar o fórum inteiro para um único tópico.
                    ui.info(t("copy.cancelled"))
                    self._pop()
                    return
                self._show_entity(setup.dest, t("entity.dest"))
        else:
            from channel_clone import ChannelCloneError, ChannelCloner

            cloner = ChannelCloner(self.client, repo=db)
            default_title = cloner.default_title(source, self.prefs.clone_suffix)
            while True:
                entered_title = ui.ask(
                    t("copy.clone.title_prompt", title=default_title), required=False
                )
                clone_title = entered_title or default_title
                title_units = len(clone_title.encode("utf-16-le")) // 2
                if 1 <= title_units <= 128:
                    break
                ui.warn(t("copy.clone.title_invalid"))
            try:
                clone_plan = await cloner.prepare(source, clone_title)
            except ChannelCloneError as exc:
                logger.exception(f"Falha ao preparar a estrutura do destino: {exc}")
                ui.err(t("copy.clone.failed", reason=str(exc)))
                ui.pause()
                self._pop()
                return
            for warning in clone_plan.warnings:
                ui.warn(t("copy.clone.about_warning", reason=warning))
            setup = _ForumSetup(
                dest=None, cloner=cloner, plan=clone_plan,
                topic_map={} if clone_plan.forum else None,
            )

        filter_code = await self._pick_filter() or "all"
        if filter_code != "all" and not ui.confirm(t("copy.filter_warning"), default=False):
            self._pop()
            return
        config = self._copy_options()
        if getattr(source, "noforwards", False) and config.forward_mode:
            config.forward_mode = False
            ui.info(t("copy.source_protected"))
        if setup.faithful or (setup.plan is not None and setup.plan.forum):
            # Repetições entre tópicos e respostas são conteúdo legítimo.
            # Uma assinatura alteraria o texto original; hashes globais podem
            # descartar a segunda ocorrência de um aviso em outro tópico.
            config.use_dedup = False
            config.signature = ""
            ui.info(t("copy.clone.faithful_forum"))

        if setup.plan is not None and setup.plan.existing:
            rows = [
                (t("entity.source"), getattr(source, "title", str(source.id))),
                (t("entity.dest"), getattr(setup.dest, "title", str(setup.dest.id))),
                (t("entity.type"), t("copy.clone.kind.forum")),
                (t("copy.clone.existing_topics"), str(len(setup.topic_map or {}))),
                (t("copy.clone.missing_topics"), str(len(setup.missing))),
            ]
        elif setup.plan is not None:
            kind = (t("copy.clone.kind.forum") if setup.plan.forum
                    else t("copy.clone.kind.channel") if setup.plan.broadcast
                    else t("copy.clone.kind.group"))
            rows = [
                (t("entity.source"), getattr(source, "title", str(source.id))),
                (t("entity.dest"), setup.plan.title),
                (t("entity.type"), kind),
                (t("copy.clone.new"), t("copy.clone.private_note")),
            ]
            if setup.plan.forum:
                topic_count = len({topic.id for topic in setup.plan.topics} | {1})
                rows.append((t("copy.clone.topics"), str(topic_count)))
        else:
            rows = [
                (t("entity.source"), getattr(source, "title", str(source.id))),
                (t("entity.dest"), getattr(setup.dest, "title", str(setup.dest.id))),
            ]
        rows.extend([
            (t("filter.default"), filter_name(filter_code)),
            (t("copy.mode"),
             t("copy.mode.forward") if config.forward_mode else t("copy.mode.copy")),
            (t("settings.delay"), f"{config.delay:.1f}s"),
            (t("settings.workers"), str(config.workers)),
            (t("settings.resume"), t("settings.on") if config.resume else t("settings.off")),
        ])
        ui.box(t("copy.summary"), rows, color=C.CYAN)

        if not ui.confirm(t("copy.confirm")):
            self._pop()
            return

        if setup.plan is not None:
            from channel_clone import ChannelCloneError

            try:
                await self._apply_forum_structure(setup)
            except ChannelCloneError as exc:
                logger.exception(f"Falha ao preparar a estrutura do destino: {exc}")
                ui.err(t("copy.clone.failed", reason=str(exc)))
                if getattr(exc, "destination", None) is not None:
                    ui.warn(t("copy.clone.partial", id=getattr(exc.destination, "id", "?")))
                hint = self._clone_error_hint(exc)
                if hint:
                    ui.warn(hint)
                ui.pause()
                self._pop()
                return
            config.use_dedup = False
            config.signature = ""

        from copier import Copier

        dest = setup.dest
        topic_map = setup.topic_map
        copier = Copier(self.client, repo=db, config=config)
        result: Dict[str, Any] = {}
        started = time.time()
        try:
            result = await copier.run(
                source, dest,
                filter_code=filter_code,
                source_topic=source_topic,
                dest_topic=setup.dest_topic,
                topic_map=topic_map,
                topic_titles=setup.titles(),
            )
        except KeyboardInterrupt:
            ui.warn(t("copy.interrupted"))
        except Exception as exc:
            logger.exception("Falha na cópia")
            ui.err(f"{t('err.generic')}: {exc}")
            ui.pause()
        finally:
            if setup.plan is not None and dest is not None and topic_map:
                if result.get("complete"):
                    try:
                        setting_warnings = await setup.cloner.apply_topic_settings(
                            setup.plan, dest, topic_map
                        )
                        for topic_name, reason in setting_warnings:
                            ui.warn(t("copy.clone.setting_failed",
                                      topic=topic_name, reason=reason))
                    except Exception as exc:
                        logger.exception(f"Falha ao restaurar opções dos tópicos: {exc}")
                        ui.warn(t("copy.clone.setting_failed", topic="—", reason=str(exc)))
                else:
                    ui.warn(t("copy.clone.settings_postponed"))

        self._record_result(result, time.time() - started)
        self._print_result(result)
        self._pop()

    def _copy_options(self) -> RunConfig:
        cfg = RunConfig(**{k: v for k, v in self.config.to_dict().items()})
        ui.blank()
        if ui.confirm(f"{t('copy.options')}: {t('copy.mode.copy')} / "
                      f"{t('copy.mode.forward')}? Alterar configurações?"):
            ui.menu([
                ("1", t("copy.mode"),
                 t("copy.mode.forward") if cfg.forward_mode else t("copy.mode.copy")),
                ("2", t("settings.delay"), f"{cfg.delay:.1f}s"),
                ("3", t("settings.workers"), str(cfg.workers)),
                ("4", t("settings.resume"),
                 t("settings.on") if cfg.resume else t("settings.off")),
                ("0", t("menu.back"), ""),
            ])
            while True:
                choice = (ui.ask(t("menu.option"), required=False) or "0").strip()
                if choice == "0":
                    break
                if choice == "1":
                    cfg.forward_mode = not cfg.forward_mode
                    cfg.delivery = "forward" if cfg.forward_mode else "upload"
                    ui.ok(f"{t('copy.mode')}: "
                          f"{t('copy.mode.forward') if cfg.forward_mode else t('copy.mode.copy')}")
                elif choice == "2":
                    raw = ui.ask(t("settings.delay.hint"), required=False)
                    try:
                        if raw:
                            cfg.delay = max(0.0, float(raw.replace(",", ".")))
                            ui.ok(t("settings.delay.set", val=f"{cfg.delay:.1f}"))
                    except ValueError:
                        ui.err(t("settings.delay.invalid"))
                elif choice == "3":
                    raw = ui.ask(t("settings.workers.title", max=WORKERS_HARD_CAP,
                                   current=cfg.workers),
                                 required=False)
                    try:
                        if raw:
                            cfg.workers = max(1, min(WORKERS_HARD_CAP, int(raw)))
                            ui.ok(t("settings.workers.set", val=cfg.workers))
                    except ValueError:
                        ui.err(t("menu.invalid"))
                elif choice == "4":
                    cfg.resume = not cfg.resume
                    ui.ok(f"{t('settings.resume')}: "
                          f"{t('settings.on') if cfg.resume else t('settings.off')}")
            self.config = cfg
            self.prefs.save_config(cfg)
        return cfg

    # ── fluxo: multi-destino ────────────────────────────────────────────────

    async def _menu_multi(self) -> None:
        self._push(t("menu.multi"))
        ui.clear()
        ui.header(t("menu.multi"))
        self._trail_line()

        source = await self.picker.pick(t("entity.source"))
        if source is None:
            self._pop()
            return
        source_topic = await self._resolve_topic(source)
        self._show_entity(source, t("entity.source"))

        targets: List[_ForumSetup] = []
        seen: set = set()

        while True:
            if targets:
                ui.section(t("multi.list"))
                for index, target in enumerate(targets, start=1):
                    extra = ""
                    if target.dest_topic:
                        extra = f" · {t('multi.topic', id=target.dest_topic)}"
                    elif (not source_topic and getattr(source, "forum", False)
                          and target.dest is not None
                          and getattr(target.dest, "forum", False)):
                        extra = f" · {t('multi.forum_full')}"
                    ui.bullet(f"{index}. {self._setup_label(target)}{extra}")
            ui.menu([
                ("1", t("picker.from_list"), t("picker.from_list.desc")),
                ("2", t("picker.manual"), t("picker.manual.desc")),
                ("3", t("multi.remove"), ""),
                ("0", t("multi.start") if targets else t("menu.back"), ""),
            ])
            mode = (ui.ask(t("multi.dest_n", n=len(targets) + 1), required=False) or "0").strip()

            if mode == "0":
                break
            if mode == "3":
                if targets:
                    removed = targets.pop()
                    if removed.dest is not None:
                        seen.discard(removed.dest.id)
                    ui.ok(f"{t('multi.remove')}: {self._setup_label(removed)}")
                continue

            entity = None
            if mode == "1":
                entity = await self.picker.pick(
                    t("entity.dest"), highlight_id=source.id, allow_create=True
                )
                if entity is AUTO_CREATE:
                    auto = await self._prepare_auto_clone(source)
                    if auto is None:
                        continue
                    if any(t.plan is not None and t.dest is None
                           and t.plan.title == auto.plan.title for t in targets):
                        ui.warn(f"{auto.plan.title} — {t('multi.already')}")
                        continue
                    targets.append(auto)
                    ui.ok(f"{t('multi.added')}: {auto.plan.title}")
                    continue
            elif mode == "2":
                raw = ui.ask(f"{t('entity.dest')} (@usuario, link ou ID)")
                if raw:
                    entity = await self._resolve_entity(raw)

            if entity is None:
                continue
            if entity.id == source.id:
                ui.err(t("entity.same"))
                continue
            if entity.id in seen:
                ui.warn(f"{getattr(entity, 'title', entity.id)} — {t('multi.already')}")
                continue

            topic = await self._resolve_topic(entity, for_destination=True)
            targets.append(_ForumSetup(dest=entity, dest_topic=topic))
            seen.add(entity.id)
            ui.ok(f"{t('multi.added')}: {getattr(entity, 'title', entity.id)}")

        if not targets:
            ui.warn(t("multi.no_dests"))
            ui.pause()
            self._pop()
            return

        filter_code = await self._pick_filter() or "all"
        if filter_code != "all" and not ui.confirm(t("copy.filter_warning"), default=False):
            self._pop()
            return
        if not ui.confirm(t("multi.confirm", n=len(targets))):
            self._pop()
            return

        # Cada destino de fórum recebe a própria estrutura de tópicos antes de
        # enviar qualquer mensagem; um destino problemático não derruba os outros.
        from channel_clone import ChannelCloneError
        from copier import Copier

        ready: List[_ForumSetup] = []
        for target in targets:
            label = self._setup_label(target)
            try:
                if target.plan is not None and target.dest is None:
                    # Destino a criar: o plano já veio pronto de _prepare_auto_clone.
                    setup = target
                else:
                    setup = await self._plan_forum_structure(
                        source, target.dest, source_topic, target.dest_topic,
                        require_forum=False,
                    )
                if setup is None:
                    ui.warn(f"{label} — {t('multi.skipped_topic')}")
                    continue
                await self._apply_forum_structure(setup)
            except ChannelCloneError as exc:
                logger.exception(f"Multi-destino: estrutura de {label} falhou: {exc}")
                ui.err(f"{label} — {exc}")
                hint = self._clone_error_hint(exc)
                if hint:
                    ui.warn(hint)
                recovered = await self._handle_clone_failure(
                    exc, source, require_forum=False
                )
                if recovered is not None:
                    ready.append(recovered)
                continue
            except Exception as exc:
                logger.exception(f"Multi-destino: falha ao preparar {label}: {exc}")
                ui.err(f"{label} — {t('multi.failed')}: {exc}")
                continue
            ready.append(setup)

        if not ready:
            ui.warn(t("multi.no_dests"))
            ui.pause()
            self._pop()
            return

        totals = {"done": 0, "skipped": 0, "errors": 0, "duplicates": 0, "bytes": 0,
                  "placeholders": 0, "server_copies": 0, "bytes_saved": 0,
                  "intentional_skips": 0}
        for index, setup in enumerate(ready, start=1):
            dest = setup.dest
            ui.section(f"{t('multi.dest_label', i=index, total=len(ready))}: "
                       f"{getattr(dest, 'title', dest.id)}")
            config = self.config
            if setup.faithful:
                config = RunConfig(**self.config.to_dict())
                config.use_dedup = False
                config.signature = ""
            copier = Copier(self.client, repo=db, config=config)
            try:
                result = await copier.run(
                    source, dest,
                    filter_code=filter_code,
                    source_topic=source_topic,
                    dest_topic=setup.dest_topic,
                    topic_map=setup.topic_map,
                    topic_titles=setup.titles(),
                )
            except KeyboardInterrupt:
                ui.warn(t("copy.interrupted"))
                break
            except Exception as exc:
                logger.exception("Falha no multi-destino")
                ui.err(f"{t('multi.failed')}: {exc}")
                continue
            for key in totals:
                totals[key] += int(result.get(key, 0) or 0)
            self._print_result(result, pause=False)
            if (setup.plan is not None and setup.faithful
                    and result.get("complete")):
                try:
                    for topic_name, reason in await setup.cloner.apply_topic_settings(
                        setup.plan, dest, setup.topic_map or {}
                    ):
                        ui.warn(t("copy.clone.setting_failed",
                                  topic=topic_name, reason=reason))
                except Exception as exc:
                    logger.exception(f"Falha ao restaurar opções dos tópicos: {exc}")
                    ui.warn(t("copy.clone.setting_failed", topic="—", reason=str(exc)))

        total_rows = [
            (t("result.sent"), Fmt.number(totals["done"])),
            (t("result.skipped"), Fmt.number(totals["skipped"])),
            (t("result.errors"), Fmt.number(totals["errors"])),
            (t("result.bytes"), Fmt.size(totals["bytes"])),
        ]
        if totals["placeholders"]:
            total_rows.append((t("result.placeholders"),
                               f"{Fmt.number(totals['placeholders'])} ({t('result.placeholders.hint')})"))
        ui.box(t("multi.total_sent", n=len(ready)), total_rows,
               color=C.AMBER if totals["errors"] or totals["placeholders"] else C.GREEN)
        ui.pause()
        self._pop()

    # ── fluxo: modo espelho ─────────────────────────────────────────────────

    async def _menu_mirror(self) -> None:
        self._push(t("menu.mirror"))
        ui.clear()
        ui.header(t("menu.mirror"))
        self._trail_line()

        source = await self.picker.pick(t("entity.source"))
        if source is None:
            self._pop()
            return
        source_topic = await self._resolve_topic(source)
        self._show_entity(source, t("entity.source"))

        from channel_clone import ChannelCloneError
        from copier import Copier

        dest = await self.picker.pick(t("entity.dest"), highlight_id=source.id,
                                      allow_create=True)
        if dest is AUTO_CREATE:
            setup = await self._prepare_auto_clone(source)
            if setup is None:
                self._pop()
                return
        else:
            if dest is None or dest.id == source.id:
                if dest is not None:
                    ui.err(t("entity.same"))
                    ui.pause()
                self._pop()
                return
            dest = await self._refresh_entity(dest)
            dest_topic = await self._resolve_topic(dest, for_destination=True)
            try:
                setup = await self._plan_forum_structure(
                    source, dest, source_topic, dest_topic, require_forum=False
                )
            except ChannelCloneError as exc:
                logger.exception(f"Falha ao planejar a estrutura do destino: {exc}")
                ui.err(t("copy.clone.failed", reason=str(exc)))
                ui.pause()
                self._pop()
                return
            if setup is None:
                ui.info(t("copy.cancelled"))
                self._pop()
                return
            self._show_entity(setup.dest, t("entity.dest"))

        filter_code = await self._pick_filter() or "all"
        if filter_code != "all" and not ui.confirm(t("copy.filter_warning"), default=False):
            self._pop()
            return
        ui.blank()
        ui.info(t("mirror.stop"))
        if not ui.confirm(t("copy.confirm")):
            self._pop()
            return

        try:
            await self._apply_forum_structure(setup)
        except ChannelCloneError as exc:
            logger.exception(f"Falha ao preparar a estrutura do destino: {exc}")
            ui.err(t("copy.clone.failed", reason=str(exc)))
            hint = self._clone_error_hint(exc)
            if hint:
                ui.warn(hint)
            recovered = await self._handle_clone_failure(
                exc, source, require_forum=False
            )
            if recovered is None:
                ui.pause()
                self._pop()
                return
            setup = recovered

        config = self.config
        topic_map = setup.topic_map if setup.plan is not None else None
        is_forum_plan = setup.plan is not None and bool(getattr(setup.plan, "forum", False))
        if topic_map:
            # Espelho fiel de fórum: repetições em tópicos diferentes valem.
            config = RunConfig(**self.config.to_dict())
            config.use_dedup = False
            config.signature = ""
            ui.info(t("copy.clone.faithful_forum"))
        copier = Copier(self.client, repo=db, config=config)
        ensure_topic = (self._mirror_topic_creator(source, setup)
                        if is_forum_plan else None)
        try:
            await copier.mirror(
                source, setup.dest,
                filter_code=filter_code,
                source_topic=source_topic,
                dest_topic=setup.dest_topic,
                topic_map=topic_map,
                ensure_topic=ensure_topic,
            )
        except KeyboardInterrupt:
            ui.warn(t("mirror.stopped"))
        ui.pause()
        self._pop()

    # ── clone automático ────────────────────────────────────────────────────

    @staticmethod
    def _clone_key(source) -> str:
        return f"auto_clone:{int(getattr(source, 'id', 0) or 0)}"

    def _remember_clone(self, source, dest) -> None:
        """Registra o par origem→destino logo após CreateChannelRequest."""
        payload = {
            "dest_id": int(getattr(dest, "id", 0) or 0),
            "criado_em": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            db.set_kv(self._clone_key(source), json.dumps(payload))
        except Exception as exc:
            logger.warning(f"Clone automático não pôde ser registrado: {exc}")

    def _known_clone_id(self, source) -> Optional[int]:
        raw = db.get_kv(self._clone_key(source))
        if not raw:
            return None
        try:
            dest_id = int(json.loads(raw).get("dest_id") or 0)
        except (ValueError, TypeError, AttributeError):
            return None
        return dest_id or None

    async def _lookup_chat(self, chat_id: int):
        try:
            return await self.client.get_entity(int(chat_id))
        except Exception as exc:
            logger.warning(f"Clone registrado ({chat_id}) não pôde ser lido: {exc}")
            return None

    @staticmethod
    def _looks_admin(entity) -> bool:
        return bool(getattr(entity, "creator", False)
                    or getattr(entity, "admin_rights", None))

    async def _may_manage(self, chat) -> bool:
        """Admin/creator do destino? Na dúvida, deixa a cópia reclamar."""
        if self._looks_admin(chat):
            return True
        get_permissions = getattr(self.client, "get_permissions", None)
        if get_permissions is None:
            return True
        try:
            perms = await get_permissions(chat)
        except Exception as exc:
            logger.warning(f"Permissões de {getattr(chat, 'id', '?')} indisponíveis: {exc}")
            return True
        return bool(getattr(perms, "is_admin", False)
                    or getattr(perms, "is_creator", False))

    async def _existing_clone(self, source) -> Optional[Any]:
        """Clone desta origem ainda válido (existe e somos admin), ou None."""
        candidates: List[int] = []
        known = self._known_clone_id(source)
        if known:
            candidates.append(known)
        try:
            for item in await self.picker.load():
                entity = item.get("entity")
                clone_of = getattr(entity, "clone_of", None)
                clone_of_id = int(getattr(clone_of, "channel_id", clone_of) or 0)
                if clone_of_id and clone_of_id == int(source.id):
                    candidates.append(int(item["id"]))
        except Exception as exc:  # pragma: no cover - depende da rede
            logger.warning(f"Busca por clones existentes falhou: {exc}")

        source_id = int(getattr(source, "id", 0) or 0)
        for chat_id in dict.fromkeys(candidates):
            if not chat_id or chat_id == source_id:
                continue
            chat = await self._lookup_chat(chat_id)
            if chat is None:
                if chat_id == known:
                    ui.warn(t("clone.reuse_missing", id=chat_id))
                    try:
                        db.delete_kv(self._clone_key(source))
                    except Exception:
                        pass
                continue
            if not await self._may_manage(chat):
                ui.warn(t("clone.reuse_no_admin",
                          title=getattr(chat, "title", chat_id)))
                continue
            return chat
        return None

    async def _prepare_auto_clone(self, source) -> Optional[_ForumSetup]:
        """Planeja um destino novo com o mesmo tipo/título/tópicos da origem."""
        from channel_clone import ChannelCloneError, ChannelCloner

        cloner = ChannelCloner(self.client, repo=db)
        title = ChannelCloner.default_title(source, self.prefs.clone_suffix)
        try:
            plan = await cloner.prepare(source, title)
        except ChannelCloneError as exc:
            logger.exception(f"Falha ao preparar o clone automático: {exc}")
            ui.err(t("copy.clone.failed", reason=str(exc)))
            ui.pause()
            return None
        for warning in plan.warnings:
            ui.warn(t("copy.clone.about_warning", reason=warning))
        return _ForumSetup(
            dest=None, cloner=cloner, plan=plan,
            topic_map={} if plan.forum else None,
        )

    async def _copy_profile_photo(self, source, dest) -> None:
        """Copia a foto da origem; qualquer falha vira aviso."""
        path = None
        try:
            handle, path = tempfile.mkstemp(prefix="telecopy_photo_", suffix=".jpg")
            os.close(handle)
            downloaded = await self.client.download_profile_photo(source, file=path)
            if not downloaded:
                ui.info(t("clone.photo_none"))
                return
            from telethon.tl.functions.channels import EditPhotoRequest
            from telethon.tl.types import InputChatUploadedPhoto

            uploaded = await self.client.upload_file(downloaded)
            await self.client(EditPhotoRequest(
                dest, InputChatUploadedPhoto(file=uploaded)
            ))
            ui.ok(t("clone.photo_done"))
        except Exception as exc:
            logger.warning(f"Foto do clone não pôde ser copiada: {exc}")
            ui.warn(t("clone.photo_failed", reason=exc))
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as exc:  # pragma: no cover - disco
                    logger.warning(f"Temporário da foto não foi removido: {exc}")

    async def _apply_no_forwards(self, dest) -> None:
        from telethon.tl.functions.messages import ToggleNoForwardsRequest

        try:
            await self.client(ToggleNoForwardsRequest(dest, True))
            ui.ok(t("clone.noforwards_done"))
        except Exception as exc:
            logger.warning(f"Proteção de conteúdo não pôde ser aplicada: {exc}")
            ui.warn(t("clone.noforwards_failed", reason=exc))

    async def _clone_extras(self, source, dest) -> None:
        if dest is None:
            return
        if self.prefs.clone_copy_photo:
            await self._copy_profile_photo(source, dest)
        if getattr(source, "noforwards", False):
            await self._apply_no_forwards(dest)

    async def _delete_created_chat(self, chat, source=None) -> bool:
        """Exclui um chat criado por esta execução (nunca um chat alheio)."""
        from telethon.tl.functions.channels import DeleteChannelRequest
        from telethon.tl.types import InputChannel

        chat_id = int(getattr(chat, "id", 0) or 0)
        try:
            await self.client(DeleteChannelRequest(InputChannel(
                chat_id, int(getattr(chat, "access_hash", 0) or 0)
            )))
        except Exception as exc:
            logger.exception(f"Falha ao excluir o clone {chat_id}: {exc}")
            ui.err(t("clone.delete_failed", reason=exc))
            return False
        if source is not None and self._known_clone_id(source) == chat_id:
            try:
                db.delete_kv(self._clone_key(source))
            except Exception:
                pass
        self.picker._loaded = False
        ui.ok(t("clone.deleted", title=getattr(chat, "title", chat_id)))
        return True

    @staticmethod
    def _clone_error_hint(exc: Exception) -> str:
        """Mensagem específica para os erros mais comuns de criação."""
        text = str(exc).casefold()
        if "channels_too_much" in text or "too many channels" in text:
            return t("clone.err_too_much")
        if "flood" in text:
            return t("clone.err_flood")
        if ("chat_admin_required" in text or "admin" in text
                or "right" in text or "not enough" in text):
            return t("clone.err_admin")
        return ""

    async def _handle_clone_failure(self, exc: Exception, source,
                                    require_forum: bool = True
                                    ) -> Optional[_ForumSetup]:
        """Falha parcial: continuar no destino criado ou excluí-lo."""
        created = getattr(exc, "destination", None)
        if created is None:
            return None
        ui.warn(t("clone.partial", title=getattr(created, "title", "?"),
                  id=getattr(created, "id", "?")))
        ui.menu([
            ("1", t("clone.partial_continue"), t("clone.partial_continue.desc")),
            ("2", t("clone.partial_delete"), t("clone.partial_delete.desc")),
            ("0", t("menu.back"), ""),
        ])
        choice = (ui.ask(t("menu.option"), required=False) or "0").strip()
        if choice == "1":
            from channel_clone import ChannelCloneError

            try:
                setup = await self._plan_forum_structure(
                    source, created, None, None, require_forum=require_forum
                )
            except ChannelCloneError as inner:
                logger.exception(f"Retomada do clone não é possível: {inner}")
                ui.err(t("clone.reuse_failed", reason=str(inner)))
                ui.pause()
                return None
            if setup is None:
                ui.info(t("copy.cancelled"))
                return None
            try:
                await self._apply_forum_structure(setup)
            except Exception as inner:
                logger.exception(f"Retomada do clone falhou: {inner}")
                ui.err(t("copy.clone.failed", reason=inner))
                ui.pause()
                return None
            return setup
        if choice == "2":
            if ui.confirm(t("clone.partial_delete_confirm",
                            title=getattr(created, "title", "?"))):
                await self._delete_created_chat(created, source)
            return None
        return None

    async def _ask_existing_clone(self, existing) -> str:
        """Pergunta o que fazer com o clone já existente (1=reusar, 2=outro)."""
        ui.warn(t("clone.reuse_found",
                  title=getattr(existing, "title", "?"),
                  id=getattr(existing, "id", "?")))
        ui.menu([
            ("1", t("clone.reuse_btn"), t("clone.reuse_desc")),
            ("2", t("clone.new_btn"), t("clone.new_desc")),
            ("0", t("menu.back"), ""),
        ])
        choice = (ui.ask(t("menu.option"), required=False) or "0").strip()
        return choice if choice in {"1", "2"} else "0"

    async def _menu_clone(self) -> None:
        self._push(t("menu.clone"))
        ui.clear()
        ui.header(t("menu.clone"))
        self._trail_line()
        ui.info(t("clone.intro"))

        source = await self.picker.pick(t("entity.source"))
        if source is None:
            self._pop()
            return
        self._show_entity(source, t("entity.source"))

        # O clone existente é consultado antes de montar um plano novo: se a
        # leitura dos tópicos da origem falhar, reutilizar continua possível.
        setup: Optional[_ForumSetup] = None
        existing = await self._existing_clone(source)
        if existing is not None:
            choice = await self._ask_existing_clone(existing)
            if choice == "0":
                self._pop()
                return
            if choice == "1":
                from channel_clone import ChannelCloneError

                try:
                    setup = await self._plan_forum_structure(
                        source, existing, None, None
                    )
                except ChannelCloneError as exc:
                    logger.exception(f"Clone existente não serve: {exc}")
                    ui.err(t("clone.reuse_failed", reason=str(exc)))
                    ui.pause()
                    setup = None
                if setup is None:
                    self._pop()
                    return
        if setup is None:
            setup = await self._prepare_auto_clone(source)
            if setup is None:
                self._pop()
                return

        plan = setup.plan
        is_forum = bool(plan is not None and plan.forum)
        if plan is not None:
            kind = (t("copy.clone.kind.forum") if plan.forum
                    else t("copy.clone.kind.channel") if plan.broadcast
                    else t("copy.clone.kind.group"))
        else:
            kind = (t("copy.clone.kind.forum")
                    if getattr(setup.dest, "forum", False)
                    else t("copy.clone.kind.channel")
                    if getattr(setup.dest, "broadcast", False)
                    else t("copy.clone.kind.group"))
        config = RunConfig(**self.config.to_dict())
        config.use_dedup = False
        if getattr(source, "noforwards", False) and config.forward_mode:
            config.forward_mode = False
            ui.info(t("copy.source_protected"))
        if is_forum:
            config.use_dedup = False
            config.signature = ""

        rows: List[Tuple[str, str]] = [
            (t("entity.source"), getattr(source, "title", str(source.id))),
            (t("entity.dest"),
             getattr(setup.dest, "title", None)
             or (plan.title if plan is not None else "?")),
            (t("copy.clone.kind"), kind),
        ]
        if is_forum and plan is not None:
            topic_count = len({topic.id for topic in plan.topics} | {1})
            label = (t("copy.clone.existing_topics") if plan.existing
                     else t("copy.clone.topics"))
            rows.append((label, str(topic_count)))
        rows.extend([
            (t("filter.default"), filter_name("all")),
            (t("settings.delay"), f"{config.delay:.1f}s"),
            (t("settings.workers"), str(config.workers)),
            (t("settings.resume"),
             t("settings.on") if config.resume else t("settings.off")),
            (t("clone.photo"),
             t("settings.on") if self.prefs.clone_copy_photo else t("settings.off")),
            (t("clone.start_copy"),
             t("settings.on") if self.prefs.clone_start_copy else t("settings.off")),
        ])
        ui.box(t("clone.summary"), rows, color=C.CYAN)
        if not ui.confirm(t("clone.confirm")):
            self._pop()
            return

        from channel_clone import ChannelCloneError
        from copier import Copier

        creating = setup.dest is None
        try:
            await self._apply_forum_structure(setup)
        except ChannelCloneError as exc:
            logger.exception(f"Falha ao preparar o clone: {exc}")
            ui.err(t("copy.clone.failed", reason=str(exc)))
            hint = self._clone_error_hint(exc)
            if hint:
                ui.warn(hint)
            setup = await self._handle_clone_failure(exc, source)
            if setup is None:
                self._pop()
                return
            creating = setup.dest is None
            plan = setup.plan
            is_forum = bool(plan is not None and plan.forum)
        except Exception as exc:  # erro inesperado não derruba o programa
            logger.exception(f"Falha inesperada ao preparar o clone: {exc}")
            ui.err(f"{t('err.generic')}: {exc}")
            ui.pause()
            self._pop()
            return

        dest = setup.dest
        if dest is None:
            ui.warn(t("clone.no_destination"))
            ui.pause()
            self._pop()
            return

        if not self.prefs.clone_start_copy:
            key = "clone.structure_only" if creating else "clone.reuse_only"
            ui.ok(t(key, title=getattr(dest, "title", dest.id),
                    id=getattr(dest, "id", "?")))
            ui.pause()
            self._pop()
            return

        topic_map = setup.topic_map or {}
        copier = Copier(self.client, repo=db, config=config)
        result: Dict[str, Any] = {}
        started = time.time()
        try:
            result = await copier.run(
                source, dest,
                filter_code="all",
                source_topic=None,
                dest_topic=setup.dest_topic,
                topic_map=topic_map or None,
                topic_titles=setup.titles(),
            )
        except KeyboardInterrupt:
            ui.warn(t("copy.interrupted"))
        except Exception as exc:
            logger.exception("Falha na cópia do clone automático")
            ui.err(f"{t('err.generic')}: {exc}")
        finally:
            if is_forum and plan is not None and topic_map and result.get("complete"):
                try:
                    for topic_name, reason in await setup.cloner.apply_topic_settings(
                        plan, dest, topic_map
                    ):
                        ui.warn(t("copy.clone.setting_failed",
                                  topic=topic_name, reason=reason))
                except Exception as exc:
                    logger.exception(f"Falha ao restaurar opções dos tópicos: {exc}")
                    ui.warn(t("copy.clone.setting_failed", topic="—", reason=str(exc)))

        self._record_result(result, time.time() - started)
        if creating:
            ui.info(t("clone.done_hint", id=getattr(dest, "id", "?")))
        self._print_result(result)
        self._pop()

    # ── ferramentas ─────────────────────────────────────────────────────────

    async def _menu_tools(self) -> None:
        self._push(t("menu.tools"))
        while True:
            ui.clear()
            ui.header(t("menu.tools"))
            self._trail_line()
            ui.menu([
                ("1", t("tools.resolve"), t("tools.resolve.desc")),
                ("2", t("tools.stats"), t("tools.stats.desc")),
                ("3", t("tools.retry"), t("tools.retry.desc")),
                ("4", t("tools.dedup"), t("tools.dedup.desc")),
                ("5", t("tools.maint"), t("tools.maint.desc")),
                ("6", t("tools.recover"), t("tools.recover.desc")),
                ("7", t("tools.order"), t("tools.order.desc")),
                ("0", t("menu.back"), ""),
            ])
            choice = (ui.ask(t("menu.option"), required=False) or "0").strip()
            if choice == "0":
                self._pop()
                return
            try:
                if choice == "1":
                    await self._tool_resolve()
                elif choice == "2":
                    self._tool_stats()
                elif choice == "3":
                    await self._tool_retry()
                elif choice == "4":
                    self._tool_dedup()
                elif choice == "5":
                    await self._tool_maintenance()
                elif choice == "6":
                    await self._tool_recover()
                elif choice == "7":
                    self._tool_order()
                elif choice:
                    ui.warn(t("menu.invalid"))
            except Exception as exc:
                logger.exception("Erro em ferramentas")
                ui.err(f"{t('err.generic')}: {exc}")
                ui.pause()

    async def _tool_resolve(self) -> None:
        ui.clear()
        ui.header(t("tools.resolve.title"))
        self._trail_line()
        ui.info(t("tools.resolve.hint"))
        while True:
            ui.blank()
            raw = ui.ask(t("tools.resolve.query"), required=False)
            if not raw:
                break
            entity = await self._resolve_entity(raw)
            if entity is None:
                continue
            rows = [
                (t("tools.resolve.name"), getattr(entity, "title", None) or str(entity.id)),
                ("ID", str(entity.id)),
                (t("tools.resolve.type"), type(entity).__name__),
                (t("tools.resolve.access"),
                 t("tools.resolve.public") if getattr(entity, "username", None)
                 else t("tools.resolve.private")),
            ]
            members = getattr(entity, "participants_count", None)
            if members:
                rows.append((t("entity.members"), Fmt.number(members)))
            if getattr(entity, "username", None):
                rows.append((t("entity.username"), f"@{entity.username}"))
            ui.box(t("tools.resolve.result"), rows, color=C.CYAN)
        ui.pause()

    def _tool_stats(self) -> None:
        ui.clear()
        ui.header(t("tools.stats.title"))
        self._trail_line()

        data = self.stats.data
        ui.box(t("tools.stats.session"), [
            (t("tools.stats.total"), Fmt.number(data.get("total", 0))),
            (t("tools.stats.photos"), Fmt.number(data.get("photos", 0))),
            (t("tools.stats.videos"), Fmt.number(data.get("videos", 0))),
            (t("tools.stats.audio"), Fmt.number(data.get("audio", 0))),
            (t("tools.stats.documents"), Fmt.number(data.get("documents", 0))),
            (t("tools.stats.text"), Fmt.number(data.get("text", 0))),
            (t("tools.stats.bytes"), Fmt.size(data.get("bytes_sent", 0))),
            (t("tools.stats.sessions"), Fmt.number(data.get("sessions", 0))),
            (t("tools.stats.last_run"), Fmt.date(data.get("last_run"))),
        ], color=C.PURPLE)

        try:
            global_stats = db.global_stats()
            ui.box(t("tools.stats.alltime"), [
                (t("tools.stats.total"), Fmt.number(global_stats.get("success", 0))),
                (t("tools.stats.errors"), Fmt.number(global_stats.get("errors", 0))),
                (t("tools.stats.duplicates"), Fmt.number(global_stats.get("duplicates", 0))),
                (t("tools.stats.moved"), Fmt.size(global_stats.get("bytes", 0))),
                (t("tools.stats.sessions"), Fmt.number(global_stats.get("sessions", 0))),
            ], color=C.INDIGO)
        except Exception:
            pass

        level, count, remaining = daily_limit.status(
            int(getattr(self.config, "daily_limit", DAILY_LIMIT)))
        color = C.RED if level == "limit" else (C.YELLOW if level == "warn" else C.GREEN)
        ui.box(t("tools.stats.daily"), [
            (t("tools.stats.today"), Fmt.number(count)),
            (t("tools.stats.limit"),
             Fmt.number(self.config.daily_limit) if self.config.daily_limit
             else t("settings.off")),
            (t("tools.stats.remaining"), Fmt.number(remaining)),
        ], color=color)

        recent = db.recent_sessions(6)
        if recent:
            ui.section(t("tools.stats.recent"))
            rows = []
            for sess in recent:
                label = f"{(sess.get('source_name') or '?')[:18]} → {(sess.get('dest_name') or '?')[:18]}"
                rows.append([label, str(sess.get("success") or 0), Fmt.date(sess.get("started_at"))])
            ui.table(["Cópia", "Enviadas", "Quando"], rows,
                     widths=[42, 10, 18])
        ui.pause()

    async def _tool_retry(self) -> None:
        ui.clear()
        ui.header(t("tools.retry.title"))
        self._trail_line()

        stats = self.retry.stats()
        ui.box(t("tools.retry.queue"), [
            (t("tools.retry.pending"), Fmt.number(stats["pending"])),
            (t("tools.retry.total"), Fmt.number(stats["total"])),
            (t("tools.retry.attempts"), Fmt.number(stats["attempts"])),
        ], color=C.AMBER)

        jobs = self.retry.all_jobs()
        if not jobs:
            ui.info(t("tools.retry.empty"))
            ui.pause()
            return

        rows = []
        for job in jobs[:12]:
            rows.append([
                str(job.msg_id),
                str(job.attempts),
                (job.reason or "—")[:34],
            ])
        ui.table(["msg_id", "tent.", "motivo"], rows, widths=[10, 7, 46])

        if stats["pending"] and ui.confirm(t("tools.retry.retry_now")):
            result = await self._retry_pending()
            if result:
                self._print_result(result)
                return
        if ui.confirm(t("tools.retry.clear")):
            removed = self.retry.clear_all()
            ui.ok(f"{removed} {t('tools.retry.cleared')}")
        ui.pause()

    async def _retry_pending(self) -> Dict[str, Any]:
        """Reprocessa as mensagens que falharam, agrupadas por origem/destino."""
        from copier import Copier

        jobs = self.retry.all_pending()
        if not jobs:
            ui.info(t("queue.nothing"))
            return {}

        groups: Dict[Tuple[int, int], List[int]] = {}
        for job in jobs:
            groups.setdefault((job.source_id, job.dest_id), []).append(job.msg_id)

        ui.info(t("queue.retry_start"))
        totals = {"done": 0, "skipped": 0, "errors": 0, "duplicates": 0, "bytes": 0,
                  "placeholders": 0, "server_copies": 0, "bytes_saved": 0,
                  "intentional_skips": 0}
        for (source_id, dest_id), msg_ids in groups.items():
            try:
                source = await self.client.get_entity(source_id)
                dest = await self.client.get_entity(dest_id)
            except Exception as exc:
                logger.error(f"Retry: entidade indisponível {source_id}->{dest_id}: {exc}")
                continue
            retry_config = self.config
            if getattr(source, "forum", False) and getattr(dest, "forum", False):
                retry_config = RunConfig(**self.config.to_dict())
                retry_config.use_dedup = False
                retry_config.signature = ""
            copier = Copier(self.client, repo=db, config=retry_config)
            try:
                result = await copier.retry_messages(source, dest, msg_ids)
            except Exception as exc:
                logger.exception("Falha ao reprocessar")
                ui.err(f"{t('err.generic')}: {exc}")
                continue
            for key in totals:
                totals[key] += int(result.get(key, 0) or 0)
        return totals

    def _tool_order(self) -> None:
        ui.clear()
        ui.header(t("tools.order.title"))
        pairs = db.order_pairs()
        if not pairs:
            ui.info(t("tools.order.empty"))
            ui.pause()
            return
        violations = 0
        unknown = 0
        details = []
        for pair in pairs:
            by_topic: Dict[Any, Dict[str, list]] = {}
            for row in db.order_rows(pair["source_id"], pair["dest_id"]):
                bucket = by_topic.setdefault(row.get("dest_topic"),
                                             {"resolved": [], "unresolved": []})
                if row.get("dest_msg_id") is None:
                    bucket["unresolved"].append(int(row["msg_id"]))
                else:
                    bucket["resolved"].append((int(row["dest_msg_id"]), int(row["msg_id"])))
            for topic, bucket in by_topic.items():
                unknown += len(bucket["unresolved"])
                resolved = sorted(bucket["resolved"])
                last_msg_id = None
                for _dest_msg_id, msg_id in resolved:
                    if last_msg_id is not None and msg_id < last_msg_id:
                        violations += 1
                        details.append(f"{pair['source_id']}->{pair['dest_id']} "
                                       f"tópico {topic}: {msg_id} após {last_msg_id}")
                    last_msg_id = msg_id
                # Posição resolvida ocupando o lugar de uma pendência anterior.
                # Pendência DEPOIS de tudo é estado normal, não violação.
                if bucket["unresolved"] and resolved:
                    if min(bucket["unresolved"]) < max(m for _d, m in resolved):
                        violations += 1
                        details.append(f"{pair['source_id']}->{pair['dest_id']} "
                                       f"tópico {topic}: resolvido após posição não resolvida")
        rows_box = [(t("tools.order.violations"), str(violations)),
                    (t("tools.order.unknown"), str(unknown))]
        ui.box(t("tools.order.title"), rows_box,
               color=C.GREEN if violations == 0 else C.RED)
        for detail in details[:20]:
            ui.warn(detail)
        if violations == 0:
            ui.ok(t("tools.order.ok"))
        ui.pause()

    async def _tool_recover(self) -> None:
        ui.clear()
        ui.header(t("tools.recover.title"))
        self._trail_line()
        pairs = db.placeholder_pairs()
        if not pairs:
            ui.info(t("tools.recover.empty"))
            ui.pause()
            return
        ui.info(t("tools.recover.columns"))
        ui.box(t("tools.recover.title"), [
            (t("tools.recover.pair", source=row["source_id"], dest=row["dest_id"]),
             f"{row['open']} / {row['recovered']} / {row['unrecoverable']}")
            for row in pairs
        ], color=C.AMBER)
        if not any(row["open"] for row in pairs):
            ui.info(t("tools.recover.empty"))
            ui.pause()
            return
        if not ui.confirm(t("tools.recover.confirm")):
            return
        from copier import Copier

        totals = {"recovered": 0, "unrecoverable": 0, "failed": 0}
        for row in pairs:
            if not row["open"]:
                continue
            try:
                source = await self.client.get_entity(int(row["source_id"]))
                dest = await self.client.get_entity(int(row["dest_id"]))
            except Exception as exc:
                logger.error(f"Recuperação: entidade indisponível {row['source_id']}->{row['dest_id']}: {exc}")
                totals["failed"] += int(row["open"])
                continue
            copier = Copier(self.client, repo=db, config=self.config)
            try:
                result = await copier.recover_placeholders(source, dest, self.config)
            except Exception as exc:
                logger.exception("Falha ao recuperar lacunas")
                ui.err(f"{t('err.generic')}: {exc}")
                totals["failed"] += int(row["open"])
                continue
            for key in totals:
                totals[key] += int(result.get(key, 0) or 0)
        display = {
            "done": totals["recovered"],
            "errors": totals["failed"],
            "unrecoverable": totals["unrecoverable"],
            "complete": totals["failed"] == 0 and totals["unrecoverable"] == 0,
        }
        if totals["failed"]:
            display["first_error"] = t("tools.recover.failed")
        self._print_result(display, pause=False)
        (ui.ok if display["complete"] else ui.warn)(t("tools.recover.done"))
        ui.pause()

    def _tool_dedup(self) -> None:
        ui.clear()
        ui.header(t("tools.dedup.title"))
        self._trail_line()

        stats = self.dedup.stats()
        lru, bloom = stats["lru"], stats["bloom"]
        ui.box(t("tools.dedup.lru"), [
            (t("tools.dedup.items"), f"{Fmt.number(lru['items'])} / {Fmt.number(lru['capacity'])}"),
            (t("tools.dedup.usage"), f"{lru['usage_pct']}%"),
        ], color=C.CYAN)
        ui.box(t("tools.dedup.bloom"), [
            (t("tools.dedup.items"), Fmt.number(bloom["items"])),
            (t("tools.dedup.memory"), f"{bloom['memory_kb']} KB"),
            (t("tools.dedup.hashes"), str(bloom["hash_funcs"])),
            (t("tools.dedup.fp"), f"{bloom['fp_rate']}%"),
        ], color=C.PURPLE)
        ui.box(t("tools.dedup.db"), [
            (t("tools.dedup.history"), Fmt.number(db.dedup_count())),
            (t("tools.dedup.size"), Fmt.size(db.size_bytes())),
        ], color=C.INDIGO)

        if ui.confirm(t("tools.dedup.reset")):
            self.dedup.reset()
            ui.ok(t("tools.dedup.reset_done"))
        ui.pause()

    async def _tool_maintenance(self) -> None:
        ui.clear()
        ui.header(t("tools.maint.title"))
        self._trail_line()

        health = db.health()
        ui.box(t("tools.maint.health"), [
            (t("tools.maint.integrity"), str(health["integrity"])),
            (t("tools.dedup.size"), Fmt.size(health["size"])),
            (t("tools.stats.sessions"), Fmt.number(health["counts"].get("sessions", 0))),
            (t("tools.dedup.history"), Fmt.number(health["counts"].get("dedup", 0))),
        ], color=C.GREEN)

        temp_files = [f for f in TEMP_DIR.glob("*") if f.is_file()] if TEMP_DIR.exists() else []
        if temp_files:
            size = sum(f.stat().st_size for f in temp_files)
            ui.info(f"{len(temp_files)} {t('tools.maint.tmp_found')} ({Fmt.size(size)})")
            if ui.confirm(t("tools.maint.tmp_delete")):
                removed = 0
                for path in temp_files:
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        pass
                ui.ok(f"{t('tools.maint.tmp_cleared')}: {removed}")
        else:
            ui.info(t("tools.maint.tmp_none"))

        if ui.confirm(t("tools.maint.backup")):
            target = BACKUP_DIR / f"telecopy_{datetime.now():%Y%m%d_%H%M%S}.db"
            try:
                db.vacuum()
                shutil.copy2(db.path, target)
                ui.ok(f"{t('tools.maint.backup_done')}: {target}")
            except Exception as exc:
                ui.err(str(exc))

        if ui.confirm(t("tools.maint.db_prune")):
            try:
                removed = db.prune(days=30)
                total = sum(removed.values())
                ui.ok(f"{Fmt.number(total)} {t('tools.maint.db_done')}")
            except Exception as exc:
                ui.err(str(exc))

        if ui.confirm(t("tools.maint.db_vacuum")):
            try:
                db.vacuum()
                ui.ok(t("tools.maint.db_vacuum_done"))
            except Exception as exc:
                ui.err(str(exc))

        ui.pause()

    # ── configurações ───────────────────────────────────────────────────────

    async def _menu_settings(self) -> None:
        self._push(t("menu.settings"))
        while True:
            ui.clear()
            ui.header(t("menu.settings"))
            self._trail_line()

            cfg = self.config
            ui.box(t("settings.status"), [
                (t("settings.delay"), f"{cfg.delay:.1f}s"),
                (t("settings.workers"), str(cfg.workers)),
                (t("settings.fast_transfer"), t("settings.on") if cfg.fast_transfer else t("settings.off")),
                (t("settings.connections_per_file"), str(cfg.connections_per_file)),
                (t("settings.lookahead"), str(cfg.lookahead) if cfg.lookahead else "auto"),
                (t("settings.max_tmp_gb"), f"{cfg.max_tmp_gb:g}"),
                (t("settings.max_total_connections"), str(cfg.max_total_connections)),
                (t("settings.daily_limit"),
                 str(cfg.daily_limit) if cfg.daily_limit else t("settings.off")),
                (t("settings.dedup"), t("settings.on") if cfg.use_dedup else t("settings.off")),
                (t("settings.resume"), t("settings.on") if cfg.resume else t("settings.off")),
                (t("settings.forward"),
                 t("copy.mode.forward") if cfg.forward_mode else t("copy.mode.copy")),
                (t("settings.placeholder"),
                 t("settings.on") if cfg.placeholder else t("settings.off")),
                (t("settings.delivery"), t(f"delivery.{cfg.delivery}")),
                (t("settings.server_copy"),
                 t("settings.on") if cfg.server_copy else t("settings.off")),
                (t("settings.on_failure"), t(f"on_failure.{cfg.on_failure}")),
                (t("settings.strict_order"), t("settings.on") if cfg.strict_order else t("settings.off")),
                (t("settings.max_retries"), str(cfg.max_retries)),
                (t("settings.signature"), cfg.signature or "—"),
                (t("clone.suffix"), self.prefs.clone_suffix or "—"),
                (t("clone.photo"),
                 t("settings.on") if self.prefs.clone_copy_photo else t("settings.off")),
                (t("clone.start_copy"),
                 t("settings.on") if self.prefs.clone_start_copy else t("settings.off")),
                (t("settings.lang"), get_lang().upper()),
            ], color=C.INDIGO)
            ui.warn(t("settings.fidelity.warning"))

            ui.menu([
                ("1", t("settings.delay.option"), f"{cfg.delay:.1f}s"),
                ("2", t("settings.workers.option"), str(cfg.workers)),
                ("r", t("settings.recommended.option"), t("settings.recommended.desc")),
                ("p", t("settings.turbo.option"), t("settings.turbo.desc")),
                ("t", t("settings.fast_transfer.toggle"), t("settings.on") if cfg.fast_transfer else t("settings.off")),
                ("n", t("settings.connections_per_file.option"), str(cfg.connections_per_file)),
                ("l", t("settings.lookahead.option"), str(cfg.lookahead) if cfg.lookahead else "auto"),
                ("g", t("settings.max_tmp_gb.option"), f"{cfg.max_tmp_gb:g}"),
                ("j", t("settings.max_total_connections.option"), str(cfg.max_total_connections)),
                ("q", t("settings.daily_limit.option"),
                 str(cfg.daily_limit) if cfg.daily_limit else t("settings.off")),
                ("3", t("settings.dedup.toggle"), ""),
                ("4", t("settings.resume.toggle"), ""),
                ("5", t("settings.forward.toggle"), ""),
                ("6", t("settings.signature.option"), ""),
                ("7", t("settings.lang"), get_lang().upper()),
                ("8", t("settings.logs.export"), t("settings.logs.desc")),
                ("9", t("settings.about"), ""),
                ("a", t("settings.clone_suffix.option"), self.prefs.clone_suffix or "—"),
                ("f", t("settings.clone_photo.toggle"), ""),
                ("k", t("settings.clone_start.toggle"), ""),
                ("h", t("settings.placeholder.toggle"), ""),
                ("d", t("settings.delivery.toggle"), t(f"delivery.{cfg.delivery}")),
                ("s", t("settings.server_copy.toggle"), ""),
                ("o", t("settings.on_failure.toggle"), t(f"on_failure.{cfg.on_failure}")),
                ("m", t("settings.max_retries.option"), str(cfg.max_retries)),
                ("b", t("settings.data_reset"), t("settings.data_reset.desc")),
                ("c", t("settings.creds.reset"), t("settings.creds.desc")),
                ("0", t("menu.back"), ""),
            ])
            choice = (ui.ask(t("menu.option"), required=False) or "0").strip().lower()

            if choice == "0":
                self._pop()
                return
            if choice == "1":
                self._set_delay()
            elif choice == "2":
                self._set_workers()
            elif choice == "3":
                if not cfg.use_dedup and not ui.confirm(t("settings.dedup.warning"), default=False):
                    await asyncio.sleep(0.2)
                    continue
                cfg.use_dedup = not cfg.use_dedup
                self._save_config()
                ui.ok(f"{t('settings.dedup')}: "
                      f"{t('settings.on') if cfg.use_dedup else t('settings.off')}")
                await asyncio.sleep(0.7)
            elif choice == "4":
                cfg.resume = not cfg.resume
                self._save_config()
                ui.ok(f"{t('settings.resume')}: "
                      f"{t('settings.on') if cfg.resume else t('settings.off')}")
                await asyncio.sleep(0.7)
            elif choice == "5":
                cfg.forward_mode = not cfg.forward_mode
                cfg.delivery = "forward" if cfg.forward_mode else "upload"
                self._save_config()
                ui.ok(f"{t('settings.forward')}: "
                      f"{t('copy.mode.forward') if cfg.forward_mode else t('copy.mode.copy')}")
                await asyncio.sleep(0.7)
            elif choice == "6":
                self._set_signature()
            elif choice == "7":
                self._change_language()
            elif choice == "8":
                self._export_logs()
            elif choice == "9":
                self._about()
            elif choice == "a":
                self._set_clone_suffix()
            elif choice == "f":
                self._toggle_clone_photo()
                await asyncio.sleep(0.7)
            elif choice == "k":
                self._toggle_clone_start()
                await asyncio.sleep(0.7)
            elif choice == "h":
                cfg.placeholder = not cfg.placeholder
                self._save_config()
                message = (f"{t('settings.placeholder')}: "
                           f"{t('settings.on') if cfg.placeholder else t('settings.off')}")
                (ui.ok if cfg.placeholder else ui.warn)(message)
                await asyncio.sleep(0.7)
            elif choice == "d":
                order = {"upload": "reference", "reference": "forward", "forward": "upload"}
                cfg.delivery = order.get(cfg.delivery, "upload")
                cfg.forward_mode = cfg.delivery == "forward"
                if cfg.delivery == "reference":
                    cfg.server_copy = True
                self._save_config()
                ui.ok(f"{t('settings.delivery')}: {t(f'delivery.{cfg.delivery}')}")
                await asyncio.sleep(0.7)
            elif choice == "s":
                cfg.server_copy = not cfg.server_copy
                self._save_config()
                ui.ok(f"{t('settings.server_copy')}: "
                      f"{t('settings.on') if cfg.server_copy else t('settings.off')}")
                await asyncio.sleep(0.7)
            elif choice == "o":
                cfg.on_failure = "abort" if cfg.on_failure == "placeholder" else "placeholder"
                self._save_config()
                ui.ok(f"{t('settings.on_failure')}: {t(f'on_failure.{cfg.on_failure}')}")
                await asyncio.sleep(0.7)
            elif choice == "r":
                self._apply_recommended()
                await asyncio.sleep(1.5)
            elif choice == "p":
                self._apply_turbo()
                await asyncio.sleep(1.5)
            elif choice == "t":
                cfg.fast_transfer = not cfg.fast_transfer
                self._save_config()
                ui.info(t("settings.fast_transfer.hint"))
                ui.ok(f"{t('settings.fast_transfer')}: "
                      f"{t('settings.on') if cfg.fast_transfer else t('settings.off')}")
                await asyncio.sleep(0.7)
            elif choice == "n":
                self._set_connection_option("connections_per_file", 1, MAX_CONNECTIONS_PER_FILE)
                await asyncio.sleep(0.7)
            elif choice == "l":
                self._set_connection_option("lookahead", 0, 1000)
                await asyncio.sleep(0.7)
            elif choice == "g":
                self._set_tmp_limit()
                await asyncio.sleep(0.7)
            elif choice == "j":
                self._set_connection_option("max_total_connections", 1, MAX_TOTAL_CONNECTIONS_CAP)
                await asyncio.sleep(0.7)
            elif choice == "q":
                self._set_connection_option("daily_limit", 0, 1_000_000)
                await asyncio.sleep(0.7)
            elif choice == "m":
                raw = ui.ask(t("settings.max_retries"), required=False)
                if raw is not None:
                    try:
                        cfg.max_retries = max(0, min(10, int(raw)))
                        self._save_config()
                        ui.ok(f"{t('settings.max_retries')}: {cfg.max_retries}")
                    except ValueError:
                        ui.err(t("menu.invalid"))
                await asyncio.sleep(0.7)
            elif choice == "b":
                if ui.confirm(t("settings.data_reset.confirm")):
                    db.reset_all()
                    self.dedup.reset()
                    ui.ok(t("settings.data_reset.done"))
                    await asyncio.sleep(1.2)
            elif choice == "c":
                if await self._reset_credentials():
                    return
            elif choice:
                ui.warn(t("menu.invalid"))
                await asyncio.sleep(0.6)

    def _save_config(self) -> None:
        self.prefs.save_config(self.config)

    def _set_clone_suffix(self) -> None:
        ui.blank()
        ui.info(t("settings.clone_suffix.hint"))
        raw = ui.ask(t("settings.clone_suffix.title", current=self.prefs.clone_suffix),
                     required=False)
        if raw is None:
            return
        text = raw.strip()
        if text == "-":
            suffix = ""
        elif text:
            units = len(f" {text}".encode("utf-16-le")) // 2
            if units > CLONE_SUFFIX_MAX:
                ui.err(t("settings.clone_suffix.too_long",
                         count=units, max=CLONE_SUFFIX_MAX))
                return
            suffix = text
        else:
            return
        self.prefs.set("clone_suffix", suffix)
        ui.ok(t("settings.clone_suffix.set", val=suffix))

    def _toggle_clone_photo(self) -> None:
        value = not self.prefs.clone_copy_photo
        self.prefs.set("clone_copy_photo", value)
        ui.ok(f"{t('clone.photo')}: {t('settings.on') if value else t('settings.off')}")

    def _toggle_clone_start(self) -> None:
        value = not self.prefs.clone_start_copy
        self.prefs.set("clone_start_copy", value)
        ui.ok(f"{t('clone.start_copy')}: {t('settings.on') if value else t('settings.off')}")

    def _set_delay(self) -> None:
        ui.blank()
        ui.info(t("settings.delay.hint"))
        raw = ui.ask(t("settings.delay.title", current=f"{self.config.delay:.1f}"),
                     required=False)
        if not raw:
            return
        try:
            value = max(0.0, float(raw.replace(",", ".")))
        except ValueError:
            ui.err(t("settings.delay.invalid"))
            return
        self.config.delay = value
        self._save_config()
        ui.ok(t("settings.delay.set", val=f"{value:.1f}"))

    def _apply_recommended(self) -> None:
        """Perfil recomendado: ponto de partida conservador, reduzido pelo hardware."""
        from config import recommended_settings

        values = recommended_settings()
        for key, value in values.items():
            setattr(self.config, key, value)
        self.config.__post_init__()
        self._save_config()
        ui.blank()
        ui.info(t("autoconfig.note"))
        ui.ok(t("settings.recommended.applied",
                workers=self.config.workers,
                conn=self.config.connections_per_file,
                total=self.config.max_total_connections,
                tmp=f"{self.config.max_tmp_gb:g}",
                delay=f"{self.config.delay:g}"))

    def _apply_turbo(self) -> None:
        """Perfil máximo explícito com teto conservador, sem tocar em ordem/dedup."""
        from config import turbo_settings

        values = turbo_settings()
        for key, value in values.items():
            setattr(self.config, key, value)
        self.config.__post_init__()
        self._save_config()
        ui.blank()
        ui.ok(t("settings.turbo.applied",
                workers=self.config.workers,
                conn=self.config.connections_per_file,
                total=self.config.max_total_connections,
                tmp=f"{self.config.max_tmp_gb:g}",
                delay=f"{self.config.delay:g}"))
        ui.warn(t("settings.turbo.warn"))

    def _set_connection_option(self, field: str, minimum: int, maximum: int) -> None:
        ui.blank()
        ui.info(t(f"settings.{field}.hint"))
        current = getattr(self.config, field)
        raw = ui.ask(t(f"settings.{field}.title", current=current), required=False)
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError:
            ui.err(t("menu.invalid"))
            return
        if not minimum <= value <= maximum:
            ui.err(t("menu.invalid"))
            return
        setattr(self.config, field, value)
        self._save_config()
        ui.ok(t(f"settings.{field}.set", val=value))

    def _set_tmp_limit(self) -> None:
        ui.blank()
        ui.info(t("settings.max_tmp_gb.hint"))
        raw = ui.ask(t("settings.max_tmp_gb.title", current=f"{self.config.max_tmp_gb:g}"),
                     required=False)
        if not raw:
            return
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            ui.err(t("menu.invalid"))
            return
        if value <= 0:
            ui.err(t("menu.invalid"))
            return
        self.config.max_tmp_gb = value
        self._save_config()
        ui.ok(t("settings.max_tmp_gb.set", val=f"{value:g}"))

    def _set_workers(self) -> None:
        from system_analyzer import analyzer

        ui.blank()
        ui.info(t("settings.workers.hint", rec=analyzer.recommended()))
        raw = ui.ask(t("settings.workers.title", max=WORKERS_HARD_CAP,
                       current=self.config.workers),
                     required=False)
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError:
            ui.err(t("menu.invalid"))
            return
        if not 1 <= value <= WORKERS_HARD_CAP:
            ui.err(t("menu.invalid"))
            return
        self.config.workers = value
        self._save_config()
        ui.ok(t("settings.workers.set", val=value))

    def _set_signature(self) -> None:
        ui.blank()
        ui.info(t("settings.signature.hint"))
        raw = ui.ask(t("settings.signature.option"), required=False)
        self.config.signature = raw.strip()
        self._save_config()
        ui.ok(t("settings.signature.set"))

    def _change_language(self) -> None:
        ui.blank()
        print(f"  {C.AMBER}[1]{C.RST}  {C.WHITE}{t('lang.pt')}{C.RST}")
        print(f"  {C.AMBER}[2]{C.RST}  {C.WHITE}{t('lang.en')}{C.RST}")
        raw = (ui.ask(t("lang.select"), required=False) or "").strip()
        if raw in ("1", "pt"):
            set_lang("pt")
        elif raw in ("2", "en"):
            set_lang("en")
        else:
            return
        self.prefs.set_language(get_lang())
        self.creds.update(lang=get_lang())
        ui.ok(t("settings.lang.set"))

    def _export_logs(self) -> None:
        ui.blank()
        if not LOG_FILE.exists():
            ui.warn(t("settings.logs.none"))
            return
        if not ui.confirm(t("settings.logs.export_confirm")):
            return
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        target = EXPORT_DIR / f"telecopy_{datetime.now():%Y%m%d_%H%M%S}.log"
        try:
            shutil.copy2(LOG_FILE, target)
            ui.ok(f"{t('settings.logs.done')}: {target}")
        except Exception as exc:
            ui.err(str(exc))

    def _about(self) -> None:
        ui.clear()
        ui.splash(_app.__version__, f"{t('app.tagline')} · v{_app.__version__}", t("app.tech"))
        ui.box(t("about.title"), [
            (t("about.version"), _app.__version__),
            (t("about.author"), _app.__author__),
            (t("about.license"), _app.__license__),
            (t("about.arch"), t("about.arch.val")),
            (t("about.dedup"), t("about.dedup.val")),
            (t("about.system"), f"{platform.system()} {platform.release()}"),
            (t("about.python"), platform.python_version()),
            (t("about.data_dir"), str(DATA_DIR)),
            (t("about.site"), "github.com/montx2/telecopy"),
        ], color=C.PURPLE)
        ui.pause()

    async def _reset_credentials(self) -> bool:
        ui.blank()
        ui.warn(t("settings.creds.confirm"))
        if not ui.confirm(t("settings.creds.confirm_short")):
            return False
        self.creds.clear()
        session_path = Path(str(SESSION_FILE) + ".session")
        if not session_path.exists():
            session_path = Path(str(SESSION_FILE))
        if session_path.exists():
            try:
                session_path.unlink()
            except OSError:
                pass
        if self.client is not None:
            try:
                await self.client.log_out()
            except Exception:
                pass
        ui.ok(t("settings.creds.cleared"))
        ui.pause()
        self._running = False
        return True

    # ── resultados ──────────────────────────────────────────────────────────

    def _record_result(self, result: Dict[str, Any], elapsed: float) -> None:
        if not result:
            return
        try:
            self.stats.inc("total", int(result.get("done", 0)))
            self.stats.add_bytes(int(result.get("bytes", 0)), "sent")
            self.stats.add_time(elapsed)
        except Exception:
            pass

    def _print_result(self, result: Dict[str, Any], pause: bool = True) -> None:
        if not result:
            if pause:
                ui.pause()
            return
        rows = [
            (t("result.sent"), Fmt.number(result.get("done", 0))),
            (t("result.skipped"), Fmt.number(result.get("skipped", 0))),
            (t("result.duplicates"), Fmt.number(result.get("duplicates", 0))),
            (t("result.errors"), Fmt.number(result.get("errors", 0))),
            (t("result.time"), str(result.get("elapsed", "—"))),
        ]
        if result.get("albums"):
            rows.append((t("result.albums"), Fmt.number(result["albums"])))
        if result.get("bytes"):
            rows.append((t("result.bytes"), Fmt.size(result["bytes"])))
        if result.get("speed"):
            rows.append((t("result.speed"), str(result["speed"])))
        if result.get("server_copies"):
            rows.append((t("result.server_copies"), Fmt.number(result["server_copies"])))
        if result.get("bytes_saved"):
            rows.append((t("result.bytes_saved"), Fmt.size(result["bytes_saved"])))
        if result.get("intentional_skips"):
            rows.append((t("result.intentional_skips"), Fmt.number(result["intentional_skips"])))
        if result.get("placeholders"):
            rows.append((t("result.placeholders"),
                         f"{Fmt.number(result['placeholders'])} ({t('result.placeholders.hint')})"))
        if result.get("unrecoverable"):
            rows.append((t("result.unrecoverable"), Fmt.number(result["unrecoverable"])))
        ok = (bool(result.get("complete")) and not result.get("errors")
              and not result.get("first_error") and not result.get("placeholders")
              and not result.get("unrecoverable"))
        ui.box(t("result.title"), rows, color=C.GREEN if ok else C.AMBER)
        if result:
            if not ok and result.get("placeholders"):
                ui.warn(t("copy.placeholders", count=result["placeholders"]))
                ui.warn(t("result.pending_placeholders"))
            else:
                (ui.ok if ok else ui.warn)(t("result.complete" if ok else "result.incomplete"))
        topic_rows = result.get("topics") or []
        if topic_rows:
            ui.box(t("result.topics"), [
                (str(row.get("title") or row.get("id")),
                 f"{row.get('done', 0)}/{row.get('expected', 0)}")
                for row in topic_rows
            ], color=C.GREEN if all(row.get("missing", 0) == 0 for row in topic_rows) else C.AMBER)
        if result.get("first_error"):
            ui.err(f"{t('result.reason')}: {result['first_error']}")
        if pause:
            ui.pause()
