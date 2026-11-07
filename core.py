"""
core.py — Application core: authentication, FSM, menus, tools e configuracoes.

Melhorias v3.1:
  • Wizard de onboarding para novos usuarios (PT/EN)
  • Autenticacao step-by-step com instrucoes claras
  • TODAS as ferramentas implementadas (resolve, stats, retry, dedup, maint)
  • Configuracoes completas (delay, dedup, export, reset, idioma, about)
  • Breadcrumb visual de navegacao
  • Mensagens de erro mais claras
  • Detecao automatica de recursos do sistema (workers)
  • Carregamento de idioma do instalador
  • Verificacao de atualizacoes
"""

import asyncio
import math
import os
import re
import shutil
import sys
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.types import Channel, Chat, User

from config import (
    C,
    UI,
    RunConfig,
    SESSION_FILE,
    VAULT_FILE,
    LOGS_DIR,
    TEMP_DIR,
    EXPORT_DIR,
    DAILY_LIMIT,
    ui,
)
from database import db, Status
from dedup import DedupEngine
from filters import list_filters, resolve as resolve_filter, get_name as filter_name
from i18n import t, set_lang, get_lang
from retry_queue import queue as rqueue
from security import Vault
from utils import logger, Stats, Fmt, daily_limit

import __init__ as _app

_PAGE = 10


# ==============================================================================
# ChannelPicker — seletor paginado com busca
# ==============================================================================


class ChannelPicker:
    def __init__(self, client: TelegramClient) -> None:
        self.client = client
        self._cache: List[Any] = []
        self._loaded = False

    async def _load(self) -> None:
        if self._loaded:
            return
        ui.clear()
        print(f"\n  {C.CYAN}·{C.RST}  {t('picker.loading')}…", end="", flush=True)
        self._cache = []
        async for dialog in self.client.iter_dialogs():
            ent = dialog.entity
            if isinstance(ent, (Channel, Chat)):
                is_forum = getattr(ent, "forum", False)
                is_channel = isinstance(ent, Channel) and getattr(
                    ent, "broadcast", False
                )
                ent_type = "Forum" if is_forum else ("Canal" if is_channel else "Grupo")
                self._cache.append(
                    {
                        "entity": ent,
                        "name": getattr(ent, "title", str(ent.id)),
                        "id": ent.id,
                        "type": ent_type,
                        "members": getattr(ent, "participants_count", None),
                    }
                )
        print(
            f"\r  {C.GREEN}✓{C.RST}  {len(self._cache)} {t('picker.found')}            "
        )
        self._loaded = True

    def _render(self, items, page, total_pages, query, highlight_id=None):
        ui.clear()
        sub = (
            f"{t('picker.search')}: \"{query}\"  ·  " if query else ""
        ) + f"{t('picker.page')} {page + 1}/{total_pages}"
        ui.header(t("picker.title"), sub)

        W = ui.W
        col_num, col_type, col_id, col_mem = 5, 8, 14, 10
        col_name = W - col_num - col_type - col_id - col_mem - 6

        print(
            f"  {C.DARK}#    "
            f"{'Tipo':<{col_type}}{'ID':<{col_id}}{'Membros':<{col_mem}}{'Nome':<{col_name}}{C.RST}"
        )
        print(f"  {C.DARK}{'─' * W}{C.RST}")

        start_idx = page * _PAGE + 1
        for i, ch in enumerate(items, start_idx):
            hl = highlight_id and ch["id"] == highlight_id
            nc = C.GREEN if hl else C.AMBER
            rc = C.GREEN if hl else ""

            name_raw = ch["name"]
            if hl:
                name_raw += f" {t('picker.source_hl')}"
            name_raw = name_raw[: col_name - 1]
            mem_str = str(ch["members"]) if ch["members"] is not None else "—"

            print(
                f"  {nc}{C.BOLD}[{i:<2}]{C.RST}"
                f" {rc or C.VIOLET}{ch['type']:<{col_type}}{C.RST}"
                f"{rc or C.GRAY}{str(ch['id']):<{col_id}}{C.RST}"
                f"{rc or C.SILVER}{mem_str:<{col_mem}}{C.RST}"
                f"{rc or C.WHITE}{name_raw:<{col_name}}{C.RST}"
            )

        print(f"  {C.DARK}{'─' * W}{C.RST}")

        nav = []
        if page > 0:
            nav.append(f"{C.PURPLE}[P]{C.RST} {t('picker.prev')}")
        if page < total_pages - 1:
            nav.append(f"{C.PURPLE}[N]{C.RST} {t('picker.next')}")
        nav.append(f"{C.CYAN}[/]{C.RST} {t('picker.search_btn')}")
        nav.append(f"{C.GRAY}[0]{C.RST} {t('menu.back')}")
        print(f"\n  {'   '.join(nav)}")
        ui.blank()

    async def pick(self, label: str = "", highlight_id: int = None) -> Optional[Any]:
        await self._load()
        query, page, subset = "", 0, self._cache

        while True:
            subset = (
                [c for c in self._cache if query.lower() in c["name"].lower()]
                if query
                else self._cache
            )
            total_pages = max(1, math.ceil(len(subset) / _PAGE))
            page = max(0, min(page, total_pages - 1))
            items = subset[page * _PAGE : (page + 1) * _PAGE]

            self._render(items, page, total_pages, query, highlight_id)
            sys.stdout.write(
                f"  {C.AMBER}›{C.RST} {C.WHITE}{label or t('picker.select')}{C.RST}: "
            )
            try:
                raw = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                return None

            if raw in ("0", ""):
                return None
            if raw == "n":
                page = min(page + 1, total_pages - 1)
                continue
            if raw == "p":
                page = max(page - 1, 0)
                continue
            if raw == "/":
                sys.stdout.write(f"  {C.AMBER}›{C.RST} {t('picker.search_prompt')}: ")
                try:
                    query = input().strip()
                    page = 0
                except (EOFError, KeyboardInterrupt):
                    pass
                continue
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(subset):
                    return subset[idx]["entity"]
                ui.warn(t("picker.invalid"))
                continue
            ui.warn(t("picker.invalid"))


# ==============================================================================
# CredentialManager
# ==============================================================================


class CredentialManager:
    def __init__(self) -> None:
        self._vault = Vault(VAULT_FILE)

    def load(self) -> Optional[Dict[str, Any]]:
        return self._vault.load()

    def save(self, data: Dict[str, Any]) -> None:
        self._vault.save(data)

    def exists(self) -> bool:
        return self._vault.exists()

    def clear(self) -> None:
        self._vault.delete()


# ==============================================================================
# Aplicacao
# ==============================================================================


