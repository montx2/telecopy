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
import math
import platform
import re
import shutil
import time
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

import __init__ as _app
from config import (
    APP_AUTHOR,
    BACKUP_DIR,
    C,
    DAILY_LIMIT,
    DATA_DIR,
    EXPORT_DIR,
    LOG_FILE,
    SESSION_FILE,
    TEMP_DIR,
    VAULT_FILE,
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

    def __init__(self, client: TelegramClient) -> None:
        self.client = client
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
                query: str, highlight_id: Optional[int]) -> None:
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
        nav.append(f"{C.GRAY}[0]{C.RST} {t('menu.back')}")
        print(f"\n  {'   '.join(nav)}\n")

    # -- seleção ------------------------------------------------------------

    async def pick(self, label: str = "", highlight_id: Optional[int] = None):
        items = await self.load()
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

            self._render(rows, page, pages, query, highlight_id)
            raw = ui.ask(label or t("picker.select"), required=False).lower()

            if raw in ("0", ""):
                return None
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
                    return subset[index]["entity"]
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
                    request_retries=1,
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
                ("4", t("menu.tools"), t("menu.tools.desc")),
                ("5", t("menu.settings"), t("menu.settings.desc")),
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
                    await self._menu_tools()
                elif choice == "5":
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
        level, count, remaining = daily_limit.status()
        if level == "limit":
            ui.warn(t("limit.reached", count=count, limit=DAILY_LIMIT))
        elif level == "warn":
            ui.warn(t("limit.warn", count=count, limit=DAILY_LIMIT, remaining=remaining))

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

    async def _resolve_topic(self, entity) -> Optional[int]:
        """Retorna o id do tópico (ou None para todos)."""
        if not getattr(entity, "forum", False):
            return None
        ui.blank()
        ui.warn(t("picker.forum_warn"))
        raw = ui.ask(t("picker.topic_prompt"), required=False)
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        match = re.search(r"t\.me/(?:c/)?[^/]+/(?:(\d+)/)?(\d+)", raw)
        if match:
            return int(match.group(1) or match.group(2))
        ui.err(t("picker.topic_invalid"))
        return None

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

        if getattr(source, "noforwards", False):
            ui.warn(t("copy.source_protected"))
            ui.pause()
            self._pop()
            return

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
        dest_topic: Optional[int] = None
        topic_map: Optional[Dict[int, int]] = None
        dest = None
        cloner = None
        clone_plan = None
        missing_topics: Tuple[str, ...] = ()

        if destination_mode == "1":
            source_topic = await self._resolve_topic(source)
            for _ in range(3):
                candidate = await self.picker.pick(t("entity.dest"), highlight_id=source.id)
                if candidate is None:
                    self._pop()
                    return
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
            if dest is None:
                self._pop()
                return
            dest_topic = await self._resolve_topic(dest)
            if (getattr(source, "forum", False) and source_topic is None
                    and not getattr(dest, "forum", False)):
                ui.err(t("copy.clone.destination_forum_required"))
                ui.pause()
                self._pop()
                return
            if (getattr(source, "forum", False)
                    and getattr(dest, "forum", False)
                    and source_topic is None and dest_topic is None):
                from channel_clone import ChannelCloneError, ChannelCloner

                cloner = ChannelCloner(self.client, repo=db)
                try:
                    clone_plan, topic_map, missing_topics = await cloner.plan_existing(source, dest)
                    dest_topic = topic_map.get(1)
                    ui.info(t("copy.clone.map_topics"))
                except ChannelCloneError as exc:
                    ui.err(t("copy.clone.failed", reason=str(exc)))
                    ui.pause()
                    self._pop()
                    return
            self._show_entity(dest, t("entity.dest"))
        else:
            from channel_clone import ChannelCloneError, ChannelCloner

            cloner = ChannelCloner(self.client, repo=db)
            default_title = cloner.default_title(source)
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

        filter_code = await self._pick_filter() or "all"
        config = self._copy_options()
        if topic_map or clone_plan is not None:
            # Repetições entre tópicos e respostas são conteúdo legítimo.
            # Uma assinatura alteraria o texto original; hashes globais podem
            # descartar a segunda ocorrência de um aviso em outro tópico.
            config.use_dedup = False
            config.signature = ""
            ui.info(t("copy.clone.faithful_forum"))

        if clone_plan is not None and clone_plan.existing:
            rows = [
                (t("entity.source"), getattr(source, "title", str(source.id))),
                (t("entity.dest"), getattr(dest, "title", str(dest.id))),
                (t("entity.type"), t("copy.clone.kind.forum")),
                (t("copy.clone.existing_topics"), str(len(topic_map or {}))),
                (t("copy.clone.missing_topics"), str(len(missing_topics))),
            ]
        elif clone_plan is not None:
            kind = (t("copy.clone.kind.forum") if clone_plan.forum
                    else t("copy.clone.kind.channel") if clone_plan.broadcast
                    else t("copy.clone.kind.group"))
            rows = [
                (t("entity.source"), getattr(source, "title", str(source.id))),
                (t("entity.dest"), clone_plan.title),
                (t("entity.type"), kind),
                (t("copy.clone.new"), t("copy.clone.private_note")),
            ]
            if clone_plan.forum:
                topic_count = len({topic.id for topic in clone_plan.topics} | {1})
                rows.append((t("copy.clone.topics"), str(topic_count)))
        else:
            rows = [
                (t("entity.source"), getattr(source, "title", str(source.id))),
                (t("entity.dest"), getattr(dest, "title", str(dest.id))),
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

        if clone_plan is not None:
            try:
                if clone_plan.existing:
                    ui.info(t("copy.clone.preparing_existing"))
                    topic_map = await cloner.ensure_topics(clone_plan, dest, topic_map or {})
                    dest_topic = topic_map.get(1)
                    ui.ok(t("copy.clone.ready_existing"))
                else:
                    ui.info(t("copy.clone.creating"))
                    dest, topic_map = await cloner.create(clone_plan)
                    self.picker._loaded = False
                    ui.ok(t("copy.clone.created", title=clone_plan.title,
                            id=getattr(dest, "id", "?")))
                    self._show_entity(dest, t("entity.dest"))
                    if topic_map:
                        dest_topic = topic_map.get(1)
            except ChannelCloneError as exc:
                logger.exception(f"Falha ao preparar a estrutura do destino: {exc}")
                ui.err(t("copy.clone.failed", reason=str(exc)))
                if exc.destination is not None:
                    ui.warn(t("copy.clone.partial", id=getattr(exc.destination, "id", "?")))
                ui.pause()
                self._pop()
                return
            config.use_dedup = False
            config.signature = ""

        from copier import Copier

        copier = Copier(self.client, repo=db, config=config)
        result: Dict[str, Any] = {}
        started = time.time()
        try:
            result = await copier.run(
                source, dest,
                filter_code=filter_code,
                source_topic=source_topic,
                dest_topic=dest_topic,
                topic_map=topic_map,
                topic_titles=({1: "General", **{topic.id: topic.title for topic in clone_plan.topics}}
                              if clone_plan is not None else None),
            )
        except KeyboardInterrupt:
            ui.warn(t("copy.interrupted"))
        except Exception as exc:
            logger.exception("Falha na cópia")
            ui.err(f"{t('err.generic')}: {exc}")
            ui.pause()
        finally:
            if clone_plan is not None and dest is not None and topic_map:
                if result.get("complete"):
                    try:
                        setting_warnings = await cloner.apply_topic_settings(
                            clone_plan, dest, topic_map
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
                    ui.ok(f"{t('copy.mode')}: "
                          f"{t('copy.mode.forward') if cfg.forward_mode else t('copy.mode.copy')}")
                elif choice == "2":
                    raw = ui.ask(t("settings.delay.hint"), required=False)
                    try:
                        if raw:
                            cfg.delay = max(0.1, float(raw.replace(",", ".")))
                            ui.ok(t("settings.delay.set", val=f"{cfg.delay:.1f}"))
                    except ValueError:
                        ui.err(t("settings.delay.invalid"))
                elif choice == "3":
                    raw = ui.ask(t("settings.workers.title", max=8, current=cfg.workers),
                                 required=False)
                    try:
                        if raw:
                            cfg.workers = max(1, min(8, int(raw)))
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

        targets: List[Tuple[Any, Optional[int]]] = []
        seen: set = set()

        while True:
            if targets:
                ui.section(t("multi.list"))
                for index, (entity, topic) in enumerate(targets, start=1):
                    extra = f" · tópico {topic}" if topic else ""
                    ui.bullet(f"{index}. {getattr(entity, 'title', entity.id)}{extra}")
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
                    seen.discard(removed[0].id)
                    ui.ok(f"{t('multi.remove')}: {getattr(removed[0], 'title', '')}")
                continue

            entity = None
            if mode == "1":
                entity = await self.picker.pick(t("entity.dest"), highlight_id=source.id)
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

            topic = await self._resolve_topic(entity)
            targets.append((entity, topic))
            seen.add(entity.id)
            ui.ok(f"{t('multi.added')}: {getattr(entity, 'title', entity.id)}")

        if not targets:
            ui.warn(t("multi.no_dests"))
            ui.pause()
            self._pop()
            return

        filter_code = await self._pick_filter() or "all"
        if not ui.confirm(t("multi.confirm", n=len(targets))):
            self._pop()
            return

        from copier import Copier

        totals = {"done": 0, "skipped": 0, "errors": 0, "duplicates": 0, "bytes": 0}
        for index, (dest, topic) in enumerate(targets, start=1):
            ui.section(f"{t('multi.dest_label', i=index, total=len(targets))}: "
                       f"{getattr(dest, 'title', dest.id)}")
            copier = Copier(self.client, repo=db, config=self.config)
            try:
                result = await copier.run(
                    source, dest,
                    filter_code=filter_code,
                    source_topic=source_topic,
                    dest_topic=topic,
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

        ui.box(t("multi.total_sent", n=len(targets)), [
            (t("result.sent"), Fmt.number(totals["done"])),
            (t("result.skipped"), Fmt.number(totals["skipped"])),
            (t("result.errors"), Fmt.number(totals["errors"])),
            (t("result.bytes"), Fmt.size(totals["bytes"])),
        ], color=C.GREEN)
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

        dest = await self.picker.pick(t("entity.dest"), highlight_id=source.id)
        if dest is None or dest.id == source.id:
            if dest is not None:
                ui.err(t("entity.same"))
                ui.pause()
            self._pop()
            return
        dest_topic = await self._resolve_topic(dest)
        self._show_entity(dest, t("entity.dest"))

        filter_code = await self._pick_filter() or "all"
        ui.blank()
        ui.info(t("mirror.stop"))
        if not ui.confirm(t("copy.confirm")):
            self._pop()
            return

        from copier import Copier

        copier = Copier(self.client, repo=db, config=self.config)
        try:
            await copier.mirror(source, dest, filter_code=filter_code,
                                source_topic=source_topic, dest_topic=dest_topic)
        except KeyboardInterrupt:
            ui.warn(t("mirror.stopped"))
        ui.pause()
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

        level, count, remaining = daily_limit.status()
        color = C.RED if level == "limit" else (C.YELLOW if level == "warn" else C.GREEN)
        ui.box(t("tools.stats.daily"), [
            (t("tools.stats.today"), Fmt.number(count)),
            (t("tools.stats.limit"), Fmt.number(DAILY_LIMIT)),
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
        totals = {"done": 0, "skipped": 0, "errors": 0, "duplicates": 0, "bytes": 0}
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
                (t("settings.dedup"), t("settings.on") if cfg.use_dedup else t("settings.off")),
                (t("settings.resume"), t("settings.on") if cfg.resume else t("settings.off")),
                (t("settings.forward"),
                 t("copy.mode.forward") if cfg.forward_mode else t("copy.mode.copy")),
                (t("settings.signature"), cfg.signature or "—"),
                (t("settings.lang"), get_lang().upper()),
            ], color=C.INDIGO)

            ui.menu([
                ("1", t("settings.delay.option"), f"{cfg.delay:.1f}s"),
                ("2", t("settings.workers.option"), str(cfg.workers)),
                ("3", t("settings.dedup.toggle"), ""),
                ("4", t("settings.resume.toggle"), ""),
                ("5", t("settings.forward.toggle"), ""),
                ("6", t("settings.signature.option"), ""),
                ("7", t("settings.lang"), get_lang().upper()),
                ("8", t("settings.logs.export"), t("settings.logs.desc")),
                ("9", t("settings.about"), ""),
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

    def _set_delay(self) -> None:
        ui.blank()
        ui.info(t("settings.delay.hint"))
        raw = ui.ask(t("settings.delay.title", current=f"{self.config.delay:.1f}"),
                     required=False)
        if not raw:
            return
        try:
            value = max(0.1, float(raw.replace(",", ".")))
        except ValueError:
            ui.err(t("settings.delay.invalid"))
            return
        self.config.delay = value
        self._save_config()
        ui.ok(t("settings.delay.set", val=f"{value:.1f}"))

    def _set_workers(self) -> None:
        from system_analyzer import analyzer

        ui.blank()
        ui.info(t("settings.workers.hint", rec=analyzer.recommended()))
        raw = ui.ask(t("settings.workers.title", max=8, current=self.config.workers),
                     required=False)
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError:
            ui.err(t("menu.invalid"))
            return
        if not 1 <= value <= 8:
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
        ok = bool(result.get("complete")) and not result.get("errors") and not result.get("first_error")
        ui.box(t("result.title"), rows, color=C.GREEN if ok else C.AMBER)
        if result:
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
