#!/usr/bin/env python3
"""
TELECOPY — Replicador de Canais Telegram
Telegram Channel Replicator

Corrigido para execução Standalone (Nuitka/PyInstaller)
"""

import asyncio
import sys
import os
import traceback
from pathlib import Path

# --- LÓGICA DE DIRETÓRIO BASE PARA EXECUTÁVEL ---
def get_base_path():
    # Se estiver rodando como um executável compilado (Nuitka/PyInstaller)
    if getattr(sys, 'frozen', False):
        # sys.executable aponta para o caminho do arquivo .exe real
        return os.path.dirname(sys.executable)
    # Se estiver rodando como script .py normal
    return os.path.dirname(os.path.abspath(__file__))

# Define o caminho de onde o programa foi aberto
BASE_DIR = get_base_path()

# Muda o diretório de trabalho do processo para a pasta do .exe
# Isso garante que bibliotecas que usam caminhos relativos salvem tudo na pasta correta
os.chdir(BASE_DIR)

# Garante que o diretório base esteja no PATH para importar o 'core'
sys.path.insert(0, BASE_DIR)

def _check_deps() -> None:
    missing = []
    for pkg, install in [
        ("telethon",     "telethon>=1.35.0"),
        ("cryptography", "cryptography>=41.0.0"),
        ("psutil",       "psutil>=5.9.0"),
    ]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(install)

    if missing:
        print("\n  Dependências faltando / Missing dependencies:\n")
        for m in missing:
            print(f"    pip install {m}")
        print("\n  Ou rode / Or run:  pip install -r requirements.txt\n")
        sys.exit(1)

    try:
        import rich  # noqa: F401
    except ImportError:
        pass # Silencioso em produção para não sujar a UI

def main() -> None:
    _check_deps()

    # Importa o core após garantir o diretório de trabalho
    from core import App

    app = App()

    try:
        asyncio.run(app.start())
    except KeyboardInterrupt:
        print("\n\n  Interrompido / Interrupted.\n")
    except Exception as exc:
        print(f"\n  Erro fatal / Fatal error: {exc}\n")
        traceback.print_exc()
        # Importante: mantém o console aberto em caso de erro mesmo sem a flag de console
        input("\n  Pressione ENTER para sair / Press ENTER to exit…")

if __name__ == "__main__":
    main()