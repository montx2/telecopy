"""
config.py — Configuration, constants, and terminal UI primitives.
Com detecção automática de recursos do sistema (RAM, CPU, Disco).
"""

import os
import sys
import psutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ── Directories ───────────────────────────────────────────────────────────────

DATA_DIR    = Path("data")
TEMP_DIR    = DATA_DIR / "tmp"
LOGS_DIR    = DATA_DIR / "logs"
VAULT_DIR   = DATA_DIR / "vault"
EXPORT_DIR  = DATA_DIR / "exports"

CONFIG_FILE  = DATA_DIR / "config.json"
SESSION_FILE = DATA_DIR / "session"
DB_FILE      = DATA_DIR / "telecopy.db"
STATS_FILE   = DATA_DIR / "stats.json"
QUEUE_FILE   = DATA_DIR / "queue.json"
VAULT_FILE   = VAULT_DIR / "credentials.enc"

# ── Limits ────────────────────────────────────────────────────────────────────

DAILY_LIMIT      = 1000
DAILY_WARN       = 800
MAX_RETRIES      = 3

# ── System Detection & Auto-Configuration ─────────────────────────────────────

def _detect_system_resources():
    """Detecta recursos do sistema e calcula configurações ótimas."""
    try:
        # RAM
        ram = psutil.virtual_memory()
        ram_total = ram.total / (1024**3)  # GB
        ram_available = ram.available / (1024**3)  # GB
        
        # CPU
        cpus = psutil.cpu_count(logical=True) or 2
        cpu_physical = psutil.cpu_count(logical=False) or 1
        
        # Disco
        try:
            disk = psutil.disk_usage(DATA_DIR if DATA_DIR.exists() else os.getcwd())
            disk_free = disk.free / (1024**3)  # GB
        except:
            disk_free = 10  # Fallback seguro
        
        # Cálculo inteligente de workers
        # Fórmula: 1 worker por 2GB de RAM disponível, limitado pela CPU
        workers_by_ram = int(ram_available / 2)
        workers_by_cpu = min(cpus * 2, 8)  # Máx 2x cores lógicos, limitado em 8
        
        MAX_WORKERS_DL = min(workers_by_ram, workers_by_cpu, 16)
        MAX_WORKERS_DL = max(1, MAX_WORKERS_DL)  # Mínimo 1
        
        return {
            'ram_total': ram_total,
            'ram_available': ram_available,
            'cpu_count': cpus,
            'cpu_physical': cpu_physical,
            'disk_free': disk_free,
            'max_workers_dl': MAX_WORKERS_DL,
            'max_workers_up': max(1, MAX_WORKERS_DL // 2),
            'chunk_size': min(512 * 1024, int(ram.available * 0.1)),  # 512KB ou 10% RAM
            'buffer_ram': int(ram.available * 0.6),  # 60% da RAM disponível por padrão
        }
    except Exception:
        # Fallback seguro se psutil falhar
        return {
            'ram_total': 4,
            'ram_available': 2,
            'cpu_count': 2,
            'cpu_physical': 1,
            'disk_free': 10,
            'max_workers_dl': 4,
            'max_workers_up': 2,
            'chunk_size': 512 * 1024,
            'buffer_ram': 1 * 1024 * 1024 * 1024,  # 1GB
        }

# Detecta recursos ao importar o módulo
_SYSTEM = _detect_system_resources()

# ── Performance (Auto-detectados ou Fallback) ─────────────────────────────────

CHUNK_SIZE           = _SYSTEM['chunk_size']
MAX_WORKERS_DL       = _SYSTEM['max_workers_dl']
MAX_WORKERS_UP       = _SYSTEM['max_workers_up']
BUFFER_RAM_MAX       = _SYSTEM['buffer_ram']     # 60% da RAM disponível
PROGRESS_INTERVAL    = 0.4

# ── Deduplication ─────────────────────────────────────────────────────────────

BLOOM_CAPACITY  = 1_000_000
BLOOM_FP_RATE   = 0.001
LRU_SIZE        = 50_000


def init_dirs() -> None:
    """Create required directories with restricted permissions."""
    for d in [DATA_DIR, TEMP_DIR, LOGS_DIR, VAULT_DIR, EXPORT_DIR]:
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except Exception:
            pass


init_dirs()


# ── ANSI colour palette ───────────────────────────────────────────────────────

class C:
    """Minimal, readable ANSI colour constants."""

    RST   = "\033[0m"
    BOLD  = "\033[1m"
    DIM   = "\033[2m"

    # Primary
    ORANGE  = "\033[38;5;208m"
    AMBER   = "\033[38;5;214m"
    GOLD    = "\033[38;5;220m"

    PURPLE  = "\033[38;5;99m"
    VIOLET  = "\033[38;5;141m"
    INDIGO  = "\033[38;5;61m"

    # Status
    GREEN   = "\033[38;5;82m"
    YELLOW  = "\033[38;5;226m"
    RED     = "\033[38;5;196m"
    CYAN    = "\033[38;5;87m"
    BLUE    = "\033[38;5;75m"

    # Neutrals
    WHITE   = "\033[38;5;255m"
    SILVER  = "\033[38;5;250m"
    GRAY    = "\033[38;5;244m"
    DARK    = "\033[38;5;238m"


# ── Terminal UI primitives ────────────────────────────────────────────────────

class UI:
    """
    Clean terminal interface.

    Line width is fixed at 76 characters to stay comfortable on
    80-column terminals and most modern emulators.
    """

    W = 76

    def __init__(self) -> None:
        self._rich_ok = False
        self._console  = None

        try:
            from rich.console import Console
            self._console  = Console()
            self._rich_ok  = True
        except ImportError:
            pass

    # ── Low-level helpers ────────────────────────────────────────────────────

    @staticmethod
    def clear() -> None:
        os.system("cls" if os.name == "nt" else "clear")

    def _w(self, text: str) -> None:
        sys.stdout.write(text)

    def _wln(self, text: str) -> None:
        sys.stdout.write(text + "\n")

    def _clean_len(self, text: str) -> int:
        """Length of text after stripping ANSI escape codes."""
        import re
        return len(re.sub(r"\033\[[0-9;]*m", "", text))

    def pad(self, text: str, width: int = None, fill: str = " ") -> str:
        w = width or self.W
        n = self._clean_len(text)
        return text + fill * max(0, w - n)

    def center(self, text: str, width: int = None) -> str:
        w = width or self.W
        n = self._clean_len(text)
        left = (w - n) // 2
        right = w - n - left
        return " " * left + text + " " * right

    # ── Structural elements ───────────────────────────────────────────────────

    def rule(self, char: str = "─", color: str = None) -> None:
        col = color or C.INDIGO
        self._wln(f"  {col}{char * self.W}{C.RST}")

    def blank(self) -> None:
        print()

    def divider(self) -> None:
        self._wln(f"  {C.DARK}{'─' * self.W}{C.RST}")

    # ── Header / section title ────────────────────────────────────────────────

    def header(self, title: str, subtitle: str = "") -> None:
        self.blank()
        self.rule("═", C.PURPLE)
        title_fmt = f"{C.BOLD}{C.ORANGE}{title}{C.RST}"
        self._wln(f"  {C.PURPLE}║{C.RST}  {self.pad(title_fmt, self.W - 4)}{C.PURPLE}║{C.RST}")
        if subtitle:
            sub_fmt = f"{C.GRAY}{subtitle}{C.RST}"
            self._wln(f"  {C.PURPLE}║{C.RST}  {self.pad(sub_fmt, self.W - 4)}{C.PURPLE}║{C.RST}")
        self.rule("═", C.PURPLE)
        self.blank()

    def section(self, title: str) -> None:
        self.blank()
        self._wln(f"  {C.AMBER}{C.BOLD}{title}{C.RST}")
        self._wln(f"  {C.DARK}{'─' * len(title)}{C.RST}")

    # ── Status messages ───────────────────────────────────────────────────────

    def ok(self, msg: str) -> None:
        self._wln(f"  {C.GREEN}✓{C.RST}  {C.WHITE}{msg}{C.RST}")

    def err(self, msg: str) -> None:
        self._wln(f"  {C.RED}✗{C.RST}  {C.WHITE}{msg}{C.RST}")

    def warn(self, msg: str) -> None:
        self._wln(f"  {C.YELLOW}!{C.RST}  {C.WHITE}{msg}{C.RST}")

    def info(self, msg: str) -> None:
        self._wln(f"  {C.CYAN}·{C.RST}  {C.WHITE}{msg}{C.RST}")

    def step(self, msg: str) -> None:
        self._wln(f"  {C.PURPLE}›{C.RST}  {msg}")

    # ── Input helpers ─────────────────────────────────────────────────────────

    def ask(self, prompt: str, required: bool = True) -> str:
        try:
            from i18n import t as _t
            req_msg   = _t("prompt.required")
        except ImportError:
            req_msg   = "This field is required."

        while True:
            try:
                self._w(f"  {C.AMBER}›{C.RST} {C.WHITE}{prompt}{C.RST}: ")
                value = input().strip()
            except (EOFError, KeyboardInterrupt):
                return ""
            if value or not required:
                return value
            self.warn(req_msg)

    def confirm(self, prompt: str) -> bool:
        try:
            from i18n import t as _t
            yn        = _t("prompt.yes_no")
            yes_chars = set(_t("prompt.yes_chars").split())
        except ImportError:
            yn        = "[y/N]"
            yes_chars = {"y", "yes"}

        self._w(f"  {C.AMBER}›{C.RST} {C.WHITE}{prompt}{C.RST} {C.GRAY}{yn}{C.RST}: ")
        try:
            ans = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in yes_chars

    def pause(self, msg: str = None) -> None:
        if msg is None:
            try:
                from i18n import t as _t
                msg = _t("prompt.continue")
            except ImportError:
                msg = "Press ENTER to continue"
        self._w(f"\n  {C.GRAY}{msg}{C.RST}  ")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

    # ── Menu rendering ────────────────────────────────────────────────────────

    def menu(self, items: list[tuple]) -> None:
        """
        Render a numbered menu.

        items: list of (key, label, description?)
        """
        self.blank()
        for i, entry in enumerate(items):
            key, label = entry[0], entry[1]
            desc = entry[2] if len(entry) > 2 else ""

            if key == "0":
                color = C.GRAY
            elif i % 2 == 0:
                color = C.ORANGE
            else:
                color = C.VIOLET

            self._wln(
                f"  {color}[{C.BOLD}{key}{C.RST}{color}]{C.RST}"
                f"  {C.BOLD}{C.WHITE}{label}{C.RST}"
                + (f"  {C.GRAY}— {desc}{C.RST}" if desc else "")
            )
        self.blank()

    # ── Key-value box ─────────────────────────────────────────────────────────

    def box(self, title: str, rows: list[tuple], color: str = None) -> None:
        """Render a bordered key-value box."""
        col = color or C.PURPLE
        inner = self.W - 4

        self.blank()
        self._wln(f"  {col}┌{'─' * (inner + 2)}┐{C.RST}")

        title_fmt = f" {C.BOLD}{C.AMBER}{title}{C.RST} "
        pad_total = inner + 2 - self._clean_len(title_fmt)
        left_pad = pad_total // 2
        right_pad = pad_total - left_pad
        self._wln(f"  {col}│{' ' * left_pad}{title_fmt}{' ' * right_pad}{col}│{C.RST}")
        self._wln(f"  {col}├{'─' * (inner + 2)}┤{C.RST}")

        for label, value in rows:
            lbl = f"{C.ORANGE}{label}{C.RST}"
            val = f"{C.WHITE}{value}{C.RST}"
            content = f"  {lbl}  {C.DARK}│{C.RST}  {val}"
            raw_len = self._clean_len(content)
            padding = inner + 2 - raw_len
            self._wln(f"  {col}│{content}{' ' * max(0, padding)}{col}│{C.RST}")

        self._wln(f"  {col}└{'─' * (inner + 2)}┘{C.RST}")
        self.blank()

    # ── Splash screen ─────────────────────────────────────────────────────────

    def splash(self, version: str) -> None:
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
        tagline = f"Telegram Channel Replicator  ·  v{version}"
        print(f"  {C.GRAY}{self.center(tagline)}{C.RST}")
        tech = "SQLite WAL  ·  AES-256-GCM  ·  Bloom Filter  ·  Async"
        print(f"  {C.DARK}{self.center(tech)}{C.RST}")
        print()
        self.rule("─", C.DARK)
        print()

    # ── Rich progress bar (optional) ──────────────────────────────────────────

    def make_progress(self, total: int, description: str = "Processing"):
        """
        Return a Rich Progress context manager if Rich is available,
        otherwise return None and the caller falls back to plain output.
        """
        if not self._rich_ok:
            return None, None

        from rich.progress import (
            Progress, SpinnerColumn, BarColumn,
            TextColumn, TimeRemainingColumn, MofNCompleteColumn,
        )

        prog = Progress(
            SpinnerColumn(style="bold purple"),
            TextColumn("[bold orange3]{task.description}"),
            BarColumn(
                complete_style="orange3",
                finished_style="green",
                pulse_style="purple",
            ),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=self._console,
            refresh_per_second=8,
        )
        task = prog.add_task(description, total=total)
        return prog, task

    def loading_bar(self, label: str = "Loading", steps: int = 40) -> None:
        """Animated loading bar — used only at startup."""
        import time

        if self._rich_ok:
            from rich.progress import Progress, BarColumn, TextColumn
            with Progress(
                TextColumn(f"  [dim]{label}[/dim]"),
                BarColumn(complete_style="orange3", finished_style="green"),
                "[progress.percentage]{task.percentage:>3.0f}%",
                console=self._console,
                transient=True,
            ) as prog:
                task = prog.add_task("", total=steps)
                for _ in range(steps):
                    prog.advance(task)
                    time.sleep(0.03)
        else:
            self._w(f"  {C.GRAY}{label}  [{C.RST}")
            for i in range(steps):
                col = C.ORANGE if i < steps // 2 else C.PURPLE
                self._w(f"{col}█{C.RST}")
                sys.stdout.flush()
                time.sleep(0.025)
            self._wln(f"{C.GRAY}]{C.RST}  {C.GREEN}OK{C.RST}")


# Singleton
ui = UI()


# ── Runtime configuration ─────────────────────────────────────────────────────

@dataclass
class RunConfig:
    """Parameters passed to the copy engine at runtime."""
    delay:              float = 0.9
    workers_dl:         int   = field(default_factory=lambda: MAX_WORKERS_DL)
    workers_up:         int   = field(default_factory=lambda: MAX_WORKERS_UP)
    buffer_ram:         int   = field(default_factory=lambda: BUFFER_RAM_MAX)
    chunk_size:         int   = CHUNK_SIZE
    progress_interval:  float = PROGRESS_INTERVAL
    use_dedup:          bool  = True
    verify_integrity:   bool  = True
    memory_profile:     float = 0.60  # Novo: percentual da RAM para buffer (0.0-1.0)

    def __post_init__(self) -> None:
        if self.delay < 0.1:
            self.delay = 0.1
        if self.memory_profile < 0.1:
            self.memory_profile = 0.1
        if self.memory_profile > 0.95:
            self.memory_profile = 0.95