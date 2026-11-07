#!/usr/bin/env python3
"""
demo.py — Demonstração offline do TELECOPY.

Executa uma cópia completa usando um cliente Telegram **simulado**:
não acessa a internet, não pede login e não envia nada para lugar nenhum.
Serve para conferir que a instalação está funcionando e ver a interface
em ação antes de usar sua conta.

    python demo.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# Ambiente isolado: a demonstração nunca toca nos seus dados reais.
_TMP = Path(tempfile.mkdtemp(prefix="telecopy_demo_"))
import config  # noqa: E402

config.DATA_DIR = _TMP
config.TEMP_DIR = _TMP / "tmp"
config.LOGS_DIR = _TMP / "logs"
config.VAULT_DIR = _TMP / "vault"
config.EXPORT_DIR = _TMP / "exports"
config.BACKUP_DIR = _TMP / "backups"
config.STATS_FILE = _TMP / "stats.json"
config.DAILY_FILE = _TMP / "daily.json"
config.LOG_FILE = config.LOGS_DIR / "demo.log"
config.init_dirs()

from config import C, RunConfig, ui  # noqa: E402
from copier import Copier  # noqa: E402
from database import Repository  # noqa: E402
from tests.fake_telegram import (  # noqa: E402
    FakeClient,
    album_messages,
    channel,
    document_message,
    photo_message,
    text_message,
    video_message,
)
from utils import Fmt  # noqa: E402


def build_messages():
    return [
        text_message(1, "Bem-vindo ao canal! 🎉"),
        photo_message(2, "Aula 01 — introdução"),
        document_message(3, "apostila-aula-01.pdf", size=250_000),
        video_message(4, "aula-01.mp4", size=900_000),
        *album_messages(10, 3),
        text_message(20, "Aula 02 em breve"),
        photo_message(21, "Capa da Aula 02"),
    ]


async def main() -> int:
    client = FakeClient(build_messages())
    source = channel(-1001234567, "Cursos de Programação")
    dest = channel(-1009876543, "Meu Backup")
    client.register_entity(source)
    client.register_entity(dest)
    repo = Repository(_TMP / "demo.db")

    ui.splash("4.0.0", "DEMONSTRAÇÃO OFFLINE — nada é enviado ao Telegram",
              "Cliente Telegram simulado · dados temporários")

    config_obj = RunConfig(workers=3, delay=0.1)
    result = await Copier(client, repo=repo, config=config_obj).run(source, dest)

    ui.box("RESULTADO DA CÓPIA", [
        ("Enviadas", Fmt.number(result["done"])),
        ("Ignoradas", Fmt.number(result["skipped"])),
        ("Duplicatas evitadas", Fmt.number(result["duplicates"])),
        ("Erros", Fmt.number(result["errors"])),
        ("Álbuns", Fmt.number(result["albums"])),
        ("Transferido", Fmt.size(result["bytes"])),
        ("Tempo total", result["elapsed"]),
        ("Velocidade", result["speed"]),
    ], color=C.GREEN)

    print(f"  Publicações no destino : {len(client.sent)}")
    print(f"  Checkpoints gravados   : {repo.pair_summary(source.id, dest.id)['done']}")
    print(f"  Temporários restantes  : {len(list(config.TEMP_DIR.glob('*')))}")

    print(f"\n  {C.CYAN}▶{C.RST} Rodando a mesma cópia outra vez "
          f"(deve pular tudo, sem duplicar)...\n")
    second = await Copier(client, repo=repo, config=config_obj).run(source, dest)
    print(f"  Enviadas na 2ª execução: {second['done']} · "
          f"publicações totais no destino: {len(client.sent)}")
    print(f"\n  {C.GREEN}✓{C.RST}  Demonstração concluída. "
          f"Para usar de verdade: {C.BOLD}python main.py{C.RST}\n")
    repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
