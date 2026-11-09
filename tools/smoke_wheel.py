"""Smoke test do wheel instalado — usado pelo CI após `pip install dist/*.whl`.

Valida o que importa para empacotamento:
  1. o console script `telecopy` existe e responde;
  2. os módulos do pacote importam a partir do wheel, não do checkout;
  3. `doctor --json` emite JSON puro e estruturado.

Não trata alcance de rede como falha: o runner do CI pode não alcançar o IP do
Telegram, e isso é requisito de USO, não de EMPACOTAMENTO. Qualquer outra falha
de instalação (módulo ausente, banco inválido, pasta sem escrita) é erro real.

Uso:  python tools/smoke_wheel.py
Exit: 0 ok, 1 falha de empacotamento.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile

# Requisito de uso, não de instalação — ver doctor.run_doctor.
CHECK_REDE = "Telegram (rede)"


def main() -> int:
    # cwd fora do checkout: garante que os imports vêm do wheel instalado.
    with tempfile.TemporaryDirectory() as tmp:
        help_proc = subprocess.run(
            ["telecopy", "--help"], cwd=tmp, capture_output=True, text=True, timeout=120,
        )
        if help_proc.returncode != 0:
            print("✗ `telecopy --help` falhou:")
            print(help_proc.stdout + help_proc.stderr)
            return 1
        if "clone" not in help_proc.stdout or "doctor" not in help_proc.stdout:
            print("✗ `telecopy --help` não lista os subcomandos esperados")
            print(help_proc.stdout)
            return 1
        print("✓ console script `telecopy` responde e lista subcomandos")

        proc = subprocess.run(
            ["telecopy", "doctor", "--json"], cwd=tmp, capture_output=True, text=True, timeout=180,
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            print(f"✗ `doctor --json` não emitiu JSON puro: {exc}")
            print((proc.stdout or "")[:500])
            print((proc.stderr or "")[:500])
            return 1
        print("✓ `doctor --json` emitiu JSON puro")

        checks = data.get("checks") or []
        if "ok" not in data or not checks:
            print(f"✗ JSON sem contrato esperado (ok/checks): {data!r:.300}")
            return 1

        bad = [c for c in checks if c.get("status") == "fail" and c.get("check") != CHECK_REDE]
        if bad:
            print("✗ falhas de instalação no wheel:")
            for c in bad:
                print(f"    {c.get('check')}: {c.get('detail')}")
            return 1

        net = [c for c in checks if c.get("status") == "fail" and c.get("check") == CHECK_REDE]
        if net:
            print(f"! {CHECK_REDE} inacessível no runner — ignorado (requisito de uso)")

        # Confirma que o módulo veio do wheel e não do repositório clonado.
        origin = subprocess.run(
            [sys.executable, "-c", "import opcional, telecopy_cli; print(telecopy_cli.__file__)"],
            cwd=tmp, capture_output=True, text=True, timeout=60,
        )
        if origin.returncode != 0:
            print("✗ módulos do pacote não importam fora do checkout:")
            print(origin.stdout + origin.stderr)
            return 1
        print(f"✓ módulos importam do wheel: {origin.stdout.strip()}")

        print(f"\nSmoke test OK — {len(checks)} checks, 0 falhas de instalação.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
