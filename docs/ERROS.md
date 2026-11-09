# Política de erros — TELECOPY

Classificação central: `politica_erros.classificar()`. O motor consulta a política em
`Copier._is_transient` e `Copier._is_fatal_send_error`; o transporte de partes mantém
o tratamento específico de flood/migração/refresh já testado.

| Classe / exemplo | Ação | Retry? | Observação |
|---|---|---:|---|
| `FLOOD_WAIT_x`, `FLOOD_PREMIUM_WAIT_x` | esperar portão compartilhado; AIMD; fallback após orçamento | sim, limitado | não pula item; não troca conta |
| `AuthKeyUnregistered/Invalid`, `SessionRevoked/Expired`, `UserDeactivated*` | parar autenticação | não | exige novo login autorizado; sem rodízio de contas |
| `ChatAdminRequired`, `ChatWriteForbidden`, `ChannelPrivate/Invalid`, `PeerIdInvalid`, `UserBannedInChannel` | parar destino/item | não | falha estrutural de permissão |
| `FileReferenceExpired/Invalid`, `FilerefUpgradeNeeded` | refresh limitado; fallback upload se permitido | sim, limitado | não altera modo de referência escolhido sem necessidade |
| `FileMigrateError` | migrar DC preservando parte/ordem | sim, até 3 | não contorna flood |
| `ServerError`, `RpcCallFail`, `TimedOut`, `ConnectionError`, `OSError` | retry com orçamento/backoff | sim, limitado | transporte |
| `RPCError` desconhecido | parar e registrar | não | evita retry cego de erro novo |
| `ValueError` histórico do Telethon padrão | tratado como transporte pelo chamador | sim, limitado | compatibilidade com falhas internas conhecidas |

## Evidência

- `tests/test_politica_erros.py`: flood, auth terminal, referência, RPC desconhecido, tabela serializável.
- `docs/evidencias/erros-depois.txt`: 5 passaram.
- `docs/evidencias/fase3-suite.txt`: 281 passaram após integração.

## Limites

A tabela não substitui a documentação oficial do Telegram nem prevê todos os erros futuros.
Erros desconhecidos são registrados com tipo/mensagem e interrompem o caminho automático.
