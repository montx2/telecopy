#!/usr/bin/env python3
"""
bench_transfer.py — Mede o caminho padrão vs. rápido com um arquivo real.

Uso:
    python bench_transfer.py --source LINK --msg-id N --connections 1,2,4,8

Baixa a mensagem indicada, reenvia o arquivo para as Mensagens Salvas e imprime
MB/s de download, upload e agregado. Não afirma ganho sem medição; a recomendação
de ``connections_per_file`` sai dos números desta execução. Requer a sessão/
credenciais já configuradas pelo TELECOPY (ou --api-id/--api-hash).

Cuidado: o teste envia mensagens reais para as Mensagens Salvas e pode consumir
cota/FloodWait. Comece com um arquivo de teste pequeno e poucas conexões.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from telethon import TelegramClient

from config import SESSION_FILE, TEMP_DIR, VAULT_FILE
from fasttransfer import FastTransferPool, fast_download, fast_upload
from security import Vault
from utils import logger


def _parse_connections(raw: str) -> List[int]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        value = int(item)
        if not 1 <= value <= 8:
            raise argparse.ArgumentTypeError("connections deve estar entre 1 e 8")
        if value not in values:
            values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("informe ao menos uma quantidade de conexões")
    return values


def _mbps(bytes_total: int, seconds: float) -> float:
    return bytes_total / 1048576.0 / max(seconds, 1e-6)


async def _measure_standard(client: TelegramClient, msg: Any, path: Path,
                            file_name: str) -> Dict[str, Any]:
    start = time.perf_counter()
    await client.download_media(msg, file=str(path))
    downloaded = time.perf_counter()
    size = os.path.getsize(path)
    handle = await client.upload_file(str(path), file_name=file_name)
    uploaded = time.perf_counter()
    sent = await client.send_file("me", handle, force_document=True,
                                  caption=f"TELECOPY bench padrão: {file_name}")
    finished = time.perf_counter()
    return {
        "size": size,
        "download_s": downloaded - start,
        "upload_s": uploaded - downloaded,
        "send_s": finished - uploaded,
        "total_s": finished - start,
        "msg_id": getattr(sent, "id", None),
    }


async def _measure_fast(client: TelegramClient, msg: Any, path: Path,
                        file_name: str, connections: int,
                        max_total: int) -> Dict[str, Any]:
    pool = FastTransferPool(client, max_total_connections=max(max_total, connections),
                            connections_per_file=connections)
    try:
        start = time.perf_counter()
        await fast_download(pool, msg, path)
        downloaded = time.perf_counter()
        size = os.path.getsize(path)
        handle = await fast_upload(pool, path, file_name)
        uploaded = time.perf_counter()
        sent = await client.send_file("me", handle, force_document=True,
                                      caption=f"TELECOPY bench rápido ({connections} conexões): {file_name}")
        finished = time.perf_counter()
        return {
            "size": size,
            "download_s": downloaded - start,
            "upload_s": uploaded - downloaded,
            "send_s": finished - uploaded,
            "total_s": finished - start,
            "msg_id": getattr(sent, "id", None),
            "halvings": pool.halvings,
            "capacity": pool.capacity,
        }
    finally:
        await pool.close()


def _print_row(label: str, row: Dict[str, Any]) -> None:
    size = row["size"]
    down = _mbps(size, row["download_s"])
    up = _mbps(size, row["upload_s"])
    agg = _mbps(2 * size, row["download_s"] + row["upload_s"])
    extra = ""
    if "halvings" in row:
        extra = f"  auto-proteções={row['halvings']} capacidade_final={row['capacity']}"
    print(f"  {label:<18} ↓ {down:7.2f} MB/s  ↑ {up:7.2f} MB/s  "
          f"agregado {agg:7.2f} MB/s  total {row['total_s']:7.2f}s{extra}")


async def run(args: argparse.Namespace) -> int:
    creds = Vault(VAULT_FILE).load() or {}
    api_id = args.api_id or creds.get("api_id") or os.environ.get("TELEGRAM_API_ID")
    api_hash = args.api_hash or creds.get("api_hash") or os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        print("Credenciais ausentes. Execute main.py uma vez ou informe --api-id/--api-hash.")
        return 1

    client = TelegramClient(str(SESSION_FILE), int(api_id), str(api_hash),
                            timeout=12, connection_retries=1, request_retries=1)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("Sessão não autorizada. Faça login pelo main.py antes do benchmark.")
            return 1
        source = await client.get_entity(args.source)
        msg = await client.get_messages(source, ids=args.msg_id)
        if msg is None or getattr(msg, "media", None) is None:
            print("Mensagem não encontrada ou sem mídia transferível.")
            return 1
        file_name = getattr(getattr(msg, "file", None), "name", None) or f"bench_{args.msg_id}.bin"
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        print(f"\nArquivo: {file_name}  mensagem={args.msg_id}  origem={args.source}")
        print("Destino dos envios: Mensagens Salvas (me)\n")

        rows: List[tuple[str, Dict[str, Any]]] = []
        std_path = TEMP_DIR / f"bench_std_{args.msg_id}_{os.getpid()}.bin"
        try:
            row = await _measure_standard(client, msg, std_path, file_name)
            _print_row("padrão", row)
            rows.append(("padrão", row))
        except Exception as exc:
            logger.warning(f"Bench padrão falhou: {exc}")
            print(f"  padrão: FALHOU ({exc})")
        finally:
            std_path.unlink(missing_ok=True)

        for connections in args.connections:
            path = TEMP_DIR / f"bench_fast_{connections}_{args.msg_id}_{os.getpid()}.bin"
            try:
                row = await _measure_fast(client, msg, path, file_name, connections, args.max_total)
                _print_row(f"rápido/{connections}", row)
                rows.append((f"rápido/{connections}", row))
            except Exception as exc:
                logger.warning(f"Bench rápido/{connections} falhou: {exc}")
                print(f"  rápido/{connections}: FALHOU ({exc})")
            finally:
                path.unlink(missing_ok=True)

        fast_rows = [(label, row) for label, row in rows if label.startswith("rápido/")]
        if fast_rows:
            def agg(row: Dict[str, Any]) -> float:
                return _mbps(2 * row["size"], row["download_s"] + row["upload_s"])

            best = max(agg(row) for _, row in fast_rows)
            # Menor número de conexões dentro de 5% do melhor throughput medido.
            near = [(label, row) for label, row in fast_rows if agg(row) >= 0.95 * best]
            label, row = min(near, key=lambda item: int(item[0].split("/")[1]))
            connections = int(label.split("/")[1])
            print(f"\nRecomendação medida: connections_per_file={connections} "
                  f"(agregado {agg(row):.2f} MB/s).")
            if any(row.get("halvings", 0) for _, row in fast_rows):
                print("Houve auto-proteção por FloodWait/timeouts; prefira menos conexões e delay maior.")
            if rows and rows[0][0] == "padrão":
                base = agg(rows[0][1])
                if base > 0:
                    print(f"Razão medida rápido/padrão no melhor ponto: {best / base:.2f}x.")
        print("\nUse estes números apenas para este link/arquivo; valide ~10 mensagens "
              "num canal de teste antes de rodar o canal inteiro.")
        return 0
    finally:
        await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark padrão vs. rápido do TELECOPY")
    parser.add_argument("--source", required=True, help="link/username/ID da origem")
    parser.add_argument("--msg-id", required=True, type=int, help="ID da mensagem com mídia")
    parser.add_argument("--connections", type=_parse_connections, default="1,2,4,8",
                        help="lista separada por vírgula (1–8), padrão 1,2,4,8")
    parser.add_argument("--max-total", type=int, default=12, help="orçamento global de conexões")
    parser.add_argument("--api-id", type=int, default=None)
    parser.add_argument("--api-hash", default=None)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
