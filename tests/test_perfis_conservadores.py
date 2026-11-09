"""Perfis padrão conservadores: hardware não é autorização para acelerar na API."""
from __future__ import annotations

from config import DAILY_LIMIT, RunConfig, hardware_profiles, recommended_settings, turbo_settings


def test_padrao_do_motor_nao_abre_dezenas_de_conexoes() -> None:
    cfg = RunConfig()
    assert cfg.delay >= 1.0
    assert cfg.workers <= 2
    assert cfg.connections_per_file <= 2
    assert cfg.max_total_connections <= 4
    assert cfg.daily_limit == DAILY_LIMIT
    assert cfg.strict_order is True and cfg.resume is True


def test_recomendado_e_conservador_mesmo_em_pc_forte() -> None:
    rec = recommended_settings()
    assert rec["delay"] >= 1.0
    assert rec["workers"] <= 2
    assert rec["connections_per_file"] <= 2
    assert rec["max_total_connections"] <= 4
    assert rec["daily_limit"] == DAILY_LIMIT


def test_maximo_explicito_ainda_tem_piso_de_intervalo_e_teto_de_partes() -> None:
    turbo = turbo_settings()
    assert turbo["delay"] >= 0.5
    assert turbo["workers"] <= 4
    assert turbo["connections_per_file"] <= 4
    assert turbo["max_total_connections"] <= 16
    # Turbo é opt-in explícito: pode remover a parede local, não o cooldown.
    assert turbo["daily_limit"] == 0


def test_perfis_continuam_ordenados_por_conservadorismo() -> None:
    rec, turbo = hardware_profiles()["recommended"], hardware_profiles()["max"]
    for key in ("workers", "connections_per_file", "max_total_connections", "max_tmp_gb"):
        assert rec[key] <= turbo[key]
    assert rec["delay"] >= turbo["delay"]
    assert RunConfig(**rec).to_dict() == RunConfig(**RunConfig(**rec).to_dict()).to_dict()
    assert RunConfig(**turbo).to_dict() == RunConfig(**RunConfig(**turbo).to_dict()).to_dict()