class App:
    def __init__(self) -> None:
        self.client: Optional[TelegramClient] = None
        self.creds: CredentialManager = CredentialManager()
        self.stats: Stats = Stats()
        self.config: RunConfig = RunConfig()
        self._trail: List[str] = []
        self._running: bool = True
        self._picker: Optional[ChannelPicker] = None
        self._dedup: Optional[DedupEngine] = None
        self._memory_profile: float = 0.60  # Padrão equilibrado
        self._workers_selected: int = 4

    async def start(self) -> None:
        first_time = not self.creds.exists()

        if self._load_language_from_installer():
            pass
        elif first_time:
            self._pick_language_boot()

        ui.splash(_app.__version__)
        ui.loading_bar(t("init.loading"))
        ui.clear()

        self.stats.new_session()
        logger.info(f"Sessao iniciada — v{_app.__version__} [{get_lang()}]")

        if first_time:
            self._show_onboarding()

        if not await self._authenticate():
            ui.err(t("auth.fail"))
            return

        await self.check_updates()

        self._picker = ChannelPicker(self.client)
        self._dedup = DedupEngine(db)
        await self._main_menu()
        await self._shutdown()

    def _load_language_from_installer(self) -> bool:
        """Le o idioma selecionado no instalador (data/config.json)."""
        try:
            import json
            from pathlib import Path

            base_dir = (
                Path(sys.executable).parent
                if getattr(sys, "frozen", False)
                else Path(__file__).parent
            )
            config_path = base_dir / "data" / "config.json"

            if not config_path.exists():
                return False

            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                lang = cfg.get("language")

                if lang not in ("pt", "en"):
                    return False

                set_lang(lang)

                creds = self.creds.load() or {}
                creds["lang"] = lang
                self.creds.save(creds)

                try:
                    config_path.unlink()
                    (base_dir / "default.lang").unlink(missing_ok=True)
                except OSError:
                    pass

                return True
        except Exception:
            pass
        return False

    async def check_updates(self) -> None:
        """Verifica silenciosamente se ha nova versao no GitHub."""
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=3)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://api.github.com/repos/montx2/telecopy/releases/latest",
                    headers={"Accept": "application/vnd.github.v3+json"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        latest = (
                            data.get("tag_name", "").replace("v", "").replace("V", "")
                        )

                        if latest and latest > _app.__version__:
                            ui.blank()
                            ui.warn(
                                f"Nova versao disponivel: v{latest} (voce tem v{_app.__version__})"
                            )
                            ui.info(
                                f"Download: {data.get('html_url', 'https://github.com/montx2/telecopy')}"
                            )
                            ui.blank()
                            if ui.confirm("Abrir pagina de download agora?"):
                                import webbrowser

                                webbrowser.open(
                                    data.get(
                                        "html_url", "https://github.com/montx2/telecopy"
                                    )
                                )
        except Exception:
            pass

    def _pick_language_boot(self) -> None:
        ui.clear()
        print()
        print(
            f"  {C.ORANGE}{C.BOLD}TELECOPY{C.RST}  {C.GRAY}v{_app.__version__}{C.RST}"
        )
        print()
        print(f"  {C.SILVER}Bem-vindo! / Welcome!{C.RST}")
        print()
        print(f"  {C.AMBER}[1]{C.RST}  {C.WHITE}Portugues (Brasil){C.RST}")
        print(f"  {C.AMBER}[2]{C.RST}  {C.WHITE}English{C.RST}")
        print()

        while True:
            try:
                sys.stdout.write(f"  {C.AMBER}›{C.RST} Select / Selecione [1/2]: ")
                choice = input().strip()
            except (EOFError, KeyboardInterrupt):
                choice = "1"

            if choice == "2":
                set_lang("en")
                break
            elif choice in ("1", ""):
                set_lang("pt")
                break

    def _show_onboarding(self) -> None:
        ui.clear()
        ui.header(t("onboard.title"), t("onboard.subtitle"))

        steps = [
            (t("onboard.step1.title"), t("onboard.step1.body")),
            (t("onboard.step2.title"), t("onboard.step2.body")),
            (t("onboard.step3.title"), t("onboard.step3.body")),
            (t("onboard.step4.title"), t("onboard.step4.body")),
        ]

        for i, (title, body) in enumerate(steps, 1):
            print(f"  {C.PURPLE}{C.BOLD}  {i}.  {title}{C.RST}")
            words = body.split()
            line = ""
            for w in words:
                if len(line) + len(w) + 1 > 68:
                    print(f"      {C.SILVER}{line}{C.RST}")
                    line = w
                else:
                    line = f"{line} {w}" if line else w
            if line:
                print(f"      {C.SILVER}{line}{C.RST}")
            print()

        ui.rule("─", C.DARK)
        ui.blank()
        print(f"  {C.CYAN}·{C.RST}  {t('onboard.tip')}")
        ui.blank()
        ui.pause(t("onboard.continue"))

    async def _authenticate(self) -> bool:
        creds = self.creds.load()

        if creds:
            api_id = creds.get("api_id")
            api_hash = creds.get("api_hash")
            if creds.get("lang"):
                set_lang(creds["lang"])
        else:
            ui.clear()
            ui.header(t("auth.first_time.title"), t("auth.first_time.subtitle"))

            print(f"  {C.AMBER}{C.BOLD}{t('auth.creds_guide.title')}{C.RST}")
            print()
            for line in t("auth.creds_guide.steps").split("|"):
                print(f"  {C.CYAN}›{C.RST}  {C.SILVER}{line.strip()}{C.RST}")
            print()
            ui.rule("─", C.DARK)
            ui.blank()

            raw = ui.ask(t("auth.api_id"))
            if not raw.isdigit():
                ui.err(t("auth.api_id_invalid"))
                return False
            api_id = int(raw)
            api_hash = ui.ask(t("auth.api_hash"))
            if len(api_hash) < 30:
                ui.err(t("auth.api_hash_short"))
                return False

            self.creds.save(
                {"api_id": api_id, "api_hash": api_hash, "lang": get_lang()}
            )
            ui.ok(t("auth.creds_saved"))

        try:
            self.client = TelegramClient(str(SESSION_FILE), api_id, api_hash)
            await self.client.connect()
        except Exception as exc:
            ui.err(f"{t('auth.conn_failed')}: {exc}")
            return False

        if await self.client.is_user_authorized():
            me = await self.client.get_me()
            ui.ok(
                f"{t('auth.signed_in')}  {C.WHITE}{me.first_name}{C.RST}  {C.GRAY}({me.phone}){C.RST}"
            )
            await asyncio.sleep(0.7)
            return True

        ui.clear()
        ui.header(t("auth.sign_in.title"), t("auth.sign_in.subtitle"))
        phone = ui.ask(t("auth.phone"))

        try:
            await self.client.send_code_request(phone)
        except Exception as exc:
            ui.err(f"{t('auth.code_send_fail')}: {exc}")
            return False

        for attempt in range(3):
            ui.blank()
            ui.info(t("auth.code_hint"))
            code = ui.ask(t("auth.code", n=attempt + 1))
            try:
                await self.client.sign_in(phone, code)
                break
            except PhoneCodeInvalidError:
                ui.warn(t("auth.code_wrong"))
            except PhoneCodeExpiredError:
                ui.warn(t("auth.code_expired"))
                await self.client.send_code_request(phone)
            except SessionPasswordNeededError:
                ui.blank()
                ui.warn(t("auth.2fa_hint"))
                pw = ui.ask(t("auth.2fa"), required=True)
                try:
                    await self.client.sign_in(password=pw)
                    break
                except Exception as exc:
                    ui.err(f"{t('auth.2fa_fail')}: {exc}")
                    return False
            except Exception as exc:
                ui.err(str(exc))
                return False
        else:
            ui.err(t("auth.too_many"))
            return False

        me = await self.client.get_me()
        ui.ok(f"{t('auth.signed_in')}  {C.WHITE}{me.first_name}{C.RST}")
        await asyncio.sleep(0.6)
        return True

    # ==============================================================================
    # Configuracao Inteligente de Performance (RAM + Workers)
    # ==============================================================================

    async def _configure_performance(self) -> Tuple[int, float]:
        """Configura workers e perfil de memoria baseado no hardware detectado."""
        from system_analyzer import analyzer

        ui.clear()
        ui.header("CONFIGURACAO DE PERFORMANCE")

        p = analyzer.profile
        ram_total = p.ram_total_gb
        ram_avail = p.ram_available_gb

        # Detecta automaticamente o perfil recomendado baseado na RAM total
        if ram_total >= 16:
            default_profile = 0.60  # 60% = Equilibrado para PCs fortes
            profile_name = "Equilibrado (Recomendado)"
        elif ram_total >= 8:
            default_profile = 0.60  # 60% = Equilibrado
            profile_name = "Equilibrado (Recomendado)"
        else:
            default_profile = 0.40  # 40% = Conservador para PCs fracos
            profile_name = "Economico (Recomendado)"

        # Calcula valores reais
        conservador = min(ram_avail * 0.30, p.disk_free_gb * 0.15)
        equilibrado = min(ram_avail * 0.60, p.disk_free_gb * 0.20)
        agressivo = min(ram_avail * 0.85, p.disk_free_gb * 0.30)

        ui.box(
            "RECURSOS DETECTADOS",
            [
                ("Memoria Total", f"{ram_total:.1f} GB"),
                ("Memoria Disponivel", f"{ram_avail:.1f} GB"),
                ("Armazenamento", f"{p.disk_type} - {p.disk_free_gb:.1f}GB livre"),
                (
                    "Processadores",
                    f"{p.cpu_count_logical} logicos / {p.cpu_count_physical} fisicos",
                ),
                (
                    "Classificacao",
                    (
                        "Alto Desempenho"
                        if p.is_high_end_pc
                        else "Standard" if not p.is_low_end_pc else "Basico"
                    ),
                ),
            ],
            color=C.CYAN,
        )

        ui.section("Perfil de Memoria")
        print(f"  {C.GRAY}Define quanto da RAM usar para buffer de arquivos.{C.RST}")
        print(
            f"  {C.GRAY}Mais RAM = downloads paralelos maiores, mas risco de travamento.{C.RST}"
        )
        ui.blank()

        # Menu com recomendacao automatica destacada
        items = [
            (
                "1",
                f"{profile_name} [PADRAO]",
                f"Usa ate {min(ram_avail * default_profile, p.disk_free_gb * 0.2):.1f}GB. Otimizado para seu PC ({ram_total:.0f}GB RAM).",
            ),
            (
                "2",
                "Conservador (30% da RAM)",
                f"Usa ate {conservador:.1f}GB. Seguro para multitarefa ou PCs antigos.",
            ),
            (
                "3",
                "Agressivo (85% da RAM)",
                f"Usa ate {agressivo:.1f}GB. Maxima velocidade. FECHE outros programas!",
            ),
            ("4", "Personalizado", "Defina a porcentagem exata (10-90%)."),
        ]

        ui.menu(items)
        choice = ui.ask("Perfil de memoria", required=False) or "1"

        percent = default_profile

        if choice == "1":
            percent = default_profile
            ui.ok(f"Perfil recomendado ativado ({percent*100:.0f}%)")

        elif choice == "2":
            percent = 0.30
            ui.ok(f"Modo Conservador ativado. Limite: {conservador:.1f}GB")

        elif choice == "3":
            percent = 0.85
            ui.warn("ATENCAO: Modo Agressivo selecionado!")
            print(f"  {C.YELLOW}Isto alocara ate {agressivo:.1f}GB de RAM.{C.RST}")
            print(
                f"  {C.YELLOW}Recomendado: Feche navegadores, jogos e outros apps.{C.RST}"
            )
            ui.blank()

            if not ui.confirm("Confirmar uso agressivo?"):
                percent = default_profile
                ui.ok("Retornando ao perfil recomendado")

        elif choice == "4":
            while True:
                custom = ui.ask(f"Porcentagem da RAM disponivel (10-90)", required=True)
                try:
                    val = int(custom.replace("%", ""))
                    if 10 <= val <= 90:
                        percent = val / 100
                        uso_gb = ram_avail * percent

                        if val > 75:
                            ui.warn(f"Alerta: {val}% e considerado agressivo!")
                            if not ui.confirm(f"Confirmar uso de {uso_gb:.1f}GB?"):
                                continue
                        elif val < 30:
                            ui.info(f"Configuracao economica: {uso_gb:.1f}GB")
                        else:
                            ui.ok(f"Configurado: {uso_gb:.1f}GB ({val}%)")
                        break
                    else:
                        ui.err("Digite entre 10 e 90")
                except ValueError:
                    ui.err("Numero invalido")

        self._memory_profile = percent

        # Agora configura workers baseado na RAM que sobrou
        ui.blank()
        ram_para_workers = ram_avail * (1 - percent)  # RAM restante
        workers_sugeridos = min(int(ram_para_workers / 0.5), p.cpu_count_logical, 8)
        workers_sugeridos = max(1, workers_sugeridos)

        ui.section("Workers (Paralelismo)")
        print(
            f"  {C.GRAY}Workers processam multiplas mensagens simultaneamente.{C.RST}"
        )
        print(
            f"  {C.GRAY}Mais workers = mais rapido, mas pode desordenar mensagens.{C.RST}"
        )
        ui.blank()

        items_workers = [
            (
                "1",
                f"Automatico ({workers_sugeridos} workers) [RECOMENDADO]",
                f"Baseado em {ram_para_workers:.1f}GB RAM livre e {p.cpu_count_logical} CPUs",
            ),
            (
                "2",
                f"Sequencial (1 worker)",
                "Ordem garantida, mas mais lento. Recomendado para aulas/cursos.",
            ),
            (
                "3",
                "Maximo possivel",
                f"Usa ate {p.max_safe_workers} workers (pode desordenar)",
            ),
            ("4", "Personalizado", "Definir numero especifico"),
        ]

        ui.menu(items_workers)
        choice_w = ui.ask("Modo de processamento", required=False) or "1"

        workers = workers_sugeridos

        if choice_w == "1":
            workers = workers_sugeridos
            ui.ok(f"Workers automaticos: {workers}")

        elif choice_w == "2":
            workers = 1
            ui.ok("Modo sequencial ativado (ordem garantida)")

        elif choice_w == "3":
            workers = p.max_safe_workers
            ui.warn(f"Modo maximo: {workers} workers (pode desordenar mensagens)")

        elif choice_w == "4":
            while True:
                custom = ui.ask(
                    f"Numero de workers (1-{p.max_safe_workers})", required=True
                )
                try:
                    val = int(custom)
                    if 1 <= val <= p.max_safe_workers:
                        workers = val
                        ui.ok(f"Workers definidos: {workers}")
                        break
                    else:
                        ui.err(f"Digite entre 1 e {p.max_safe_workers}")
                except ValueError:
                    ui.err("Numero invalido")

        self._workers_selected = workers

        # Salva no vault para proximas vezes
        creds = self.creds.load() or {}
        creds["memory_profile"] = percent
        creds["workers"] = workers
        self.creds.save(creds)

        await asyncio.sleep(1.0)
        return workers, percent

    async def _analyze_channel_size(self, source) -> Optional[float]:
        """Estima tamanho do canal em GB."""
        ui.info("Analisando volume de dados (pode levar alguns segundos)...")

        sample_size = 0
        sample_bytes = 0

        try:
            async for msg in self.client.iter_messages(source, limit=30):
                if msg.media and hasattr(msg.media, "document"):
                    sample_bytes += getattr(msg.media.document, "size", 0)
                    sample_size += 1

            if sample_size == 0:
                return None

            avg_bytes = sample_bytes / sample_size

            total_msgs = 0
            async for _ in self.client.iter_messages(source, limit=0):
                total_msgs += 1

            if total_msgs == 0:
                return None

            estimated_bytes = avg_bytes * total_msgs
            return estimated_bytes / (1024**3)

        except Exception as e:
            logger.warning(f"Falha ao estimar tamanho: {e}")
            return None

    # ==============================================================================
    # Menus Principais
    # ==============================================================================

    async def _main_menu(self) -> None:
        self._trail = ["TELECOPY"]

        while self._running:
            ui.clear()
            ui.header(
                "TELECOPY", f"v{_app.__version__}  ·  {t('app.by')} {_app.__author__}"
            )

            level, count, remaining = daily_limit.status()
            if level == "limit":
                ui.warn(t("limit.reached", count=count, limit=DAILY_LIMIT))
            elif level == "warn":
                ui.warn(
                    t("limit.warn", count=count, limit=DAILY_LIMIT, remaining=remaining)
                )

            ui.menu(
                [
                    ("1", t("menu.copy"), t("menu.copy.desc")),
                    ("2", t("menu.multi"), t("menu.multi.desc")),
                    ("3", t("menu.mirror"), t("menu.mirror.desc")),
                    ("4", t("menu.tools"), t("menu.tools.desc")),
                    ("5", t("menu.settings"), t("menu.settings.desc")),
                    ("0", t("menu.exit"), ""),
                ]
            )

            choice = ui.ask(t("menu.option"), required=False) or ""

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

    async def _resolve_topic(self, ent) -> Optional[int]:
        if not getattr(ent, "forum", False):
            return None
        ui.blank()
        ui.warn(t("picker.forum_warn"))
        while True:
            raw = ui.ask(t("picker.topic_prompt"), required=False)
            if raw == "0" or not raw:
                return -1
            if raw.isdigit():
                return int(raw)
            url = raw.split("?")[0]
            url = re.sub(r"^https?://(www\.)?t\.me/", "", url)
            parts = [p for p in url.split("/") if p]
            tid = None
            if len(parts) >= 1:
                if parts[0] == "c" and len(parts) >= 3 and parts[2].isdigit():
                    tid = int(parts[2])
                elif parts[0] != "c" and len(parts) >= 2 and parts[1].isdigit():
                    tid = int(parts[1])
            if tid is not None:
                return tid
            ui.err(t("picker.topic_invalid"))

    # ==============================================================================
    # Fluxo: Copiar canal (com configuracao inteligente)
    # ==============================================================================

    async def _menu_copy(self) -> None:
        self._push(t("copy.title"))
        ui.clear()
        ui.header(t("copy.title"))
        self._trail_line()

        source = await self._pick_entity(t("entity.source"), t("copy.source_hint"))
        if source is None:
            self._pop()
            return

        source_topic = await self._resolve_topic(source)
        if source_topic == -1:
            self._pop()
            return
        self._show_entity(source, t("entity.source"))

        dest = await self._pick_entity(
            t("entity.dest"),
            t("copy.dest_hint"),
            highlight_id=getattr(source, "id", None),
        )
        if dest is None:
            self._pop()
            return

        dest_topic = await self._resolve_topic(dest)
        if dest_topic == -1:
            self._pop()
            return
        self._show_entity(dest, t("entity.dest"))

        filter_code = await self._pick_filter()
        if filter_code is None:
            self._pop()
            return

        # Configuracao de performance (Workers + Memoria)
        from copier import Copier
        from system_analyzer import analyzer

        ui.blank()
        if ui.confirm("Deseja analisar e otimizar a configuracao de performance?"):
            workers, memory_pct = await self._configure_performance()

            if ui.confirm("Estimar tamanho do canal para calibrar (10s)?"):
                est_gb = await self._analyze_channel_size(source)
                if est_gb:
                    analysis = analyzer.estimate_for_channel(est_gb)
                    if analysis["warning"]:
                        ui.warn(analysis["warning"])
                        ui.pause()
                    # Ajusta workers se o canal for muito grande
                    if est_gb > 10 and workers > 2:
                        workers = max(2, workers // 2)
                        ui.info(
                            f"Canal grande detectado. Reduzindo para {workers} workers para seguranca."
                        )
                    ui.ok(
                        f"Canal estimado em {est_gb:.1f}GB. Configuracao final: {workers} workers, {memory_pct*100:.0f}% RAM"
                    )
        else:
            # Carrega do vault ou usa padrao
            creds = self.creds.load() or {}
            workers = creds.get("workers", 4)
            memory_pct = creds.get("memory_profile", 0.60)

        ui.blank()
        resume = ui.confirm(t("copy.resume"))
        ui.blank()
        if not ui.confirm(t("copy.confirm")):
            self._pop()
            return

        copier = Copier(
            self.client,
            repo=db,
            delay=self.config.delay,
            workers=workers,
            memory_profile=memory_pct,
        )

        try:
            result = await copier.run(
                source,
                dest,
                filter_code=filter_code,
                resume=resume,
                source_topic_id=source_topic,
                dest_topic_id=dest_topic,
            )
        except KeyboardInterrupt:
            ui.warn(t("copy.cancelled"))
            result = {}

        self._print_result(result)
        self.stats.inc("total", result.get("done", 0))
        self._pop()

    async def _menu_multi(self) -> None:
        self._push(t("menu.multi"))
        ui.clear()
        ui.header(t("multi.title"))
        self._trail_line()

        source = await self._pick_entity(t("entity.source"), t("copy.source_hint"))
        if source is None:
            self._pop()
            return

        source_topic = await self._resolve_topic(source)
        if source_topic == -1:
            self._pop()
            return
        self._show_entity(source, t("entity.source"))

        dests: List[Tuple[Any, Optional[int]]] = []

        while True:
            ui.blank()
            back_label = (
                f"{t('menu.back')} / {t('multi.start')}" if dests else t("menu.back")
            )
            ui.menu(
                [
                    ("1", t("picker.from_list"), t("picker.from_list.desc")),
                    ("2", t("picker.manual"), t("picker.manual.desc")),
                    ("0", back_label, ""),
                ]
            )
            modo = ui.ask(t("multi.dest_n", n=len(dests) + 1), required=False) or "0"
            if modo == "0":
                break

            ent = None
            if modo == "1":
                ent = await self._picker.pick(
                    t("entity.dest"), highlight_id=getattr(source, "id", None)
                )
            elif modo == "2":
                raw = ui.ask(t("copy.dest"))
                if raw:
                    try:
                        ent = await self.client.get_entity(raw)
                    except Exception as exc:
                        ui.err(f"{t('copy.dest_not_found')}: {exc}")
                        ui.pause()
                        continue

            if ent is not None:
                ent_topic = await self._resolve_topic(ent)
                if ent_topic != -1:
                    dests.append((ent, ent_topic))
                    ui.ok(f"{t('multi.added')}: {getattr(ent, 'title', ent.id)}")

        if not dests:
            ui.warn(t("multi.no_dests"))
            ui.pause()
            self._pop()
            return

        filter_code = await self._pick_filter()
        if filter_code is None:
            self._pop()
            return

        if not ui.confirm(t("multi.confirm", n=len(dests))):
            self._pop()
            return

        from copier import Copier

        for i, (dest, dest_topic) in enumerate(dests, 1):
            ui.section(
                t("multi.dest_label", i=i, total=len(dests))
                + f": {getattr(dest, 'title', dest.id)}"
            )
            copier = Copier(
                self.client,
                repo=db,
                delay=self.config.delay,
                workers=self._workers_selected,
            )
            try:
                result = await copier.run(
                    source,
                    dest,
                    filter_code=filter_code,
                    source_topic_id=source_topic,
                    dest_topic_id=dest_topic,
                )
                self._print_result(result)
            except Exception as exc:
                ui.err(f"{t('multi.failed')}: {exc}")

        self._pop()

    async def _menu_mirror(self) -> None:
        self._push(t("menu.mirror"))
        ui.clear()
        ui.header(t("mirror.title"))
        self._trail_line()

        source = await self._pick_entity(t("entity.source"), t("copy.source_hint"))
        if source is None:
            self._pop()
            return

        source_topic = await self._resolve_topic(source)
        if source_topic == -1:
            self._pop()
            return
        self._show_entity(source, t("entity.source"))

        dest = await self._pick_entity(
            t("entity.dest"),
            t("copy.dest_hint"),
            highlight_id=getattr(source, "id", None),
        )
        if dest is None:
            self._pop()
            return

        dest_topic = await self._resolve_topic(dest)
        if dest_topic == -1:
            self._pop()
            return
        self._show_entity(dest, t("entity.dest"))

        filter_code = await self._pick_filter()
        if filter_code is None:
            self._pop()
            return

        from copier import Copier

        copier = Copier(
            self.client,
            repo=db,
            delay=self.config.delay,
            workers=self._workers_selected,
        )
        await copier.mirror(
            source,
            dest,
            filter_code=filter_code,
            source_topic_id=source_topic,
            dest_topic_id=dest_topic,
        )
        self._pop()

    async def _menu_tools(self) -> None:
        self._push(t("menu.tools"))
        while True:
            ui.clear()
            ui.header(t("tools.title"))
            self._trail_line()
            ui.menu(
                [
                    ("1", t("tools.resolve"), t("tools.resolve.desc")),
                    ("2", t("tools.stats"), t("tools.stats.desc")),
                    ("3", t("tools.retry"), t("tools.retry.desc")),
                    ("4", t("tools.dedup"), t("tools.dedup.desc")),
                    ("5", t("tools.maint"), t("tools.maint.desc")),
                    ("0", t("menu.back"), ""),
                ]
            )
            choice = ui.ask(t("menu.option"), required=False) or "0"
            if choice == "1":
                await self._tool_resolve()
            elif choice == "2":
                await self._tool_stats()
            elif choice == "3":
                await self._tool_retry()
            elif choice == "4":
                await self._tool_dedup()
            elif choice == "5":
                await self._tool_maint()
            elif choice == "0":
                self._pop()
                return

    async def _tool_resolve(self) -> None:
        ui.clear()
        ui.header(t("tools.resolve.title"))
        self._trail_line()
        ui.info(t("tools.resolve.hint"))
        ui.blank()

        while True:
            raw = ui.ask(t("tools.resolve.query"), required=False)
            if not raw:
                break
            try:
                ent = await self.client.get_entity(raw)
            except Exception as exc:
                ui.err(f"{t('tools.resolve.not_found')}: {exc}")
                continue

            info = self._entity_info(ent)
            rows = [
                (t("tools.resolve.name"), info["name"]),
                ("ID", str(info["id"])),
                (t("tools.resolve.type"), info["type"]),
                (
                    t("tools.resolve.access"),
                    (
                        t("tools.resolve.public")
                        if not info["private"]
                        else t("tools.resolve.private")
                    ),
                ),
            ]

            mem = getattr(ent, "participants_count", None)
            if mem is not None:
                rows.append(("Membros", Fmt.number(mem)))

            ui.box(t("tools.resolve.result"), rows, color=C.CYAN)

        ui.pause()

    async def _tool_stats(self) -> None:
        ui.clear()
        ui.header(t("tools.stats.title"))
        self._trail_line()

        s = self.stats.data
        session_rows = [
            (t("tools.stats.total"), Fmt.number(s.get("total", 0))),
            (t("tools.stats.photos"), Fmt.number(s.get("photos", 0))),
            (t("tools.stats.videos"), Fmt.number(s.get("videos", 0))),
            (t("tools.stats.audio"), Fmt.number(s.get("audio", 0))),
            (t("tools.stats.documents"), Fmt.number(s.get("documents", 0))),
            (t("tools.stats.sessions"), Fmt.number(s.get("sessions", 0))),
        ]
        if s.get("bytes_sent", 0):
            session_rows.append((t("tools.stats.bytes"), Fmt.size(s["bytes_sent"])))
        ui.box(t("tools.stats.session"), session_rows, color=C.PURPLE)

        try:
            gs = db.global_stats()
            db_rows = [
                (t("tools.stats.total"), Fmt.number(gs.get("success", 0))),
                (t("tools.stats.errors"), Fmt.number(gs.get("errors", 0))),
                (t("tools.stats.duplicates"), Fmt.number(gs.get("duplicates", 0))),
                (t("tools.stats.moved"), Fmt.size(gs.get("bytes", 0))),
                (t("tools.stats.sessions"), Fmt.number(gs.get("sessions", 0))),
            ]
            ui.box(t("tools.stats.alltime"), db_rows, color=C.INDIGO)
        except Exception:
            pass

        level, count, remaining = daily_limit.status()
        color = (
            C.RED if level == "limit" else (C.YELLOW if level == "warn" else C.GREEN)
        )
        daily_rows = [
            (t("tools.stats.today"), Fmt.number(count)),
            (t("tools.stats.limit"), Fmt.number(DAILY_LIMIT)),
            (t("tools.stats.remaining"), Fmt.number(remaining)),
        ]
        ui.box(t("tools.stats.daily"), daily_rows, color=color)
        ui.pause()

    async def _tool_retry(self) -> None:
        ui.clear()
        ui.header(t("tools.retry.title"))
        self._trail_line()

        rstats = rqueue.stats()
        retry_rows = [
            (t("tools.retry.pending"), str(rstats["pending"])),
            (t("tools.retry.exhausted"), str(rstats["exhausted"])),
            (t("tools.retry.total"), str(rstats["total"])),
        ]
        ui.box(t("tools.retry.queue"), retry_rows, color=C.AMBER)

        if rstats["exhausted"] > 0:
            if ui.confirm(t("tools.retry.clear")):
                removed = rqueue.clear_exhausted()
                ui.ok(f"{removed} {t('tools.retry.cleared')}")
        elif rstats["total"] == 0:
            ui.info("Fila vazia — nenhum item pendente.")

        ui.pause()

    async def _tool_dedup(self) -> None:
        ui.clear()
        ui.header(t("tools.dedup.title"))
        self._trail_line()

        if self._dedup is None:
            self._dedup = DedupEngine(db)

        stats = self._dedup.stats()
        lru = stats["lru"]
        bloom = stats["bloom"]

        lru_rows = [
            (
                t("tools.dedup.items"),
                f"{Fmt.number(lru['items'])} / {Fmt.number(lru['capacity'])}",
            ),
            (t("tools.dedup.usage"), f"{lru['usage_pct']}%"),
        ]
        ui.box(t("tools.dedup.lru"), lru_rows, color=C.CYAN)

        bloom_rows = [
            (t("tools.dedup.items"), Fmt.number(bloom["items"])),
            (t("tools.dedup.memory"), f"{bloom['memory_kb']} KB"),
            (t("tools.dedup.hashes"), str(bloom["hash_funcs"])),
            (t("tools.dedup.fp"), f"{bloom['fp_rate']}%"),
        ]
        ui.box(t("tools.dedup.bloom"), bloom_rows, color=C.PURPLE)
        ui.pause()

    async def _tool_maint(self) -> None:
        ui.clear()
        ui.header(t("tools.maint.title"))
        self._trail_line()

        tmp_files = list(TEMP_DIR.glob("*")) if TEMP_DIR.exists() else []
        tmp_size = sum(f.stat().st_size for f in tmp_files if f.is_file())
        if tmp_files:
            ui.info(
                f"{len(tmp_files)} {t('tools.maint.tmp_found')} ({Fmt.size(tmp_size)})"
            )
            if ui.confirm(t("tools.maint.tmp_delete")):
                removed = 0
                for f in tmp_files:
                    try:
                        f.unlink()
                        removed += 1
                    except OSError:
                        pass
                ui.ok(f"{t('tools.maint.tmp_cleared')} ({removed} arquivos)")
        else:
            ui.info(t("tools.maint.tmp_none"))

        ui.blank()

        if ui.confirm(t("tools.maint.db_prune")):
            try:
                removed = db.prune(days=30)
                ui.ok(f"{removed} {t('tools.maint.db_done')}")
            except Exception as exc:
                ui.err(str(exc))

        ui.blank()

        if ui.confirm(t("tools.maint.db_vacuum")):
            try:
                import sqlite3

                conn = sqlite3.connect(str(db.path))
                conn.execute("VACUUM")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close()
                ui.ok(t("tools.maint.db_vacuum_done"))
            except Exception as exc:
                ui.err(str(exc))

        ui.pause()

    async def _menu_settings(self) -> None:
        self._push(t("menu.settings"))
        while True:
            ui.clear()
            ui.header(t("settings.title"))
            self._trail_line()

            dedup_status = (
                t("settings.on") if self.config.use_dedup else t("settings.off")
            )
            ui.menu(
                [
                    (
                        "1",
                        t("settings.delay.option"),
                        f"{t('settings.delay')}: {self.config.delay}s",
                    ),
                    (
                        "2",
                        t("settings.dedup.toggle"),
                        f"{t('settings.dedup')}: {dedup_status}",
                    ),
                    ("3", t("settings.logs.export"), t("settings.logs.desc")),
                    ("4", t("settings.creds.reset"), t("settings.creds.desc")),
                    (
                        "5",
                        t("settings.lang"),
                        f"{t('settings.lang.current')}: {get_lang().upper()}",
                    ),
                    ("6", t("settings.about"), ""),
                    ("0", t("menu.back"), ""),
                ]
            )
            choice = ui.ask(t("menu.option"), required=False) or "0"
            if choice == "1":
                await self._set_delay()
            elif choice == "2":
                self.config.use_dedup = not self.config.use_dedup
                ui.ok(
                    f"{t('settings.dedup')}: {t('settings.on') if self.config.use_dedup else t('settings.off')}"
                )
                await asyncio.sleep(0.8)
            elif choice == "3":
                await self._export_logs()
            elif choice == "4":
                await self._reset_creds()
            elif choice == "5":
                await self._change_lang()
            elif choice == "6":
                self._about()
            elif choice == "0":
                self._pop()
                return

    async def _set_delay(self) -> None:
        ui.clear()
        ui.header(t("settings.delay.option"))
        self._trail_line()
        ui.info(t("settings.delay.hint"))
        ui.blank()

        raw = ui.ask(
            t("settings.delay.title", current=self.config.delay), required=False
        )
        if not raw:
            return
        try:
            val = float(raw.replace(",", "."))
            if val < 0.1:
                val = 0.1
            self.config.delay = val
            ui.ok(t("settings.delay.set", val=val))
        except ValueError:
            ui.err(t("settings.delay.invalid"))
        await asyncio.sleep(1.0)

    async def _export_logs(self) -> None:
        ui.clear()
        ui.header(t("settings.logs.export"))
        self._trail_line()

        log_file = LOGS_DIR / "telecopy.log"
        if not log_file.exists():
            ui.warn(t("settings.logs.none"))
            ui.pause()
            return

        size = log_file.stat().st_size
        ui.info(f"Log: {log_file}  ({Fmt.size(size)})")
        ui.blank()

        if not ui.confirm(t("settings.logs.export_confirm")):
            return

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = EXPORT_DIR / f"telecopy_{ts}.log"
        try:
            shutil.copy2(log_file, dest)
            ui.ok(f"{t('settings.logs.done')}: {dest}")
        except Exception as exc:
            ui.err(str(exc))
        ui.pause()

    async def _reset_creds(self) -> None:
        ui.clear()
        ui.header(t("settings.creds.reset"))
        self._trail_line()
        ui.warn(t("settings.creds.confirm"))
        ui.blank()

        if not ui.confirm(t("settings.creds.confirm_short")):
            return

        self.creds.clear()
        session_path = Path(str(SESSION_FILE) + ".session")
        if session_path.exists():
            try:
                session_path.unlink()
            except OSError:
                pass

        ui.ok(t("settings.creds.cleared"))
        ui.pause()
        self._running = False

    async def _change_lang(self) -> None:
        ui.clear()
        ui.header(t("settings.lang.title"))
        self._trail_line()
        ui.blank()
        print(f"  {C.AMBER}[1]{C.RST}  {C.WHITE}Portugues (Brasil){C.RST}")
        print(f"  {C.AMBER}[2]{C.RST}  {C.WHITE}English{C.RST}")
        ui.blank()

        raw = ui.ask(t("menu.option"), required=False) or ""
        if raw == "1":
            set_lang("pt")
        elif raw == "2":
            set_lang("en")

        creds = self.creds.load() or {}
        creds["lang"] = get_lang()
        self.creds.save(creds)

        ui.ok(t("settings.lang.set"))
        await asyncio.sleep(0.8)

    def _about(self) -> None:
        ui.clear()
        ui.splash(_app.__version__)

        rows = [
            (t("about.version"), _app.__version__),
            (t("about.author"), _app.__author__),
            (t("about.license"), _app.__license__),
            (t("about.arch"), t("about.arch.val")),
            (t("about.dedup"), t("about.dedup.val")),
        ]
        ui.box(t("about.title"), rows, color=C.PURPLE)
        ui.pause()

    async def _pick_entity(
        self, label: str, hint: str, highlight_id: int = None
    ) -> Optional[Any]:
        ui.info(hint)
        ui.blank()
        ui.menu(
            [
                ("1", t("picker.from_list"), t("picker.from_list.desc")),
                ("2", t("picker.manual"), t("picker.manual.desc")),
                ("0", t("menu.back"), ""),
            ]
        )
        modo = ui.ask(t("menu.option"), required=False) or "0"
        if modo == "0":
            return None

        if modo == "1":
            return await self._picker.pick(label, highlight_id=highlight_id)

        if modo == "2":
            raw = ui.ask(f"{label} (username, link ou ID)")
            if not raw:
                return None
            try:
                return await self.client.get_entity(raw)
            except Exception as exc:
                ui.err(f"{t('copy.source_not_found')}: {exc}")
                ui.pause()
                return None

        return None

    async def _pick_filter(self) -> Optional[str]:
        ui.clear()
        ui.header(t("filter.title"))
        self._trail_line()

        items = [("0", t("filter.all"), t("filter.all.desc"))]
        items += [
            (str(i + 1), name, desc)
            for i, (code, name, desc) in enumerate(list_filters())
            if code != "all"
        ]
        ui.menu(items)
        ui.info(t("filter.back_hint"))
        ui.blank()

        choice = ui.ask(t("filter.default"), required=False) or "0"
        if choice in ("b", "v", ""):
            return "all"
        if choice == "0":
            return "all"

        code = resolve_filter(choice)
        if code:
            ui.ok(f"{t('filter.selected')}: {filter_name(code)}")
            await asyncio.sleep(0.5)
            return code
        return "all"

    async def _shutdown(self) -> None:
        ui.clear()
        ui.blank()
        ui.info(t("shutdown.saving"))
        self.stats.save()
        ui.info(t("shutdown.db"))
        db.close()
        if self.client:
            ui.info(t("shutdown.disc"))
            await self.client.disconnect()
        ui.blank()
        ui.ok(t("shutdown.bye"))
        ui.blank()

    def _push(self, name: str) -> None:
        if not self._trail or self._trail[-1] != name:
            self._trail.append(name)

    def _pop(self) -> None:
        if len(self._trail) > 1:
            self._trail.pop()

    def _trail_line(self) -> None:
        if len(self._trail) > 1:
            sep = f"  {C.DARK}›{C.RST}{C.DARK}  "
            print(f"  {C.DARK}" + sep.join(self._trail) + C.RST)
            ui.blank()

    def _show_entity(self, ent, label: str) -> None:
        info = self._entity_info(ent)
        ui.box(
            label,
            [
                (t("entity.name"), info["name"]),
                (t("entity.id"), str(info["id"])),
                (t("entity.type"), info["type"]),
            ],
        )

    def _entity_info(self, ent) -> Dict[str, Any]:
        return {
            "name": getattr(ent, "title", None)
            or getattr(ent, "username", str(ent.id)),
            "id": ent.id,
            "type": type(ent).__name__,
            "private": not getattr(ent, "username", None),
        }

    def _print_result(self, result: Dict[str, Any]) -> None:
        if not result:
            return
        rows = [
            (t("result.sent"), str(result.get("done", 0))),
            (t("result.skipped"), str(result.get("skipped", 0))),
            (t("result.errors"), str(result.get("errors", 0))),
            (t("result.time"), result.get("elapsed", "—")),
        ]
        if result.get("speed"):
            rows.append(("Velocidade", result["speed"]))
        if result.get("albums", 0):
            rows.append(("Albuns", str(result["albums"])))

        ui.box(t("result.title"), rows, color=C.GREEN)
        ui.pause()

    # ==============================================================================
    # Menus
    # ==============================================================================

    async def _main_menu(self) -> None:
        self._trail = ["TELECOPY"]

        while self._running:
            ui.clear()
            ui.header(
                "TELECOPY", f"v{_app.__version__}  ·  {t('app.by')} {_app.__author__}"
            )

            level, count, remaining = daily_limit.status()
            if level == "limit":
                ui.warn(t("limit.reached", count=count, limit=DAILY_LIMIT))
            elif level == "warn":
                ui.warn(
                    t("limit.warn", count=count, limit=DAILY_LIMIT, remaining=remaining)
                )

            ui.menu(
                [
                    ("1", t("menu.copy"), t("menu.copy.desc")),
                    ("2", t("menu.multi"), t("menu.multi.desc")),
                    ("3", t("menu.mirror"), t("menu.mirror.desc")),
                    ("4", t("menu.tools"), t("menu.tools.desc")),
                    ("5", t("menu.settings"), t("menu.settings.desc")),
                    ("0", t("menu.exit"), ""),
                ]
            )

            choice = ui.ask(t("menu.option"), required=False) or ""

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

    async def _resolve_topic(self, ent) -> Optional[int]:
        if not getattr(ent, "forum", False):
            return None
        ui.blank()
        ui.warn(t("picker.forum_warn"))
        while True:
            raw = ui.ask(t("picker.topic_prompt"), required=False)
            if raw == "0" or not raw:
                return -1
            if raw.isdigit():
                return int(raw)
            url = raw.split("?")[0]
            url = re.sub(r"^https?://(www\.)?t\.me/", "", url)
            parts = [p for p in url.split("/") if p]
            tid = None
            if len(parts) >= 1:
                if parts[0] == "c" and len(parts) >= 3 and parts[2].isdigit():
                    tid = int(parts[2])
                elif parts[0] != "c" and len(parts) >= 2 and parts[1].isdigit():
                    tid = int(parts[1])
            if tid is not None:
                return tid
            ui.err(t("picker.topic_invalid"))

    # ==============================================================================
    # Fluxo: Copiar canal
    # ==============================================================================

    async def _menu_copy(self) -> None:
        self._push(t("copy.title"))
        ui.clear()
        ui.header(t("copy.title"))
        self._trail_line()

        source = await self._pick_entity(t("entity.source"), t("copy.source_hint"))
        if source is None:
            self._pop()
            return

        source_topic = await self._resolve_topic(source)
        if source_topic == -1:
            self._pop()
            return
        self._show_entity(source, t("entity.source"))

        dest = await self._pick_entity(
            t("entity.dest"),
            t("copy.dest_hint"),
            highlight_id=getattr(source, "id", None),
        )
        if dest is None:
            self._pop()
            return

        dest_topic = await self._resolve_topic(dest)
        if dest_topic == -1:
            self._pop()
            return
        self._show_entity(dest, t("entity.dest"))

        filter_code = await self._pick_filter()
        if filter_code is None:
            self._pop()
            return

        # Configuracao de workers inteligente
        from copier import Copier
        from system_analyzer import analyzer

        ui.blank()
        if ui.confirm("Deseja analisar e otimizar a configuracao de performance?"):
            workers, memory_pct = await self._configure_performance()

            if ui.confirm("Estimar tamanho do canal para calibrar (10s)?"):
                est_gb = await self._analyze_channel_size(source)
                if est_gb:
                    analysis = analyzer.estimate_for_channel(est_gb)
                    if analysis["warning"]:
                        ui.warn(analysis["warning"])
                        ui.pause()
                    workers = analysis["recommended"]
                    ui.ok(
                        f"Canal estimado em {est_gb:.1f}GB. Usando {workers} workers."
                    )
        else:
            workers = self.config.workers_dl

        ui.blank()
        resume = ui.confirm(t("copy.resume"))
        ui.blank()
        if not ui.confirm(t("copy.confirm")):
            self._pop()
            return

        copier = Copier(self.client, repo=db, delay=self.config.delay, workers=workers, memory_profile=memory_pct)
        try:
            result = await copier.run(
                source,
                dest,
                filter_code=filter_code,
                resume=resume,
                source_topic_id=source_topic,
                dest_topic_id=dest_topic,
            )
        except KeyboardInterrupt:
            ui.warn(t("copy.cancelled"))
            result = {}

        self._print_result(result)
        self.stats.inc("total", result.get("done", 0))
        self._pop()

    # ==============================================================================
    # Fluxo: Modo Espelho
    # ==============================================================================

    async def _menu_mirror(self) -> None:
        self._push(t("menu.mirror"))
        ui.clear()
        ui.header(t("mirror.title"))
        self._trail_line()

        source = await self._pick_entity(t("entity.source"), t("copy.source_hint"))
        if source is None:
            self._pop()
            return

        source_topic = await self._resolve_topic(source)
        if source_topic == -1:
            self._pop()
            return
        self._show_entity(source, t("entity.source"))

        dest = await self._pick_entity(
            t("entity.dest"),
            t("copy.dest_hint"),
            highlight_id=getattr(source, "id", None),
        )
        if dest is None:
            self._pop()
            return

        dest_topic = await self._resolve_topic(dest)
        if dest_topic == -1:
            self._pop()
            return
        self._show_entity(dest, t("entity.dest"))

        filter_code = await self._pick_filter()
        if filter_code is None:
            self._pop()
            return

        from copier import Copier

        copier = Copier(
            self.client,
            repo=db,
            delay=self.config.delay,
            workers=self.config.workers_dl,
        )
        await copier.mirror(
            source,
            dest,
            filter_code=filter_code,
            source_topic_id=source_topic,
            dest_topic_id=dest_topic,
        )
        self._pop()

    # ==============================================================================
    # Fluxo: Multi-destino
    # ==============================================================================

    async def _menu_multi(self) -> None:
        self._push(t("menu.multi"))
        ui.clear()
        ui.header(t("multi.title"))
        self._trail_line()

        source = await self._pick_entity(t("entity.source"), t("copy.source_hint"))
        if source is None:
            self._pop()
            return

        source_topic = await self._resolve_topic(source)
        if source_topic == -1:
            self._pop()
            return
        self._show_entity(source, t("entity.source"))

        dests: List[Tuple[Any, Optional[int]]] = []

        while True:
            ui.blank()
            back_label = (
                f"{t('menu.back')} / {t('multi.start')}" if dests else t("menu.back")
            )
            ui.menu(
                [
                    ("1", t("picker.from_list"), t("picker.from_list.desc")),
                    ("2", t("picker.manual"), t("picker.manual.desc")),
                    ("0", back_label, ""),
                ]
            )
            modo = ui.ask(t("multi.dest_n", n=len(dests) + 1), required=False) or "0"
            if modo == "0":
                break

            ent = None
            if modo == "1":
                ent = await self._picker.pick(
                    t("entity.dest"), highlight_id=getattr(source, "id", None)
                )
            elif modo == "2":
                raw = ui.ask(t("copy.dest"))
                if raw:
                    try:
                        ent = await self.client.get_entity(raw)
                    except Exception as exc:
                        ui.err(f"{t('copy.dest_not_found')}: {exc}")
                        ui.pause()
                        continue

            if ent is not None:
                ent_topic = await self._resolve_topic(ent)
                if ent_topic != -1:
                    dests.append((ent, ent_topic))
                    ui.ok(f"{t('multi.added')}: {getattr(ent, 'title', ent.id)}")

        if not dests:
            ui.warn(t("multi.no_dests"))
            ui.pause()
            self._pop()
            return

        filter_code = await self._pick_filter()
        if filter_code is None:
            self._pop()
            return

        if not ui.confirm(t("multi.confirm", n=len(dests))):
            self._pop()
            return

        from copier import Copier

        for i, (dest, dest_topic) in enumerate(dests, 1):
            ui.section(
                t("multi.dest_label", i=i, total=len(dests))
                + f": {getattr(dest, 'title', dest.id)}"
            )
            copier = Copier(self.client, repo=db, delay=self.config.delay)
            try:
                result = await copier.run(
                    source,
                    dest,
                    filter_code=filter_code,
                    source_topic_id=source_topic,
                    dest_topic_id=dest_topic,
                )
                self._print_result(result)
            except Exception as exc:
                ui.err(f"{t('multi.failed')}: {exc}")

        self._pop()

    # ==============================================================================
    # Ferramentas
    # ==============================================================================

    async def _menu_tools(self) -> None:
        self._push(t("menu.tools"))
        while True:
            ui.clear()
            ui.header(t("tools.title"))
            self._trail_line()
            ui.menu(
                [
                    ("1", t("tools.resolve"), t("tools.resolve.desc")),
                    ("2", t("tools.stats"), t("tools.stats.desc")),
                    ("3", t("tools.retry"), t("tools.retry.desc")),
                    ("4", t("tools.dedup"), t("tools.dedup.desc")),
                    ("5", t("tools.maint"), t("tools.maint.desc")),
                    ("0", t("menu.back"), ""),
                ]
            )
            choice = ui.ask(t("menu.option"), required=False) or "0"
            if choice == "1":
                await self._tool_resolve()
            elif choice == "2":
                await self._tool_stats()
            elif choice == "3":
                await self._tool_retry()
            elif choice == "4":
                await self._tool_dedup()
            elif choice == "5":
                await self._tool_maint()
            elif choice == "0":
                self._pop()
                return

    async def _tool_resolve(self) -> None:
        ui.clear()
        ui.header(t("tools.resolve.title"))
        self._trail_line()
        ui.info(t("tools.resolve.hint"))
        ui.blank()

        while True:
            raw = ui.ask(t("tools.resolve.query"), required=False)
            if not raw:
                break
            try:
                ent = await self.client.get_entity(raw)
            except Exception as exc:
                ui.err(f"{t('tools.resolve.not_found')}: {exc}")
                continue

            info = self._entity_info(ent)
            rows = [
                (t("tools.resolve.name"), info["name"]),
                ("ID", str(info["id"])),
                (t("tools.resolve.type"), info["type"]),
                (
                    t("tools.resolve.access"),
                    (
                        t("tools.resolve.public")
                        if not info["private"]
                        else t("tools.resolve.private")
                    ),
                ),
            ]

            mem = getattr(ent, "participants_count", None)
            if mem is not None:
                rows.append(("Membros", Fmt.number(mem)))

            ui.box(t("tools.resolve.result"), rows, color=C.CYAN)

        ui.pause()

    async def _tool_stats(self) -> None:
        ui.clear()
        ui.header(t("tools.stats.title"))
        self._trail_line()

        s = self.stats.data
        session_rows = [
            (t("tools.stats.total"), Fmt.number(s.get("total", 0))),
            (t("tools.stats.photos"), Fmt.number(s.get("photos", 0))),
            (t("tools.stats.videos"), Fmt.number(s.get("videos", 0))),
            (t("tools.stats.audio"), Fmt.number(s.get("audio", 0))),
            (t("tools.stats.documents"), Fmt.number(s.get("documents", 0))),
            (t("tools.stats.sessions"), Fmt.number(s.get("sessions", 0))),
        ]
        if s.get("bytes_sent", 0):
            session_rows.append((t("tools.stats.bytes"), Fmt.size(s["bytes_sent"])))
        ui.box(t("tools.stats.session"), session_rows, color=C.PURPLE)

        try:
            gs = db.global_stats()
            db_rows = [
                (t("tools.stats.total"), Fmt.number(gs.get("success", 0))),
                (t("tools.stats.errors"), Fmt.number(gs.get("errors", 0))),
                (t("tools.stats.duplicates"), Fmt.number(gs.get("duplicates", 0))),
                (t("tools.stats.moved"), Fmt.size(gs.get("bytes", 0))),
                (t("tools.stats.sessions"), Fmt.number(gs.get("sessions", 0))),
            ]
            ui.box(t("tools.stats.alltime"), db_rows, color=C.INDIGO)
        except Exception:
            pass

        level, count, remaining = daily_limit.status()
        color = (
            C.RED if level == "limit" else (C.YELLOW if level == "warn" else C.GREEN)
        )
        daily_rows = [
            (t("tools.stats.today"), Fmt.number(count)),
            (t("tools.stats.limit"), Fmt.number(DAILY_LIMIT)),
            (t("tools.stats.remaining"), Fmt.number(remaining)),
        ]
        ui.box(t("tools.stats.daily"), daily_rows, color=color)
        ui.pause()

    async def _tool_retry(self) -> None:
        ui.clear()
        ui.header(t("tools.retry.title"))
        self._trail_line()

        rstats = rqueue.stats()
        retry_rows = [
            (t("tools.retry.pending"), str(rstats["pending"])),
            (t("tools.retry.exhausted"), str(rstats["exhausted"])),
            (t("tools.retry.total"), str(rstats["total"])),
        ]
        ui.box(t("tools.retry.queue"), retry_rows, color=C.AMBER)

        if rstats["exhausted"] > 0:
            if ui.confirm(t("tools.retry.clear")):
                removed = rqueue.clear_exhausted()
                ui.ok(f"{removed} {t('tools.retry.cleared')}")
        elif rstats["total"] == 0:
            ui.info("Fila vazia — nenhum item pendente.")

        ui.pause()

    async def _tool_dedup(self) -> None:
        ui.clear()
        ui.header(t("tools.dedup.title"))
        self._trail_line()

        if self._dedup is None:
            self._dedup = DedupEngine(db)

        stats = self._dedup.stats()
        lru = stats["lru"]
        bloom = stats["bloom"]

        lru_rows = [
            (
                t("tools.dedup.items"),
                f"{Fmt.number(lru['items'])} / {Fmt.number(lru['capacity'])}",
            ),
            (t("tools.dedup.usage"), f"{lru['usage_pct']}%"),
        ]
        ui.box(t("tools.dedup.lru"), lru_rows, color=C.CYAN)

        bloom_rows = [
            (t("tools.dedup.items"), Fmt.number(bloom["items"])),
            (t("tools.dedup.memory"), f"{bloom['memory_kb']} KB"),
            (t("tools.dedup.hashes"), str(bloom["hash_funcs"])),
            (t("tools.dedup.fp"), f"{bloom['fp_rate']}%"),
        ]
        ui.box(t("tools.dedup.bloom"), bloom_rows, color=C.PURPLE)
        ui.pause()

    async def _tool_maint(self) -> None:
        ui.clear()
        ui.header(t("tools.maint.title"))
        self._trail_line()

        tmp_files = list(TEMP_DIR.glob("*")) if TEMP_DIR.exists() else []
        tmp_size = sum(f.stat().st_size for f in tmp_files if f.is_file())
        if tmp_files:
            ui.info(
                f"{len(tmp_files)} {t('tools.maint.tmp_found')} ({Fmt.size(tmp_size)})"
            )
            if ui.confirm(t("tools.maint.tmp_delete")):
                removed = 0
                for f in tmp_files:
                    try:
                        f.unlink()
                        removed += 1
                    except OSError:
                        pass
                ui.ok(f"{t('tools.maint.tmp_cleared')} ({removed} arquivos)")
        else:
            ui.info(t("tools.maint.tmp_none"))

        ui.blank()

        if ui.confirm(t("tools.maint.db_prune")):
            try:
                removed = db.prune(days=30)
                ui.ok(f"{removed} {t('tools.maint.db_done')}")
            except Exception as exc:
                ui.err(str(exc))

        ui.blank()

        if ui.confirm(t("tools.maint.db_vacuum")):
            try:
                import sqlite3

                conn = sqlite3.connect(str(db.path))
                conn.execute("VACUUM")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close()
                ui.ok(t("tools.maint.db_vacuum_done"))
            except Exception as exc:
                ui.err(str(exc))

        ui.pause()

    # ==============================================================================
    # Configuracoes
    # ==============================================================================

    async def _menu_settings(self) -> None:
        self._push(t("menu.settings"))
        while True:
            ui.clear()
            ui.header(t("settings.title"))
            self._trail_line()

            dedup_status = (
                t("settings.on") if self.config.use_dedup else t("settings.off")
            )
            ui.menu(
                [
                    (
                        "1",
                        t("settings.delay.option"),
                        f"{t('settings.delay')}: {self.config.delay}s",
                    ),
                    (
                        "2",
                        t("settings.dedup.toggle"),
                        f"{t('settings.dedup')}: {dedup_status}",
                    ),
                    ("3", t("settings.logs.export"), t("settings.logs.desc")),
                    ("4", t("settings.creds.reset"), t("settings.creds.desc")),
                    (
                        "5",
                        t("settings.lang"),
                        f"{t('settings.lang.current')}: {get_lang().upper()}",
                    ),
                    ("6", t("settings.about"), ""),
                    ("0", t("menu.back"), ""),
                ]
            )
            choice = ui.ask(t("menu.option"), required=False) or "0"
            if choice == "1":
                await self._set_delay()
            elif choice == "2":
                self.config.use_dedup = not self.config.use_dedup
                ui.ok(
                    f"{t('settings.dedup')}: {t('settings.on') if self.config.use_dedup else t('settings.off')}"
                )
                await asyncio.sleep(0.8)
            elif choice == "3":
                await self._export_logs()
            elif choice == "4":
                await self._reset_creds()
            elif choice == "5":
                await self._change_lang()
            elif choice == "6":
                self._about()
            elif choice == "0":
                self._pop()
                return

    async def _set_delay(self) -> None:
        ui.clear()
        ui.header(t("settings.delay.option"))
        self._trail_line()
        ui.info(t("settings.delay.hint"))
        ui.blank()

        raw = ui.ask(
            t("settings.delay.title", current=self.config.delay), required=False
        )
        if not raw:
            return
        try:
            val = float(raw.replace(",", "."))
            if val < 0.1:
                val = 0.1
            self.config.delay = val
            ui.ok(t("settings.delay.set", val=val))
        except ValueError:
            ui.err(t("settings.delay.invalid"))
        await asyncio.sleep(1.0)

    async def _export_logs(self) -> None:
        ui.clear()
        ui.header(t("settings.logs.export"))
        self._trail_line()

        log_file = LOGS_DIR / "telecopy.log"
        if not log_file.exists():
            ui.warn(t("settings.logs.none"))
            ui.pause()
            return

        size = log_file.stat().st_size
        ui.info(f"Log: {log_file}  ({Fmt.size(size)})")
        ui.blank()

        if not ui.confirm(t("settings.logs.export_confirm")):
            return

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = EXPORT_DIR / f"telecopy_{ts}.log"
        try:
            shutil.copy2(log_file, dest)
            ui.ok(f"{t('settings.logs.done')}: {dest}")
        except Exception as exc:
            ui.err(str(exc))
        ui.pause()

    async def _reset_creds(self) -> None:
        ui.clear()
        ui.header(t("settings.creds.reset"))
        self._trail_line()
        ui.warn(t("settings.creds.confirm"))
        ui.blank()

        if not ui.confirm(t("settings.creds.confirm_short")):
            return

        self.creds.clear()
        session_path = Path(str(SESSION_FILE) + ".session")
        if session_path.exists():
            try:
                session_path.unlink()
            except OSError:
                pass

        ui.ok(t("settings.creds.cleared"))
        ui.pause()
        self._running = False

    async def _change_lang(self) -> None:
        ui.clear()
        ui.header(t("settings.lang.title"))
        self._trail_line()
        ui.blank()
        print(f"  {C.AMBER}[1]{C.RST}  {C.WHITE}Portugues (Brasil){C.RST}")
        print(f"  {C.AMBER}[2]{C.RST}  {C.WHITE}English{C.RST}")
        ui.blank()

        raw = ui.ask(t("menu.option"), required=False) or ""
        if raw == "1":
            set_lang("pt")
        elif raw == "2":
            set_lang("en")

        creds = self.creds.load() or {}
        creds["lang"] = get_lang()
        self.creds.save(creds)

        ui.ok(t("settings.lang.set"))
        await asyncio.sleep(0.8)

    def _about(self) -> None:
        ui.clear()
        ui.splash(_app.__version__)

        rows = [
            (t("about.version"), _app.__version__),
            (t("about.author"), _app.__author__),
            (t("about.license"), _app.__license__),
            (t("about.arch"), t("about.arch.val")),
            (t("about.dedup"), t("about.dedup.val")),
        ]
        ui.box(t("about.title"), rows, color=C.PURPLE)
        ui.pause()

    # ==============================================================================
    # Seletor generico de entidade
    # ==============================================================================

    async def _pick_entity(
        self, label: str, hint: str, highlight_id: int = None
    ) -> Optional[Any]:
        ui.info(hint)
        ui.blank()
        ui.menu(
            [
                ("1", t("picker.from_list"), t("picker.from_list.desc")),
                ("2", t("picker.manual"), t("picker.manual.desc")),
                ("0", t("menu.back"), ""),
            ]
        )
        modo = ui.ask(t("menu.option"), required=False) or "0"
        if modo == "0":
            return None

        if modo == "1":
            return await self._picker.pick(label, highlight_id=highlight_id)

        if modo == "2":
            raw = ui.ask(f"{label} (username, link ou ID)")
            if not raw:
                return None
            try:
                return await self.client.get_entity(raw)
            except Exception as exc:
                ui.err(f"{t('copy.source_not_found')}: {exc}")
                ui.pause()
                return None

        return None

    async def _pick_filter(self) -> Optional[str]:
        ui.clear()
        ui.header(t("filter.title"))
        self._trail_line()

        items = [("0", t("filter.all"), t("filter.all.desc"))]
        items += [
            (str(i + 1), name, desc)
            for i, (code, name, desc) in enumerate(list_filters())
            if code != "all"
        ]
        ui.menu(items)
        ui.info(t("filter.back_hint"))
        ui.blank()

        choice = ui.ask(t("filter.default"), required=False) or "0"
        if choice in ("b", "v", ""):
            return "all"
        if choice == "0":
            return "all"

        code = resolve_filter(choice)
        if code:
            ui.ok(f"{t('filter.selected')}: {filter_name(code)}")
            await asyncio.sleep(0.5)
            return code
        return "all"

    async def _shutdown(self) -> None:
        ui.clear()
        ui.blank()
        ui.info(t("shutdown.saving"))
        self.stats.save()
        ui.info(t("shutdown.db"))
        db.close()
        if self.client:
            ui.info(t("shutdown.disc"))
            await self.client.disconnect()
        ui.blank()
        ui.ok(t("shutdown.bye"))
        ui.blank()

    def _push(self, name: str) -> None:
        if not self._trail or self._trail[-1] != name:
            self._trail.append(name)

    def _pop(self) -> None:
        if len(self._trail) > 1:
            self._trail.pop()

    def _trail_line(self) -> None:
        if len(self._trail) > 1:
            sep = f"  {C.DARK}›{C.RST}{C.DARK}  "
            print(f"  {C.DARK}" + sep.join(self._trail) + C.RST)
            ui.blank()

    def _show_entity(self, ent, label: str) -> None:
        info = self._entity_info(ent)
        ui.box(
            label,
            [
                (t("entity.name"), info["name"]),
                (t("entity.id"), str(info["id"])),
                (t("entity.type"), info["type"]),
            ],
        )

    def _entity_info(self, ent) -> Dict[str, Any]:
        return {
            "name": getattr(ent, "title", None)
            or getattr(ent, "username", str(ent.id)),
            "id": ent.id,
            "type": type(ent).__name__,
            "private": not getattr(ent, "username", None),
        }

    def _print_result(self, result: Dict[str, Any]) -> None:
        if not result:
            return
        rows = [
            (t("result.sent"), str(result.get("done", 0))),
            (t("result.skipped"), str(result.get("skipped", 0))),
            (t("result.errors"), str(result.get("errors", 0))),
            (t("result.time"), result.get("elapsed", "—")),
        ]
        if result.get("speed"):
            rows.append(("Velocidade", result["speed"]))
        if result.get("albums", 0):
            rows.append(("Albuns", str(result["albums"])))

        ui.box(t("result.title"), rows, color=C.GREEN)
        ui.pause()
