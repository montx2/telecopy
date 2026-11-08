"""Migração única de preferências da versão com referência/placeholder padrão."""

from __future__ import annotations

import json

from prefs import Preferences


def test_migra_defaults_legados_para_upload_abort_e_prefs_v2(tmp_path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({
        "config": {
            "delivery": "reference",
            "server_copy": True,
            "placeholder": True,
            "on_failure": "placeholder",
            "workers": 3,
        },
        "delivery": "reference",
        "server_copy": True,
        "placeholder": True,
    }), encoding="utf-8")

    migrated = Preferences(path)
    assert migrated.get("prefs_version") == 2
    assert migrated.config.delivery == "upload"
    assert migrated.config.server_copy is False
    assert migrated.config.placeholder is False
    assert migrated.config.on_failure == "abort"
    assert migrated.config.workers == 3
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["prefs_version"] == 2
    assert saved["config"]["delivery"] == "upload"


def test_migracao_preserva_escolhas_nao_iguais_aos_defaults_antigos(tmp_path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({
        "prefs_version": 1,
        "config": {
            "delivery": "forward",
            "server_copy": False,
            "placeholder": False,
            "on_failure": "abort",
        },
    }), encoding="utf-8")

    loaded = Preferences(path)
    assert loaded.config.delivery == "forward"
    assert loaded.config.server_copy is False
    assert loaded.config.placeholder is False
    assert loaded.config.on_failure == "abort"
    assert loaded.get("prefs_version") == 2


def test_prefs_v2_nao_reescreve_escolha_explicitamente_salva(tmp_path) -> None:
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({
        "prefs_version": 2,
        "delivery": "reference",
        "server_copy": True,
        "placeholder": True,
        "config": {
            "delivery": "reference",
            "server_copy": True,
            "placeholder": True,
            "on_failure": "placeholder",
        },
    }), encoding="utf-8")

    loaded = Preferences(path)
    assert loaded.config.delivery == "reference"
    assert loaded.config.server_copy is True
    assert loaded.config.placeholder is True
    assert loaded.config.on_failure == "placeholder"


def test_preferencias_novas_usam_defaults_sem_criar_arquivo(tmp_path) -> None:
    path = tmp_path / "new-prefs.json"
    loaded = Preferences(path)
    assert loaded.get("prefs_version") == 2
    assert loaded.config.delivery == "upload"
    assert loaded.config.server_copy is False
    assert loaded.config.placeholder is False
    assert loaded.config.on_failure == "abort"
    assert not path.exists()
