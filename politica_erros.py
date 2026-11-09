"""Política explícita de erros Telegram/transporte (PT-BR, sem dependências novas).

A classificação é conservadora: erro desconhecido não é retentado cegamente.
"""
from __future__ import annotations

from dataclasses import dataclass

from telethon.errors import FloodError, RPCError


@dataclass(frozen=True)
class PoliticaErro:
    acao: str
    transitório: bool = False
    fatal: bool = False
    motivo: str = ""


_TERMINAIS_AUTH = {
    "AuthKeyUnregisteredError", "AuthKeyInvalidError", "SessionRevokedError",
    "SessionExpiredError", "UserDeactivatedBanError", "UserDeactivatedError",
    "AccessTokenExpiredError", "AccessTokenInvalidError",
}
_TERMINAIS_DESTINO = {
    "ChatAdminRequiredError", "ChatForbiddenError", "ChatWriteForbiddenError",
    "ChatSendMediaForbiddenError", "ChatSendPlainForbiddenError", "ChannelInvalidError",
    "ChannelPrivateError", "PeerIdInvalidError", "UserBannedInChannelError",
    "UserNotParticipantError", "ChatIdInvalidError", "MsgIdInvalidError",
}
_TRANSIENTES = {
    "ServerError", "RpcCallFailError", "TimedOutError", "ServiceUnavailableError",
    "InternalError", "NetworkMigrateError",
}
_REFERENCIA = {"FileReferenceExpiredError", "FileReferenceInvalidError", "FilerefUpgradeNeededError"}


def classificar(exc: BaseException) -> PoliticaErro:
    nome = type(exc).__name__
    if isinstance(exc, FloodError):
        return PoliticaErro("esperar_portao", transitório=True, motivo="flood")
    if nome in _TERMINAIS_AUTH:
        return PoliticaErro("parar_autenticacao", fatal=True, motivo="auth")
    if nome in _TERMINAIS_DESTINO:
        return PoliticaErro("parar_destino", fatal=True, motivo="destino")
    if nome in _REFERENCIA:
        return PoliticaErro("refresh_ou_fallback", transitório=True, motivo="referencia")
    if nome == "FileMigrateError":
        return PoliticaErro("migrar_dc", transitório=True, motivo="migracao")
    if nome in _TRANSIENTES or isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return PoliticaErro("retentar_com_orcamento", transitório=True, motivo="transporte")
    if isinstance(exc, RPCError):
        return PoliticaErro("parar_sem_retry_cego", fatal=True, motivo="rpc_desconhecido")
    return PoliticaErro("avaliar_chamador", motivo="nao_telegram")


def resumo_politica() -> list[dict[str, str]]:
    """Tabela serializável para docs/relatórios; não executa rede."""
    return [
        {"classe": "FloodWait/FloodPremiumWait", "acao": "esperar portão compartilhado; AIMD; fallback após orçamento"},
        {"classe": "AuthKey*/Session*/UserDeactivated*", "acao": "parar; não trocar conta nem contornar ban"},
        {"classe": "Chat*/Channel*/Peer*/UserBanned*", "acao": "parar item/destino; sem retry cego"},
        {"classe": "FileReference*", "acao": "refresh limitado; fallback upload se permitido"},
        {"classe": "FileMigrateError", "acao": "migrar DC preservando parte/ordem"},
        {"classe": "ServerError/RpcCallFail/TimedOut/ConnectionError", "acao": "retry limitado com orçamento"},
        {"classe": "RPCError desconhecido", "acao": "parar e registrar; não retentar cegamente"},
    ]
