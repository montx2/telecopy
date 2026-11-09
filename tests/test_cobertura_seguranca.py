import pytest
from security import VaultError, _load_or_create_local_key




def test_chave_local_nunca_usa_fallback_previsivel(tmp_path, monkeypatch) -> None:
    import security

    def negado(*args, **kwargs):
        raise OSError("disco somente leitura")

    monkeypatch.setattr(security.secrets, "token_bytes", negado)
    with pytest.raises(VaultError):
        _load_or_create_local_key(tmp_path / "novo")
    monkeypatch.undo()
    key = _load_or_create_local_key(tmp_path / "ok")
    assert len(key) == 32
    assert _load_or_create_local_key(tmp_path / "ok") == key
    (tmp_path / "curta").mkdir()
    (tmp_path / "curta" / "local.key").write_bytes(b"x")
    with pytest.raises(VaultError):
        _load_or_create_local_key(tmp_path / "curta")
