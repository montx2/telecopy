"""Regressão de empacotamento: o pacote tem de funcionar instalado como wheel.

Bug real: `core.py` fazia `import __init__`, e um `__init__` de topo nível não é
instalado como módulo — o wheel quebrava com ModuleNotFoundError no menu
interativo. Os metadados agora vivem em `app_meta`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import app_meta

ROOT = Path(__file__).resolve().parents[1]


def test_nenhum_modulo_do_produto_importa___init__() -> None:
    offenders = []
    for path in ROOT.glob("*.py"):
        if path.name in {"__init__.py", "app_meta.py"}:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(import\s+__init__|from\s+__init__\s+import)", line):
                offenders.append(f"{path.name}:{n}")
    assert not offenders, f"import de __init__ quebra o wheel: {offenders}"


def test_core_importa_sem___init__disponivel() -> None:
    """Reproduz o ambiente do wheel: `__init__` não é importável."""
    code = """
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name == "__init__":
            raise ModuleNotFoundError("No module named '__init__'")
        return None
sys.meta_path.insert(0, Block())
import core
import app_meta
assert app_meta.__version__
print("ok")
"""
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout


def test_app_meta_esta_no_wheel_metadata() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    bloco = pyproject.split("py-modules = [", 1)[1].split("]", 1)[0]
    assert '"app_meta"' in bloco, "app_meta precisa estar em py-modules"


def test_versao_consistente_entre_app_meta_e_pyproject() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert match, "versão não encontrada no pyproject"
    assert app_meta.__version__ == match.group(1), (
        f"app_meta {app_meta.__version__} != pyproject {match.group(1)}"
    )


def test_init_reexporta_metadados() -> None:
    import importlib

    meta = importlib.import_module("__init__")
    assert meta.__version__ == app_meta.__version__
    assert meta.__author__ == app_meta.__author__


def test_smoke_wheel_script_existe_e_compila() -> None:
    script = ROOT / "tools" / "smoke_wheel.py"
    assert script.is_file()
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", str(script)], capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def test_smoke_nao_trata_rede_como_falha_de_instalacao() -> None:
    """O CI pode não alcançar o Telegram; isso não pode reprovar o empacotamento."""
    texto = (ROOT / "tools" / "smoke_wheel.py").read_text(encoding="utf-8")
    assert "CHECK_REDE" in texto
    assert 'c.get("check") != CHECK_REDE' in texto
