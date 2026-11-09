"""CLI não interativa do TELECOPY.

Exemplos:
    telecopy clone --src @origem --dst @destino --perfil recomendado --retomar --json
    telecopy verify --src @origem --dst @destino --reparar-confirmados
    telecopy clone --src @origem --dst @destino --rehearsal 10
    telecopy doctor --auth

Códigos de saída: 0 sucesso; 1 erro/pendência; 2 interrupção/uso inválido.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any, Optional

from config import EXPORT_DIR, SESSION_FILE, RunConfig, recommended_settings, turbo_settings
from copier import Copier
from database import Repository
from i18n import set_lang, t
from relatorio import escrever_relatorio, montar_relatorio
from verificacao import verificar_par


def _perfil(nome: str) -> dict:
    if nome in {"max", "turbo"}:
        return turbo_settings()
    return recommended_settings()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telecopy", description="TELECOPY CLI")
    parser.add_argument("--lang", choices=("pt", "en"), default="pt")
    parser.add_argument("--proxy", default=None, help="socks5://host:porta, socks4:// ou http://")
    sub = parser.add_subparsers(dest="comando", required=True)

    clone = sub.add_parser("clone", help="cópia não interativa")
    clone.add_argument("--src", required=True)
    clone.add_argument("--dst", required=True)
    clone.add_argument("--perfil", choices=("recomendado", "recommended", "max", "turbo"), default="recomendado")
    clone.add_argument("--retomar", action="store_true")
    clone.add_argument("--json", action="store_true")
    clone.add_argument("--rehearsal", type=int, default=0, metavar="N")
    clone.add_argument("--forward", action="store_true")
    clone.add_argument("--report-dir", type=Path, default=EXPORT_DIR)
    clone.add_argument("--filtro", default="all")
    clone.add_argument("--inicio", default=None, metavar="HH:MM")
    clone.add_argument("--fim", default=None, metavar="HH:MM")
    clone.add_argument("--bandwidth", type=int, default=0, metavar="BYTES_POR_SEGUNDO")
    clone.add_argument("--reescrever-de", default="")
    clone.add_argument("--reescrever-para", default="")
    clone.add_argument("--notificar-url", default=None)

    verify = sub.add_parser("verify", help="verificação origem→destino")
    verify.add_argument("--src", required=True)
    verify.add_argument("--dst", required=True)
    verify.add_argument("--reparar-confirmados", action="store_true")
    verify.add_argument("--json", action="store_true")
    verify.add_argument("--limite", type=int, default=None, metavar="N")
    verify.add_argument("--filtro", default="all")

    archive = sub.add_parser("archive", help="arquivo offline somente leitura")
    archive.add_argument("--src", required=True)
    archive.add_argument("--out", required=True, type=Path)
    archive.add_argument("--limite", type=int, default=None)
    archive.add_argument("--topico", type=int, default=None)
    archive.add_argument("--json", action="store_true")

    ui = sub.add_parser("ui", help="UI local somente leitura protegida por token")
    ui.add_argument("--report-dir", type=Path, default=EXPORT_DIR)
    ui.add_argument("--token", default=None)
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)

    doctor = sub.add_parser("doctor", help="diagnóstico")
    doctor.add_argument("--auth", action="store_true", help="diagnóstico autenticado somente leitura")
    doctor.add_argument("--json", action="store_true")
    return parser


def _sinais_parada() -> tuple:
    sigs = [signal.SIGINT, signal.SIGTERM]
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        sigs.append(sigbreak)
    return tuple(sigs)


async def _com_sinais(coro, copier: Optional[Copier] = None) -> Any:
    """Cancela a tarefa e restaura handlers antigos, inclusive no fallback Windows."""
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(coro)
    interrupted = False

    def pedir_parada(*_args):
        nonlocal interrupted
        interrupted = True
        if copier is not None:
            copier.request_stop()
        if not task.done():
            task.cancel()

    def fallback_handler(signum, frame):
        loop.call_soon_threadsafe(pedir_parada)

    loop_handlers = []
    old_handlers = {}
    for sig in _sinais_parada():
        try:
            loop.add_signal_handler(sig, pedir_parada)
            loop_handlers.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                old_handlers[sig] = signal.signal(sig, fallback_handler)
            except (ValueError, OSError):
                pass
    try:
        return await task
    except asyncio.CancelledError:
        if interrupted:
            return {"interrupted": True, "errors": 0, "complete": False,
                    "first_error": t("cli.interrupted")}
        raise
    finally:
        for sig in loop_handlers:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        for sig, old in old_handlers.items():
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):
                pass


async def executar(args: argparse.Namespace, client: Any, repo: Repository) -> int:
    set_lang(args.lang)
    if args.comando == "doctor":
        from doctor import run_doctor, doctor_autenticado
        if args.auth:
            resultado = await doctor_autenticado(client)
            if args.json:
                print(json.dumps(resultado, ensure_ascii=False))
            return 0 if resultado.get("ok") else 1
        return run_doctor(json_mode=args.json)

    if args.comando == "archive":
        from opcional import arquivar
        source = await client.get_entity(args.src)
        resultado = await arquivar(client, source, args.out, limite=args.limite, topico=args.topico)
        if args.json:
            print(json.dumps(resultado, ensure_ascii=False))
        else:
            print(t("archive.done"), json.dumps(resultado, ensure_ascii=False))
        return 0

    source = await client.get_entity(args.src)
    dest = await client.get_entity(args.dst)
    if args.comando == "verify":
        resultado = await verificar_par(client, repo, source, dest,
                                        reparar=args.reparar_confirmados,
                                        limite=args.limite, filter_code=args.filtro)
        data = resultado.como_dict()
        if args.json:
            print(json.dumps(data, ensure_ascii=False))
        else:
            print(t("verify.title"), json.dumps(data, ensure_ascii=False))
        return 0 if data["complete"] else 1

    values = _perfil(args.perfil)
    cfg = RunConfig(**values)
    cfg.resume = bool(args.retomar)
    cfg.interactive = False
    cfg.bandwidth_limit_bps = int(args.bandwidth)
    cfg.schedule_start = args.inicio
    cfg.schedule_end = args.fim
    cfg.rewrite_from = args.reescrever_de
    cfg.rewrite_to = args.reescrever_para
    if args.forward and (cfg.rewrite_from or cfg.rewrite_to):
        print(t("cli.rewrite_forward"), file=sys.stderr)
        return 2
    if args.forward:
        cfg.delivery = "forward"
    copier = Copier(client, repo=repo, config=cfg, rich_ok=not args.json)
    if args.rehearsal > 0:
        if not args.json:
            print(t("cli.rehearsal", n=args.rehearsal))
        copier.max_messages = int(args.rehearsal)
    resultado = await _com_sinais(copier.run(source, dest, filter_code=args.filtro), copier)
    if resultado.get("interrupted") and not resultado.get("errors"):
        code = 2
    else:
        code = 0 if resultado.get("complete") and not resultado.get("errors") else 1
    verificacao = None
    if not resultado.get("interrupted"):
        verificacao = (await verificar_par(
            client, repo, source, dest,
            max_messages=int(args.rehearsal) if args.rehearsal > 0 else None,
            filter_code=args.filtro,
        )).como_dict()
        if not verificacao["complete"]:
            code = 1
    relatorio = montar_relatorio(resultado, perfil=cfg.to_dict(), verificacao=verificacao)
    paths = escrever_relatorio(relatorio, args.report_dir)
    if args.json:
        print(json.dumps({"exit_code": code, "resultado": resultado,
                          "verificacao": verificacao,
                          "relatorio": {k: str(v) for k, v in paths.items()}},
                         ensure_ascii=False))
    else:
        if args.rehearsal > 0:
            print(t("cli.rehearsal_ok") if code == 0 else t("cli.rehearsal_fail"))
        print(t("cli.exit_ok") if code == 0 else t("cli.exit_error"))
        print(f"JSON: {paths['json']}")
        print(f"HTML: {paths['html']}")
    if args.notificar_url:
        from opcional import notificar_webhook
        await asyncio.to_thread(notificar_webhook, args.notificar_url, {
            "exit_code": code, "resultado": resultado,
            "relatorio": {k: str(v) for k, v in paths.items()},
        })
    return code


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    set_lang(args.lang)
    if args.comando == "doctor" and not args.auth:
        from doctor import run_doctor
        return run_doctor(json_mode=args.json)
    if args.comando == "ui":
        from opcional import servir_ui
        return servir_ui(args.report_dir, args.token, args.host, args.port)

    from telethon import TelegramClient
    from security import Vault
    from config import VAULT_FILE
    creds = Vault(VAULT_FILE).load() or {}
    api_id, api_hash = creds.get("api_id"), creds.get("api_hash")
    if not api_id or not api_hash:
        print(t("doctor.auth_fail", reason="credenciais API ausentes"), file=sys.stderr)
        return 2
    proxy = None
    if args.proxy:
        from opcional import parse_proxy
        try:
            proxy = parse_proxy(args.proxy)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    client = TelegramClient(str(SESSION_FILE), int(api_id), str(api_hash), proxy=proxy)
    repo = Repository()

    async def runner() -> int:
        await client.connect()
        try:
            if not await client.is_user_authorized():
                print(t("doctor.auth_fail", reason="sessão não autorizada"), file=sys.stderr)
                return 2
            return await executar(args, client, repo)
        finally:
            await client.disconnect()
            repo.close()

    try:
        return asyncio.run(runner())
    except KeyboardInterrupt:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
