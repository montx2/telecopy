"""
config.py — Configuração central, constantes e primitivas de interface.

Os scripts guardam dados junto ao projeto; builds empacotados usam uma pasta
estável de dados por usuário, sem depender do diretório atual nem da localização
do executável.
"""

from __future__ import annotations

import os
import re
import sys
import textwrap
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# ── Caminhos do programa e dos dados ─────────────────────────────────────────

APP_NAME = "TELECOPY"
APP_AUTHOR = "montx2"


def _is_frozen_build() -> bool:
    """Detecta PyInstaller e Nuitka, incluindo compilações one-file."""
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def get_base_dir() -> Path:
    """Diretório onde o programa está instalado."""
    if _is_frozen_build():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _user_data_root(env: dict[str, str], system: str, home: Path) -> Path:
    """Diretório de dados por usuário para builds empacotados."""
    if system.startswith("win"):
        root = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(root) / APP_NAME
    if system == "darwin":
        return home / "Library" / "Application Support" / APP_NAME

    root = env.get("XDG_DATA_HOME")
    data_home = Path(root) if root and Path(root).is_absolute() else home / ".local" / "share"
    return data_home / APP_NAME.lower()


def _migrate_legacy_data(source: Path, destination: Path) -> bool:
    """Copia os dados antigos ao lado do executável para o local estável."""
    import shutil

    staging = destination.with_name(destination.name + ".migrating")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(source, staging)
        if destination.exists():
            if any(destination.iterdir()):
                shutil.rmtree(staging)
                return True
            destination.rmdir()
        os.replace(staging, destination)
        return True
    except OSError:
        try:
            if staging.exists():
                shutil.rmtree(staging)
        except OSError:
            pass
        return False


