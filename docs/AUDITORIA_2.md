# Auditoria 2 — execução e achados

Data: 2026-11-08. Branch: `arena/859ccdf0-telecopy`. Esta auditoria cobre todos os módulos
Python de primeiro nível e todos os arquivos `tests/test_*.py` inventariados em
`docs/evidencias/auditoria-inventario.txt`.

## Evidência executada

| Verificação | Comando/registro | Resultado |
|---|---|---|
| Compilação | `docs/evidencias/auditoria-compile.txt` | módulos do repo e testes OK |
| Importação | `docs/evidencias/auditoria-modulos.txt` | 18 módulos importáveis |
| Lint selecionado | `docs/evidencias/auditoria-ruff.txt` | All checks passed após remover imports/variável mortiços |
| Suíte completa | `docs/evidencias/fase3-suite.txt` | 281 passaram |
| Inventário AST | `docs/evidencias/auditoria-inventario.txt` | linhas/classes/funções/async por módulo; testes por arquivo |

`data/pesquisa/` contém código de terceiros baixado apenas para estudo; não é compilado nem
importado pelo produto. Dois arquivos externos têm sintaxe inválida para Python 3.11 e foram
excluídos da compilação do produto.

## Achados por área

### asyncio / cleanup

- `_copy_loop` cancela downloaders e limpa temporários no `finally`; o sender estrito
  `for index in range(total): await ready[index]` permanece intacto.
- `FastTransferPool.close()` acorda waiters, fecha senders próprios e devolve leases em
  cancelamento. `tests/test_flood_control.py` cobre cancelamento durante cooldown.
- `FloodController.wait_cooldown()` não deixa observer quebrado derrubar transferência.
- Risco residual: `Repository.close()` fecha apenas a conexão thread-local; conexões abertas
  em outras threads dependem do processo encerrar. Não há pool global de conexões SQLite.

### Envio → checkpoint

- Confirmado: havia janela entre efeito remoto e `mark_done`.
- Correção: `send_intents` versionado, fingerprint/random_id antes do envio, confirmação depois
  do ID, reconciliação pela cauda do destino por tópico. Ambiguidade bloqueia.
- Evidência: `tests/test_intents.py`, `docs/evidencias/intents-antes.txt` e `intents-suite.txt`.
- Limite honesto: não há exactly-once distribuído; correspondência única é heurística conservadora.

### Erros

- Política central em `politica_erros.py`; tabela em `docs/ERROS.md`.
- Auth terminal não troca conta; RPC desconhecido não retenta cegamente.
- FloodPremiumWait deixou de ser exceção que mantinha capacidade.

### Recursos / disco / texto / entidades / mídia

- Progresso agora soma bytes reais com máxima monotônica por tarefa (`tests/test_progresso_bytes.py`).
- Perfis padrão conservadores: 1s, até 2 arquivos, 2 partes/arquivo, 4 globais; máximo explícito
  0,5s, 4/4/16. Hardware só reduz tetos.
- Temporários têm limpeza em erro/cancelamento e sweep inicial; nomes lógicos preservados no upload.
- `split_text`/entidades UTF-16 têm testes existentes e propriedades adicionadas na Fase 6.
- Álbuns até 10 via UploadMedia/SendMultiMedia; fórum desliga dedup/assinatura no caminho fiel.

### SQLite / segurança / portabilidade

- WAL, busy_timeout e migração versionada com backup/rollback testado.
- PFX removido do índice e documentado em `docs/SEGURANCA.md`; histórico não reescrito.
- `security.py` tinha variável de exceção não usada; removida. Cofre continua local, sem segredo em log.
- Caminhos de dados por usuário em builds congelados; testes cobrem Windows/Linux/macOS lógico.
- Risco residual: permissões POSIX não se aplicam igual no Windows; documentação alerta.

## Dez melhorias adicionais propostas

1. Persistir prazo de flood entre processos, não só capacidade.
2. Manifesto de temporários com PID/boot para sweep mais seguro.
3. Correlação por episódio/item em todos os logs.
4. Snapshot `max_id` para separar backlog de mensagens novas.
5. Validador `DONE ⇒ dest_msg_id` em doctor/verify.
6. Detectar mudança de identidade/tamanho da mídia durante refresh.
7. Watchdog de disco antes de admitir novo item.
8. Coalescer notificações de progresso por tarefa.
9. Fencing token para dois processos no mesmo par.
10. Matriz de seeds de scheduling nos testes de ordem.
11. Normalização/validação de entidades desconhecidas antes da divisão.
12. Relatório com versão de schema e comando de reprodução.

As melhorias 1/9 exigem projeto próprio de concorrência entre processos; não foram
implementadas silenciosamente. As demais estão total ou parcialmente cobertas pelas fases seguintes.
