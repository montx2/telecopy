import pytest

from telethon import errors
from politica_erros import classificar, resumo_politica
from copier import Copier


def test_flood_espera_portao_nao_vira_fatal_de_envio() -> None:
    p = classificar(errors.FloodWaitError(request=None, capture=3))
    assert p.acao == "esperar_portao" and p.transitório and not p.fatal


def test_auth_terminal_nao_troca_conta() -> None:
    p = classificar(errors.AuthKeyUnregisteredError(request=None))
    assert p.fatal and p.acao == "parar_autenticacao"
    assert Copier._is_fatal_send_error(errors.AuthKeyUnregisteredError(request=None))


def test_referencia_tem_refresh_limitado() -> None:
    p = classificar(errors.FileReferenceExpiredError(request=None))
    assert p.acao == "refresh_ou_fallback" and p.transitório


def test_rpc_desconhecido_nao_retenta_cegamente() -> None:
    class NovoRPC(errors.RPCError):
        pass
    p = classificar(NovoRPC(request=None, message="novo"))
    assert p.fatal and p.acao == "parar_sem_retry_cego"
    assert not Copier._is_transient(NovoRPC(request=None, message="novo"))


def test_tabela_serializavel() -> None:
    rows = resumo_politica()
    assert len(rows) >= 7 and all({"classe", "acao"} <= row.keys() for row in rows)


def test_politica_contrato_completo_contra_mutacoes() -> None:
    """Trava valores exatos (ação/flags/motivo) e imutabilidade do contrato."""
    from dataclasses import FrozenInstanceError

    from politica_erros import PoliticaErro, classificar, resumo_politica

    class Nomeada(Exception):
        pass

    def com_nome(nome: str) -> Exception:
        return type(nome, (Nomeada,), {})()

    auth = ["AuthKeyUnregisteredError", "AuthKeyInvalidError", "SessionRevokedError",
            "SessionExpiredError", "UserDeactivatedBanError", "UserDeactivatedError",
            "AccessTokenExpiredError", "AccessTokenInvalidError"]
    destino = ["ChatAdminRequiredError", "ChatForbiddenError", "ChatWriteForbiddenError",
               "ChatSendMediaForbiddenError", "ChatSendPlainForbiddenError", "ChannelInvalidError",
               "ChannelPrivateError", "PeerIdInvalidError", "UserBannedInChannelError",
               "UserNotParticipantError", "ChatIdInvalidError", "MsgIdInvalidError"]
    trans = ["ServerError", "RpcCallFailError", "TimedOutError", "ServiceUnavailableError",
             "InternalError", "NetworkMigrateError"]
    ref = ["FileReferenceExpiredError", "FileReferenceInvalidError", "FilerefUpgradeNeededError"]
    casos = [(com_nome(n), "parar_autenticacao", False, True, "auth") for n in auth]
    casos += [(com_nome(n), "parar_destino", False, True, "destino") for n in destino]
    casos += [(com_nome(n), "retentar_com_orcamento", True, False, "transporte") for n in trans]
    casos += [(com_nome(n), "refresh_ou_fallback", True, False, "referencia") for n in ref]
    casos += [
        (com_nome("FileMigrateError"), "migrar_dc", True, False, "migracao"),
        (ConnectionError("x"), "retentar_com_orcamento", True, False, "transporte"),
        (TimeoutError("x"), "retentar_com_orcamento", True, False, "transporte"),
        (OSError("x"), "retentar_com_orcamento", True, False, "transporte"),
        (ValueError("x"), "avaliar_chamador", False, False, "nao_telegram"),
    ]
    from telethon.errors import FloodWaitError, RPCError

    casos.append((FloodWaitError(request=None, capture=30), "esperar_portao", True, False, "flood"))
    casos.append((RPCError(request=None, message="NOVO_ERRO"), "parar_sem_retry_cego", False, True, "rpc_desconhecido"))
    for exc, acao, trans, fatal, motivo in casos:
        p = classificar(exc)
        assert (p.acao, p.transitório, p.fatal, p.motivo) == (acao, trans, fatal, motivo), type(exc).__name__
    padrao = PoliticaErro("x")
    assert padrao.transitório is False and padrao.fatal is False and padrao.motivo == ""
    with pytest.raises(FrozenInstanceError):
        padrao.acao = "y"  # type: ignore[misc]
    tabela = resumo_politica()
    assert [r["classe"] for r in tabela] == [
        "FloodWait/FloodPremiumWait", "AuthKey*/Session*/UserDeactivated*",
        "Chat*/Channel*/Peer*/UserBanned*", "FileReference*", "FileMigrateError",
        "ServerError/RpcCallFail/TimedOut/ConnectionError", "RPCError desconhecido",
    ]
    assert all(set(row) == {"classe", "acao"} and row["acao"] for row in tabela)
    assert [r["acao"] for r in tabela] == [
        "esperar portão compartilhado; AIMD; fallback após orçamento",
        "parar; não trocar conta nem contornar ban",
        "parar item/destino; sem retry cego",
        "refresh limitado; fallback upload se permitido",
        "migrar DC preservando parte/ordem",
        "retry limitado com orçamento",
        "parar e registrar; não retentar cegamente",
    ]