def get_data_dir(
    base_dir: Path | None = None,
    *,
    frozen: bool | None = None,
    env: dict[str, str] | None = None,
    system: str | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve um diretório persistente e gravável para os dados do app.

    Scripts Python continuam usando ``./data`` junto ao projeto. Executáveis
    usam o diretório de dados do usuário, que não muda quando o .exe é movido.
    Dados de versões portáteis antigas são migrados uma vez, sem apagá-los.
    """
    base = Path(base_dir) if base_dir is not None else get_base_dir()
    is_frozen = _is_frozen_build() if frozen is None else frozen
    legacy = base / "data"
    if not is_frozen:
        return legacy

    environ = os.environ if env is None else env
    platform_name = sys.platform if system is None else system
    user_home = Path.home() if home is None else Path(home)
    preferred = _user_data_root(environ, platform_name, user_home) / "data"

    try:
        same_path = os.path.normcase(str(legacy.resolve())) == os.path.normcase(
            str(preferred.resolve())
        )
    except OSError:
        same_path = legacy == preferred
    if same_path:
        return preferred

    # Se um usuário já executava uma versão portátil, preserva a sessão,
    # credenciais, banco e preferências no novo local estável.
    if legacy.is_dir():
        try:
            destination_has_data = preferred.exists() and any(preferred.iterdir())
        except OSError:
            destination_has_data = True
        if not destination_has_data and not _migrate_legacy_data(legacy, preferred):
            # Se não foi possível migrar, mantém o diretório antigo em uso.
            return legacy
    return preferred


BASE_DIR = get_base_dir()
DATA_DIR = get_data_dir(BASE_DIR)
TEMP_DIR = DATA_DIR / "tmp"
LOGS_DIR = DATA_DIR / "logs"
VAULT_DIR = DATA_DIR / "vault"
EXPORT_DIR = DATA_DIR / "exports"
BACKUP_DIR = DATA_DIR / "backups"

CONFIG_FILE = DATA_DIR / "config.json"
LOCK_FILE = DATA_DIR / "telecopy.lock"
SESSION_FILE = DATA_DIR / "session"
DB_FILE = DATA_DIR / "telecopy.db"
STATS_FILE = DATA_DIR / "stats.json"
DAILY_FILE = DATA_DIR / "daily.json"
QUEUE_FILE = DATA_DIR / "queue.json"
VAULT_FILE = VAULT_DIR / "credentials.enc"
LOG_FILE = LOGS_DIR / "telecopy.log"

# ── Limites ───────────────────────────────────────────────────────────────────

DAILY_LIMIT = 1000
DAILY_WARN = 800
MAX_RETRIES = 5

# Telegram rejeita legendas acima de 1024 caracteres e mensagens acima de 4096.
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
# Arquivos acima deste tamanho são enviados como documento.
MAX_PHOTO_BYTES = 10 * 1024 * 1024

PROGRESS_INTERVAL = 0.4

# ── Deduplicação ──────────────────────────────────────────────────────────────

BLOOM_CAPACITY = 1_000_000
BLOOM_FP_RATE = 0.001
LRU_SIZE = 50_000

# ── Detectção de recursos do sistema ─────────────────────────────────────────


@dataclass
class SystemProfile:
    """Perfil de hardware usado para calibrar a performance."""

    ram_total_gb: float = 4.0
    ram_available_gb: float = 2.0
    ram_percent: float = 50.0
    disk_free_gb: float = 10.0
    disk_total_gb: float = 20.0
    cpu_logical: int = 2
    cpu_physical: int = 1
    is_low_end: bool = False
    is_high_end: bool = False

    @property
    def label(self) -> str:
        if self.is_high_end:
            return "Alto desempenho"
        if self.is_low_end:
            return "Básico"
        return "Padrão"


def detect_system() -> SystemProfile:
    """Coleta RAM, disco e CPU sem nunca levantar exceção."""
    try:
        import psutil

        mem = psutil.virtual_memory()
        ram_total = mem.total / (1024**3)
        ram_avail = mem.available / (1024**3)
        ram_pct = float(mem.percent)

        try:
            disk = psutil.disk_usage(str(DATA_DIR if DATA_DIR.exists() else BASE_DIR))
            disk_free = disk.free / (1024**3)
            disk_total = disk.total / (1024**3)
        except Exception:
            disk_free, disk_total = 10.0, 20.0

        cpu_log = psutil.cpu_count(logical=True) or 2
        cpu_phys = psutil.cpu_count(logical=False) or 1
    except Exception:
        ram_total, ram_avail, ram_pct = 4.0, 2.0, 50.0
        disk_free, disk_total = 10.0, 20.0
        cpu_log, cpu_phys = 2, 1

    return SystemProfile(
        ram_total_gb=round(ram_total, 2),
        ram_available_gb=round(ram_avail, 2),
        ram_percent=ram_pct,
        disk_free_gb=round(disk_free, 2),
        disk_total_gb=round(disk_total, 2),
        cpu_logical=cpu_log,
        cpu_physical=cpu_phys,
        is_low_end=(ram_total < 4 or cpu_log < 4 or disk_free < 10),
        is_high_end=(ram_total >= 16 and cpu_log >= 8 and disk_free >= 50),
    )


SYSTEM = detect_system()

# Valores de performance (recalculados em runtime por RunConfig)
CHUNK_SIZE = 512 * 1024
MAX_WORKERS_DL = max(1, min(SYSTEM.cpu_logical, 8))
MAX_WORKERS_UP = max(1, MAX_WORKERS_DL // 2)
BUFFER_RAM_MAX = int(SYSTEM.ram_available_gb * 0.6 * 1024**3)


def recommended_workers(ram_available_gb: float | None = None,
                        memory_profile: float = 0.6) -> int:
    """
    Número de workers seguro: 1 worker para cada 0.5 GB de RAM que sobra
    depois de reservar o buffer de memória, limitado pela CPU.
    """
    ram = SYSTEM.ram_available_gb if ram_available_gb is None else ram_available_gb
    free_after_buffer = max(0.25, ram * (1.0 - memory_profile))
    by_ram = int(free_after_buffer / 0.5)
    by_cpu = max(1, SYSTEM.cpu_logical)
    return max(1, min(by_ram, by_cpu, 8))


# ── Diretórios ────────────────────────────────────────────────────────────────


def init_dirs() -> None:
    """Cria os diretórios de trabalho com permissões restritas."""
    for d in (DATA_DIR, TEMP_DIR, LOGS_DIR, VAULT_DIR, EXPORT_DIR, BACKUP_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(d, 0o700)
        except OSError:
            pass


init_dirs()


# ── Utilidades de texto / terminal ────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean_len(text: str) -> int:
    """Comprimento visível do texto (ignora códigos ANSI)."""
    return len(_ANSI_RE.sub("", text))


def display_width(text: str) -> int:
    """Largura visual considerando caracteres de largura dupla."""
    width = 0
    for ch in _ANSI_RE.sub("", text):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def term_columns(default: int = 88) -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return default


def supports_color() -> bool:
    """Detecta se o terminal aceita ANSI (Windows 10+ moderno / redirect)."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return os.environ.get("TERM", "") not in ("", "dumb")
    # Windows: habilita VT mode explicitamente
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
    except Exception:
        pass
    return False


# ── Paleta de cores ───────────────────────────────────────────────────────────


class C:
    """Constantes ANSI. Ficam vazias automaticamente se a cor não é suportada."""

    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    ORANGE = "\033[38;5;208m"
    AMBER = "\033[38;5;214m"
    GOLD = "\033[38;5;220m"
    PURPLE = "\033[38;5;99m"
    VIOLET = "\033[38;5;141m"
    INDIGO = "\033[38;5;61m"

    GREEN = "\033[38;5;82m"
    YELLOW = "\033[38;5;226m"
    RED = "\033[38;5;196m"
    CYAN = "\033[38;5;87m"
    BLUE = "\033[38;5;75m"

    WHITE = "\033[38;5;255m"
    SILVER = "\033[38;5;250m"
    GRAY = "\033[38;5;244m"
    DARK = "\033[38;5;238m"


_COLOR_NAMES = [
    "RST", "BOLD", "DIM", "ORANGE", "AMBER", "GOLD", "PURPLE", "VIOLET",
    "INDIGO", "GREEN", "YELLOW", "RED", "CYAN", "BLUE", "WHITE", "SILVER",
    "GRAY", "DARK",
]


def _disable_colors() -> None:
    for name in _COLOR_NAMES:
        setattr(C, name, "")


COLOR = supports_color()
if not COLOR:
    _disable_colors()


# ── Interface de terminal ─────────────────────────────────────────────────────


class UI:
    """
    Interface de terminal enxuta, com 76 colunas fixas de conteúdo.
    Funciona igual em Windows, Linux e macOS, com ou sem cor.
    """

    W = 76

    def __init__(self) -> None:
        self._rich = None
        try:
            from rich.console import Console

            self._rich = Console(force_terminal=COLOR or None, highlight=False)
        except Exception:
            self._rich = None

    # -- básicos ------------------------------------------------------------

    @staticmethod
    def clear() -> None:
        if os.name == "nt":
            os.system("cls")
        else:
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()

    @staticmethod
    def _w(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    @staticmethod
    def _wln(text: str) -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def pad(self, text: str, width: int | None = None, fill: str = " ") -> str:
        w = (width or self.W) - 4
        return text + fill * max(0, w - display_width(text))

    def center(self, text: str, width: int | None = None) -> str:
        w = width or self.W
        n = display_width(text)
        left = max(0, (w - n) // 2)
        return " " * left + text

    def blank(self) -> None:
        print()

    # -- estrutura ----------------------------------------------------------

    def rule(self, char: str = "─", color: str | None = None) -> None:
        col = color or C.INDIGO
        self._wln(f"  {col}{char * self.W}{C.RST}")

    def divider(self) -> None:
        self.rule("─", C.DARK)

    def header(self, title: str, subtitle: str = "") -> None:
        self.blank()
        self.rule("═", C.PURPLE)
        title_fmt = f"{C.BOLD}{C.ORANGE}{title}{C.RST}"
        self._wln(f"  {C.PURPLE}║{C.RST}  {self.pad(title_fmt, self.W)}{C.PURPLE}║{C.RST}")
        if subtitle:
            sub_fmt = f"{C.GRAY}{subtitle}{C.RST}"
            self._wln(f"  {C.PURPLE}║{C.RST}  {self.pad(sub_fmt, self.W)}{C.PURPLE}║{C.RST}")
        self.rule("═", C.PURPLE)
        self.blank()

    def section(self, title: str) -> None:
        self.blank()
        self._wln(f"  {C.AMBER}{C.BOLD}{title}{C.RST}")
        self._wln(f"  {C.DARK}{'─' * display_width(title)}{C.RST}")

    # -- mensagens ----------------------------------------------------------

    def _message(self, marker: str, msg: str, marker_color: str,
                 text_color: str = C.WHITE) -> None:
        plain = _ANSI_RE.sub("", str(msg))
        width = max(30, min(self.W - 4, term_columns(default=88) - 8))
        lines = textwrap.wrap(
            plain, width=width, break_long_words=True, break_on_hyphens=False
        ) or [""]
        for index, line in enumerate(lines):
            if index == 0 and marker:
                prefix = f"  {marker_color}{marker}{C.RST}  "
            elif index == 0:
                prefix = f"  {marker_color}"
            else:
                prefix = "    "
            self._wln(f"{prefix}{text_color}{line}{C.RST}")

    def ok(self, msg: str) -> None:
        self._message("✓", msg, C.GREEN)

    def err(self, msg: str) -> None:
        self._message("✗", msg, C.RED)

    def warn(self, msg: str) -> None:
        self._message("!", msg, C.YELLOW)

    def info(self, msg: str) -> None:
        self._message("·", msg, C.CYAN)

    def step(self, msg: str) -> None:
        self._message("›", msg, C.PURPLE)

    def text(self, msg: str, color: str = "") -> None:
        self._message("", msg, color or C.SILVER, color or C.SILVER)

    def bullet(self, msg: str) -> None:
        self._message("•", msg, C.DARK, C.SILVER)

    # -- entrada ------------------------------------------------------------

    @staticmethod
    def _tr(key: str, default: str) -> str:
        try:
            from i18n import t as _t

            return _t(key)
        except Exception:
            return default

    def _prompt(self, prompt: str, suffix: str = ": ") -> None:
        """Imprime prompts longos em linhas alinhadas, sem estourar o terminal."""
        width = max(30, min(self.W, term_columns(default=88) - 8))
        lines = textwrap.wrap(
            str(prompt), width=width, break_long_words=True, break_on_hyphens=False
        ) or [""]
        for index, line in enumerate(lines):
            last = index == len(lines) - 1
            prefix = (f"  {C.AMBER}›{C.RST} {C.WHITE}" if index == 0
                      else f"    {C.WHITE}")
            rendered = f"{prefix}{line}{C.RST}{suffix if last else ''}"
            if last:
                self._w(rendered)
            else:
                self._wln(rendered)

    def ask(self, prompt: str, required: bool = True) -> str:
        req_msg = self._tr("prompt.required", "Este campo é obrigatório.")
        while True:
            try:
                self._prompt(prompt)
                value = input().strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return ""
            if value or not required:
                return value
            self.warn(req_msg)

    def secret(self, prompt: str, required: bool = True) -> str:
        """Leitura de senha sem eco (fallback: pergunta normal)."""
        import getpass

        while True:
            try:
                self._prompt(prompt)
                value = getpass.getpass("").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return ""
            except Exception:
                try:
                    value = input().strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return ""
            if value or not required:
                return value
            self.warn(self._tr("prompt.required", "Este campo é obrigatório."))

    def confirm(self, prompt: str, default: bool = False) -> bool:
        yn = self._tr("prompt.yes_no", "[s/N]" if default is False else "[S/n]")
        yes_chars = set(self._tr("prompt.yes_chars", "s sim y yes").split())
        no_chars = set(self._tr("prompt.no_chars", "n nao não no").split())
        self._prompt(prompt, f" {C.GRAY}{yn}{C.RST}: ")
        try:
            raw = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if raw == "":
            return default
        if raw in yes_chars:
            return True
        if raw in no_chars:
            return False
        return default

    def pause(self, msg: str | None = None) -> None:
        msg = msg or self._tr("prompt.continue", "Pressione ENTER para continuar")
        self._w(f"\n  {C.GRAY}{msg}{C.RST}  ")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print()

    # -- menu ---------------------------------------------------------------

    def menu(self, items: list[tuple]) -> None:
        """Menu compacto; descrições longas quebram com recuo consistente."""
        self.blank()
        width = max(30, min(self.W - 4, term_columns(default=88) - 8))
        for i, entry in enumerate(items):
            key, label = str(entry[0]), str(entry[1])
            desc = str(entry[2]) if len(entry) > 2 and entry[2] else ""
            color = C.GRAY if key == "0" else (C.ORANGE if i % 2 == 0 else C.VIOLET)
            plain = f"[{key}]  {label}"
            if desc:
                plain += f"  — {desc}"
            lines = textwrap.wrap(
                plain,
                width=width,
                break_long_words=True,
                break_on_hyphens=False,
            ) or [plain]
            marker = f"[{key}]"
            first = lines[0]
            if first.startswith(marker):
                self._wln(
                    f"  {color}{C.BOLD}{marker}{C.RST}  "
                    f"{C.WHITE}{first[len(marker):].strip()}{C.RST}"
                )
            else:
                self._wln(f"  {color}{first}{C.RST}")
            for line in lines[1:]:
                self._wln(f"    {C.GRAY}{line.strip()}{C.RST}")
        self.blank()

    # -- caixas -------------------------------------------------------------

    def box(self, title: str, rows: list[tuple], color: str | None = None) -> None:
        col = color or C.PURPLE
        inner = self.W

        self.blank()
        self._wln(f"  {col}┌{'─' * inner}┐{C.RST}")

        title_fmt = f" {C.BOLD}{C.AMBER}{title}{C.RST} "
        total_pad = inner - display_width(title_fmt)
        left = max(0, total_pad // 2)
        right = max(0, total_pad - left)
        self._wln(f"  {col}│{' ' * left}{title_fmt}{' ' * right}│{C.RST}")
        self._wln(f"  {col}├{'─' * inner}┤{C.RST}")

        # largura da coluna de rótulos
        label_w = max((display_width(str(k)) for k, _ in rows), default=0)
        label_w = min(max(label_w, 12), 28)

        for label, value in rows:
            lbl_text = str(label)
            if display_width(lbl_text) > label_w:
                lbl_text = lbl_text[: max(1, label_w - 1)] + "…"
            lbl = f"{C.ORANGE}{lbl_text}{' ' * max(0, label_w - display_width(lbl_text))}{C.RST}"
            prefix = f"  {lbl}  {C.DARK}│{C.RST}  "
            continuation = f"  {' ' * label_w}  {C.DARK}│{C.RST}  "
            value_width = max(12, inner - display_width(prefix))
            value_lines = textwrap.wrap(
                str(value),
                width=value_width,
                break_long_words=True,
                break_on_hyphens=False,
            ) or [""]
            for index, value_line in enumerate(value_lines):
                content = (prefix if index == 0 else continuation) + f"{C.WHITE}{value_line}{C.RST}"
                pad = max(0, inner - display_width(content))
                self._wln(f"  {col}│{content}{' ' * pad}│{C.RST}")

        self._wln(f"  {col}└{'─' * inner}┘{C.RST}")
        self.blank()

    # -- telas especiais ----------------------------------------------------

    def splash(self, version: str, tagline: str = "", tech: str = "") -> None:
        self.clear()
        logo = [
            " ████████╗███████╗██╗     ███████╗ ██████╗ ██████╗ ██████╗ ██╗   ██╗",
            "    ██╔══╝██╔════╝██║     ██╔════╝██╔════╝██╔═══██╗██╔══██╗╚██╗ ██╔╝",
            "    ██║   █████╗  ██║     █████╗  ██║     ██║   ██║██████╔╝  ╚████╔╝ ",
            "    ██║   ██╔══╝  ██║     ██╔══╝  ██║     ██║   ██║██╔═══╝    ╚██╔╝  ",
            "    ██║   ███████╗███████╗███████╗╚██████╗╚██████╔╝██║         ██║   ",
            "    ╚═╝   ╚══════╝╚══════╝╚══════╝ ╚═════╝ ╚═════╝ ╚═╝         ╚═╝   ",
        ]
        print()
        for i, line in enumerate(logo):
            color = C.ORANGE if i < 3 else C.PURPLE
            print(f"{C.BOLD}{color}{line}{C.RST}")
        print()
        print(f"  {C.GRAY}{tagline or f'Replicador de Canais Telegram  ·  v{version}'}{C.RST}")
        if tech:
            print(f"  {C.DARK}{tech}{C.RST}")
        print()
        self.rule("─", C.DARK)
        print()

    def table(self, headers: list[str], rows: list[list[str]],
              widths: list[int] | None = None) -> None:
        """Tabela simples e alinhada."""
        widths = widths or [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
                            for i, h in enumerate(headers)]
        head = "  " + "".join(f"{C.DARK}{str(h):<{widths[i]}}{C.RST}" for i, h in enumerate(headers))
        self._wln(head)
        self._wln(f"  {C.DARK}{'─' * sum(widths)}{C.RST}")
        for row in rows:
            cells = []
            for i, cell in enumerate(row):
                text = str(cell)
                if len(text) > widths[i]:
                    text = text[: max(1, widths[i] - 1)] + "…"
                cells.append(f"{text:<{widths[i]}}")
            self._wln("  " + "".join(cells))
        self._wln(f"  {C.DARK}{'─' * sum(widths)}{C.RST}")

    def loading_bar(self, label: str = "Carregando", steps: int = 30) -> None:
        import time

        if not sys.stdout.isatty():  # ambiente não interativo: nada de animação
            return
        self._w(f"  {C.GRAY}{label}  {C.RST}[")
        for i in range(steps):
            col = C.ORANGE if i < steps // 2 else C.PURPLE
            self._w(f"{col}█{C.RST}")
            time.sleep(0.012)
        self._wln(f"]  {C.GREEN}OK{C.RST}")


# Singleton
ui = UI()


# ── Configuração de execução ──────────────────────────────────────────────────


@dataclass
class RunConfig:
    """Parâmetros que controlam o motor de cópia."""

    delay: float = 0.9
    workers: int = field(default_factory=lambda: recommended_workers())
    memory_profile: float = 0.60
    resume: bool = True
    use_dedup: bool = True
    forward_mode: bool = False   # True = encaminhar (rápido) | False = reenviar (cópia limpa)
    skip_text: bool = False
    signature: str = ""
    retry_failed: bool = True
    clean_failed_text: bool = True
    placeholder: bool = False
    delivery: str = "upload"   # upload | reference | forward
    server_copy: bool = False
    on_failure: str = "abort"  # abort | placeholder
    strict_order: bool = True
    max_retries: int = MAX_RETRIES

    def __post_init__(self) -> None:
        self.delay = max(0.1, float(self.delay))
        self.memory_profile = max(0.10, min(0.95, float(self.memory_profile)))
        self.workers = max(1, min(int(self.workers), 6))
        if self.delivery not in {"reference", "upload", "forward"}:
            self.delivery = "upload"
        # `forward_mode` é alias legado: os dois campos nunca podem divergir,
        # senão RunConfig(delivery="forward") copiaria em vez de encaminhar.
        if self.forward_mode:
            self.delivery = "forward"
        elif self.delivery == "forward":
            self.forward_mode = True
        if self.on_failure not in {"placeholder", "abort"}:
            self.on_failure = "abort"
        self.placeholder = bool(self.placeholder)
        self.server_copy = bool(self.server_copy)
        # Ordem estrita é um invariante, não uma opção que possa abrir lacunas.
        self.strict_order = True
        self.max_retries = max(0, min(int(self.max_retries), 10))

    def to_dict(self) -> dict:
        return {
            "delay": self.delay,
            "workers": self.workers,
            "memory_profile": self.memory_profile,
            "resume": self.resume,
            "use_dedup": self.use_dedup,
            "forward_mode": self.forward_mode,
            "skip_text": self.skip_text,
            "signature": self.signature,
            "retry_failed": self.retry_failed,
            "placeholder": self.placeholder,
            "delivery": self.delivery,
            "server_copy": self.server_copy,
            "on_failure": self.on_failure,
            "strict_order": self.strict_order,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunConfig":
        cfg = cls()
        for key, value in (data or {}).items():
            if hasattr(cfg, key):
                try:
                    setattr(cfg, key, value)
                except Exception:
                    pass
        cfg.__post_init__()
        return cfg
