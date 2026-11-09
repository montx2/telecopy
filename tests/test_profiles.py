"""Perfis de hardware (recomendado x máximo) e consistência com o analisador."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    DAILY_LIMIT,
    WORKERS_HARD_CAP,
    RunConfig,
    hardware_profiles,
    recommended_settings,
    turbo_settings,
)
from system_analyzer import analyzer  # noqa: E402


def test_recomendado_nunca_passa_do_maximo() -> None:
    rec, turbo = recommended_settings(), turbo_settings()
    assert 1 <= rec["workers"] <= turbo["workers"] <= WORKERS_HARD_CAP
    assert rec["connections_per_file"] <= turbo["connections_per_file"]
    assert rec["max_total_connections"] <= turbo["max_total_connections"]
    assert rec["max_tmp_gb"] <= turbo["max_tmp_gb"]


def test_recomendado_protege_a_conta() -> None:
    rec = recommended_settings()
    assert rec["delay"] >= 1.0         # intervalo conservador
    assert rec["daily_limit"] == DAILY_LIMIT  # limite diário mantido


def test_perfis_sao_aceitos_pelo_runconfig() -> None:
    for values in hardware_profiles().values():
        cfg = RunConfig(**values)
        assert cfg.workers == values["workers"]
        assert cfg.fast_transfer is True


def test_analisador_usa_as_mesmas_regras() -> None:
    minimum, maximum = analyzer.worker_bounds()
    assert minimum == 1
    assert maximum == turbo_settings()["workers"]
    assert minimum <= analyzer.recommended() <= maximum


def test_split_text_definida_uma_vez() -> None:
    src = (ROOT / "copier.py").read_text(encoding="utf-8")
    assert len(re.findall(r"^def split_text\(", src, re.M)) == 1


def test_split_text_respeita_limite_utf16() -> None:
    from copier import split_text, utf16_len

    texto = ("😀 palavra " * 900).strip()
    partes = split_text(texto, 4096)
    assert all(utf16_len(p) <= 4096 for p in partes)
    assert "".join(partes) == texto


if __name__ == "__main__":
    falhas = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except AssertionError as exc:
                falhas += 1
                print(f"  ✗ {name}: {exc!r}")
    sys.exit(1 if falhas else 0)
