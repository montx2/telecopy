"""Regressão do instalador Termux: sintaxe, guarda de ambiente e verificação.

O script não pode ser exercitado de ponta a ponta fora do Android (não há `pkg`),
mas os caminhos que não dependem do Termux são testáveis: ajuda, guarda de
ambiente, parsing de opções, verificação automática e diagnóstico de falha.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "termux-setup.sh"
BASH = shutil.which("bash")

# O instalador é para Termux (Linux-like). No Windows o bash é o do Git, com
# tratamento de paths/shebang diferente — testar ali só geraria falso negativo.
# O job `shell` do CI (Ubuntu) cobre shellcheck + sintaxe.
pytestmark = pytest.mark.skipif(
    BASH is None or sys.platform == "win32",
    reason="instalador Termux é validado em POSIX; bash indisponível ou plataforma Windows",
)


def _run(*args: str, env_extra: dict | None = None, force: bool = True,
         with_deps: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    if force:
        env["TELECOPY_SETUP_FORCE"] = "1"
    else:
        env.pop("TELECOPY_SETUP_FORCE", None)
    if with_deps:
        # Garante que `python` no PATH é o interpretador que tem as dependências.
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [BASH, str(SCRIPT), *args], cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=180,
    )


def test_script_existe_e_sintaxe_valida() -> None:
    assert SCRIPT.is_file(), "termux-setup.sh deve existir na raiz do repo"
    proc = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_help_lista_opcoes_e_nao_instala() -> None:
    proc = _run("--help")
    assert proc.returncode == 0
    for flag in ("--verify-only", "--no-upgrade", "--with-cryptg", "--dir", "--ref"):
        assert flag in proc.stdout
    # A ajuda não pode disparar instalação.
    assert "pkg update" not in proc.stdout


def test_recusa_fora_do_termux_sem_force() -> None:
    """Sem PREFIX do Termux e sem force, deve sair 2 e orientar o caminho de PC."""
    proc = _run(force=False, env_extra={"PREFIX": "/usr"})
    assert proc.returncode == 2
    assert "Termux" in (proc.stderr + proc.stdout)


def test_opcao_desconhecida_sai_2() -> None:
    proc = _run("--opcao-que-nao-existe")
    assert proc.returncode == 2
    assert "desconhecida" in proc.stderr


def test_dir_exige_valor() -> None:
    proc = _run("--dir")
    assert proc.returncode == 2


def test_verify_only_detecta_projeto_ausente() -> None:
    proc = _run("--verify-only", "--dir", "/caminho/inexistente/telecopy-xyz")
    assert proc.returncode == 1
    assert "não encontrado" in proc.stderr


def test_verify_only_passa_com_dependencias() -> None:
    """Com as deps do ambiente de teste, a verificação automática deve passar.

    Rede é requisito de uso, não de instalação: o script não pode falhar só
    porque o Telegram está inacessível (CI/offline).
    """
    proc = _run("--verify-only")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "import telethon" in out
    assert "import cryptography" in out
    assert "import psutil" in out
    assert "doctor" in out
    # Não pode tentar instalar nada no modo verify.
    assert "pkg update" not in out


def test_verify_only_nao_promete_teste_em_android() -> None:
    """O script deve declarar honestamente que não foi testado em Android real."""
    proc = _run("--verify-only")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Android real" in proc.stdout
    assert "docs/TERMUX.md" in proc.stdout


def test_guia_termux_existe_e_cobre_fallbacks() -> None:
    guia = ROOT / "docs" / "TERMUX.md"
    assert guia.is_file()
    texto = guia.read_text(encoding="utf-8")
    for trecho in ("python-cryptography", "python-psutil", "F-Droid",
                   "termux-wake-lock", "--system-site-packages", "termux-setup.sh"):
        assert trecho in texto, f"guia deveria mencionar {trecho}"
