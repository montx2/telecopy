"""
doctor.py — Diagnóstico detalhado do TELECOPY (``python main.py --doctor``).

Use quando a cópia "não faz nada": o relatório mostra versões, se o Telethon
instalado tem as APIs internas que o caminho rápido usa, se existe login salvo,
os perfis de hardware calculados e as últimas linhas de erro do log.

O diagnóstico padrão não conecta ao Telegram e não altera nada.
`doctor_autenticado()` faz apenas leitura quando explicitamente solicitado.
"""

from __future__ import annotations

import importlib
import socket
import sys
from pathlib import Path
from typing import List, Tuple

OK, WARN, FAIL = "✓", "!", "✗"
TELETHON_PIN = "1.45.0"
FAST_INTERNALS = ("_create_exported_sender", "_borrow_exported_sender",
                  "_return_exported_sender")


def _line(mark: str, label: str, detail: str = "") -> None:
    print(f"  {mark}  {label:<24}{detail}")


def _tail_problems(log_file: Path, limit: int = 8) -> List[str]:
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    marks = ("ERROR", "CRITICAL", "Traceback", "Error:", "Exception")
    found = [ln.strip() for ln in lines if any(m in ln for m in marks)]
    return found[-limit:]


def run_doctor() -> int:
    problems: List[Tuple[str, str]] = []

    def record(mark: str, label: str, detail: str = "", fix: str = "") -> None:
        _line(mark, label, detail)
        if mark == FAIL:
            problems.append((label, fix or detail))

    print("\n  TELECOPY — diagnóstico\n")

    # Python ---------------------------------------------------------------
    py = sys.version.split()[0]
    if sys.version_info >= (3, 10):
        record(OK, "Python", py)
    else:
        record(FAIL, "Python", py, "Instale Python 3.10 ou mais novo.")

    # Dependências ---------------------------------------------------------
    telethon = None
    for module, hint, required in (
        ("telethon", f"pip install telethon=={TELETHON_PIN}", True),
        ("cryptography", "pip install cryptography", True),
        ("psutil", "pip install psutil", True),
        ("rich", "pip install rich", False),
        ("cryptg", "pip install cryptg", False),
    ):
        try:
            mod = importlib.import_module(module)
            version = getattr(mod, "__version__", "")
            if module == "telethon":
                telethon = mod
                if version and version != TELETHON_PIN:
                    record(WARN, module, f"{version} (o projeto usa {TELETHON_PIN}; "
                                         "o caminho rápido pode cair no modo padrão)")
                    continue
            record(OK, module, version)
        except ImportError:
            if required:
                record(FAIL, module, "não instalado", f"Rode: {hint}")
            else:
                record(WARN, module, "não instalado (opcional)")

    # APIs internas usadas pelo caminho rápido ----------------------------
    if telethon is not None:
        client_cls = getattr(telethon, "TelegramClient", None)
        missing = [n for n in FAST_INTERNALS if client_cls is None or not hasattr(client_cls, n)]
        if missing:
            record(WARN, "Transferência rápida",
                   "APIs ausentes: " + ", ".join(missing)
                   + " — desligue em Configurações → t ou use telethon==" + TELETHON_PIN)
        else:
            record(OK, "Transferência rápida", "APIs internas do Telethon presentes")

    # Módulos do projeto ----------------------------------------------------
    for module in ("config", "database", "copier", "fasttransfer", "core"):
        try:
            importlib.import_module(module)
            record(OK, f"módulo {module}")
        except Exception as exc:  # noqa: BLE001 — queremos o erro real na tela
            record(FAIL, f"módulo {module}", f"{type(exc).__name__}: {exc}",
                   f"O módulo '{module}' não importa: {type(exc).__name__}: {exc}")

    # Dados / login ---------------------------------------------------------
    try:
        from config import DATA_DIR, LOG_FILE, SESSION_FILE, VAULT_FILE, hardware_profiles
    except Exception:  # noqa: BLE001
        print("\n  Não foi possível carregar config.py — corrija o erro acima primeiro.\n")
        return 1

    try:
        probe = DATA_DIR / ".telecopy-doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        record(OK, "Pasta de dados", str(DATA_DIR))
    except OSError as exc:
        record(FAIL, "Pasta de dados", f"sem gravação: {exc}",
               "Dê permissão de escrita à pasta de dados.")

    session = Path(str(SESSION_FILE) + ".session")
    record(OK if session.exists() else WARN, "Login (sessão)",
           "salvo" if session.exists() else "ainda não feito — será pedido ao abrir")
    record(OK if VAULT_FILE.exists() else WARN, "Credenciais API",
           "salvas" if VAULT_FILE.exists() else "ainda não informadas — serão pedidas ao abrir")

    try:
        from database import db

        health = db.health()
        record(OK if str(health.get("integrity")) == "ok" else FAIL, "Banco de dados",
               f"integridade: {health.get('integrity')}",
               "Banco corrompido: faça backup de data/ e rode python main.py --reset.")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "Banco de dados", f"{type(exc).__name__}: {exc}")

    # Rede ------------------------------------------------------------------
    try:
        with socket.create_connection(("149.154.167.50", 443), timeout=5):
            record(OK, "Telegram (rede)", "alcançável")
    except OSError as exc:
        record(FAIL, "Telegram (rede)", f"inacessível: {exc}",
               "Verifique internet, firewall, VPN ou proxy (porta 443).")

    # Perfis de hardware ------------------------------------------------------
    try:
        profiles = hardware_profiles()
        for name, label in (("recommended", "Perfil recomendado"), ("max", "Perfil máximo")):
            v = profiles[name]
            record(OK, label,
                   f"{v['workers']} workers · {v['connections_per_file']} conexões/arquivo · "
                   f"delay {v['delay']}s · tmp {v['max_tmp_gb']:g} GB")
    except Exception as exc:  # noqa: BLE001
        record(WARN, "Perfis de hardware", f"{type(exc).__name__}: {exc}")

    # Últimos erros do log ----------------------------------------------------
    recent = _tail_problems(LOG_FILE)
    if recent:
        print("\n  Últimos erros no log:")
        for row in recent:
            print(f"    · {row[:150]}")

    print()
    if problems:
        print("  O que corrigir:")
        for label, fix in problems:
            print(f"    {FAIL} {label}: {fix}")
        print()
        return 1
    print("  Nenhum bloqueio encontrado no ambiente.")
    print("  Se ainda não copia, mande a primeira linha de falha mostrada na cópia\n"
          "  e as linhas de log acima.\n")
    return 0


async def doctor_autenticado(client) -> dict:
    """Diagnóstico autenticado somente leitura; não envia nem edita mensagens."""
    from i18n import t
    resultado = {"ok": False, "readonly": True, "checks": []}
    try:
        me = await client.get_me()
        nome = getattr(me, "username", None) or getattr(me, "first_name", "") or str(getattr(me, "id", ""))
        resultado["checks"].append({"check": "auth", "ok": True, "detail": t("doctor.auth_ok", user=nome)})
        dialogs = []
        async for dialog in client.iter_dialogs(limit=5):
            dialogs.append(getattr(dialog, "id", getattr(dialog, "name", "")))
        resultado["checks"].append({"check": "dialogs", "ok": True,
                                    "detail": t("doctor.dialogs_ok", count=len(dialogs))})
        resultado["ok"] = True
        resultado["detail"] = t("doctor.readonly")
    except Exception as exc:
        resultado["checks"].append({"check": "auth", "ok": False,
                                    "detail": t("doctor.auth_fail", reason=f"{type(exc).__name__}: {exc}")})
    return resultado
