#!/usr/bin/env python3
"""
TELECOPY — Replicador de Canais Telegram
Telegram Channel Replicator

Ponto de entrada.  Funciona tanto como script (`python main.py`) quanto
compilado (Nuitka / PyInstaller), garantindo que a pasta `data/` fique
sempre ao lado do executável.

Uso:
    python main.py              inicia normalmente
    python main.py --version    mostra a versão
    python main.py --check      testa dependências e integridade
    python main.py --lang en    inicia em inglês
    python main.py --reset      apaga o histórico de cópias (mantém login)
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
os.chdir(BASE_DIR)           # tudo é relativo à pasta do programa
sys.path.insert(0, str(BASE_DIR))

# Console com acentuação e caracteres de caixa em qualquer terminal/redirecionamento
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REQUIRED = [
    ("telethon", "telethon>=1.35.0"),
    ("cryptography", "cryptography>=41.0.0"),
    ("psutil", "psutil>=5.9.0"),
]
OPTIONAL = [("rich", "rich>=13.0.0")]


def check_dependencies(verbose: bool = False) -> bool:
    missing = []
    for module, package in REQUIRED:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        print("\n  Dependências obrigatórias ausentes / Missing dependencies:\n")
        for package in missing:
            print(f"    pip install {package}")
        print("\n  Ou instale tudo de uma vez / Or install everything at once:")
        print("    pip install -r requirements.txt\n")
        return False

    if verbose:
        for module, package in OPTIONAL:
            try:
                __import__(module)
            except ImportError:
                print(f"  [opcional] {package} — a interface fica mais simples sem ele")

    # valida suporte a scripts SQLite modernos
    import sqlite3

    try:
        sqlite3.connect(":memory:").execute("SELECT 1")
    except Exception:
        print("  SQLite indisponível — reinstale o Python.")
        return False
    return True


def _migrate_legacy_data(session_file) -> None:
    """
    Instalações antigas guardavam a sessão como data/session.session.
    O Telethon usa data/session.session por padrão ao receber o caminho sem
    extensão, então basta manter o arquivo onde está — esta rotina apenas
    garante que a pasta data/ exista e normaliza o nome quando necessário.
    """
    from pathlib import Path

    legacy = Path(str(session_file) + ".session")
    plain = Path(str(session_file))
    try:
        if plain.exists() and not legacy.exists():
            plain.rename(legacy)
    except OSError:
        pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telecopy",
        description="TELECOPY — Replicador de Canais Telegram",
        add_help=True,
    )
    parser.add_argument("--version", action="store_true", help="mostra a versão")
    parser.add_argument("--check", action="store_true", help="testa o ambiente")
    parser.add_argument("--lang", choices=("pt", "en"), help="força o idioma")
    parser.add_argument("--reset", action="store_true",
                        help="apaga o histórico de cópias (mantém o login)")
    parser.add_argument("--debug", action="store_true", help="log detalhado")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    import __init__ as app_meta

    if args.version:
        print(f"TELECOPY v{app_meta.__version__} — {app_meta.__author__}")
        return 0

    if not check_dependencies(verbose=args.check):
        return 1

    from config import DATA_DIR, LOCK_FILE, SESSION_FILE
    from database import db
    from i18n import set_lang
    from prefs import prefs
    from utils import logger, setup_telethon_logging

    setup_telethon_logging("DEBUG" if args.debug else "ERROR")
    _migrate_legacy_data(SESSION_FILE)

    if args.check:
        from system_analyzer import analyzer

        print(f"\n  TELECOPY v{app_meta.__version__} — verificação de ambiente\n")
        print(f"  Python        : {sys.version.split()[0]}")
        print(f"  Diretório     : {DATA_DIR}")
        print(f"  Banco         : {db.path} ({db.size_bytes()} bytes)")
        health = db.health()
        print(f"  Integridade   : {health['integrity']}")
        labels = {
            "ram": "Memória RAM",
            "disk": "Disco livre",
            "cpu": "Processadores",
            "class": "Perfil",
            "workers": "Workers",
        }
        for key, value in analyzer.summary().items():
            print(f"  {labels.get(key, key):<14}: {value}")

        import socket

        try:
            with socket.create_connection(("149.154.167.50", 443), timeout=5):
                print("  Telegram      : alcançável ✓")
        except OSError as exc:
            print(f"  Telegram      : inacessível — {exc}")
        print("\n  Ambiente OK.\n")
        return 0

    if args.lang:
        set_lang(args.lang)
        prefs.set_language(args.lang)

    if args.reset:
        db.reset_all()
        print("  Histórico de cópias apagado. / Copy history cleared.")
        return 0

    if args.debug:
        logger.level = "DEBUG"

    # instância única (evita dois processos escrevendo no mesmo banco)
    lock_handle = None
    try:
        lock_handle = open(LOCK_FILE, "w")
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        ui.blank()
        ui.err("O TELECOPY já está aberto em outra janela.")
        ui.err("TELECOPY is already running in another window.")
        ui.blank()
        return 2

    from core import App

    app = App()
    try:
        import asyncio

        asyncio.run(app.start())
    except KeyboardInterrupt:
        print("\n\n  Interrompido / Interrupted.\n")
    except Exception as exc:  # noqa: BLE001 - mostra erro amigável ao usuário
        logger.critical(f"Erro fatal: {exc}\n{traceback.format_exc()}")
        print(f"\n  Erro fatal / Fatal error: {exc}\n")
        traceback.print_exc()
        if os.name == "nt":
            try:
                input("\n  Pressione ENTER para sair / Press ENTER to exit…")
            except (EOFError, KeyboardInterrupt):
                pass
        return 1
    finally:
        try:
            if lock_handle is not None:
                lock_handle.close()
                LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
