# Relatório final — TELECOPY 4.1

Data: 2026-11-08 · Branch: `arena/859ccdf0-telecopy` · Base: `826a211`

## Resumo executivo

O trabalho cobre auditoria, correções de integridade, CLI não interativa,
recursos opt-in, testes de regressão/caos/soak, CI, empacotamento e
documentação. A suíte final está verde:

- **364 testes passaram** em ~67s, sem rede real (`docs/evidencias/empacotamento-suite.txt`).
- **Ruff limpo** nas regras configuradas (`docs/evidencias/ruff-final.txt`).
- **Wheel/sdist construídos** e smoke test do wheel instalado (`docs/evidencias/build.txt`).
- **Mutação**: `politica_erros.py` com **100/100 mutantes mortos**, 0 sobreviventes
  (`docs/evidencias/mutacao-politica.txt` / `.xml`).
- **Cobertura crítica medida**: **86%** no conjunto auditado; `copier.py` em 82%.
  A meta de ≥90% **não foi atingida** e está registrada como pendência honesta
  (`docs/evidencias/coverage-critica.txt` / `.json`).

Nenhum login, cópia, ban ou medição de vazão real no Telegram foi executado.
Tudo que depende da rede real permanece não validado em produção.

## O que foi entregue

### Integridade e retomada
- Entrega atômica no SQLite: checkpoint + processed + dedup + limpeza de falha +
  confirmação de intenção num único `BEGIN IMMEDIATE` (`database.py`).
- Intenções duráveis (`send_intents`, schema v1) para texto/mídia/nativo/forward/
  marcador, com fingerprint canônico e reconciliação pela cauda do destino.
- Janelas de crash restantes fechadas para forward/referência/nativo/marcador:
  rotas já resolvidas não reenviam; marcador não duplica no mesmo processo.
- Verificação dirigida pela origem, com filtro consistente com o clone; reparo
  apenas de confirmados inequívocos.
- Cofre fail-closed: erro de disco na chave local é `VaultError`, nunca fallback
  previsível; criação exclusiva `0600` com `fsync`.

### Operação não interativa
- CLI `telecopy` com `clone`, `verify`, `archive`, `ui`, `doctor`; JSON, rehearsal,
  relatório HTML+JSON, códigos de saída 0/1/2. `doctor --json` emite JSON puro
  (`{ok, checks, problems}`); o texto humano continua o padrão sem a flag.
- Sinais portáveis (SIGINT/SIGTERM/SIGBREAK quando existir), restauração de
  handlers e cancelamento via `call_soon_threadsafe`; CLI não abre prompt em erro.
- O entrypoint interativo (`main.py`) também converte SIGTERM/SIGBREAK em
  `KeyboardInterrupt` e restaura os handlers antigos no `finally`.
- Política explícita de erros (`politica_erros.py`, `docs/ERROS.md`): auth terminal
  não troca conta; RPC desconhecido não retenta cegamente; flood espera portão.

### Recursos P1 (opt-in, somente CLI)
- Proxy SOCKS/HTTP (`--proxy`, extra `socks`).
- Janela de horário `--inicio/--fim`, inclusive virando meia-noite.
- Limitador local de banda `--bandwidth` (token bucket; não mede a rede real).
- Reescrita `--reescrever-de/-para`: remove entidades quando altera o texto e é
  recusada em modo forward.
- Webhook `--notificar-url`: falha só registra log.
- `archive`: manifesto JSONL + `media/`, somente leitura da origem.
- `ui`: HTTP local somente leitura, token obrigatório, comparação constant-time,
  guarda contra path traversal, bind padrão `127.0.0.1`.

### Instalação no Android (Termux)
- `termux-setup.sh`: um comando, idempotente, com verificação automática via
  `doctor --json` e fallbacks (pkg → compilação; pip global → venv com
  `--system-site-packages`). Nativas via `pkg`, Python puro via pip.
- Rede indisponível é tratada como aviso, não como falha de instalação.
- Guia e solução de problemas em `docs/TERMUX.md`; shellcheck no CI.
- **Validado em Linux; execução em Android real ainda não testada.**

### Empacotamento
- `app_meta.py` substitui o `import __init__` (não instalável): o wheel quebrava o
  menu interativo com `ModuleNotFoundError`. Versão unificada em 4.1.0.
- `tools/smoke_wheel.py` valida o wheel instalado a partir de fora do checkout;
  alcance de rede é aviso, não falha de empacotamento.

### Qualidade e infraestrutura
- Testes de regressão antes/depois para cada correção relevante, propriedades
  Hypothesis, caos com relógio simulado e soak local de 100k linhas/intenções.
- `FakeClient.iter_messages` aplica o filtro de tópico **antes** do `limit`, como
  a API real (regressão em `tests/test_fake_iter_topico.py`).
- CI GitHub Actions: matriz Ubuntu/Windows × Python 3.10/3.11/3.12, lint, suíte,
  cobertura, build e smoke test de wheel. **Executado no GitHub: os 6 jobs de
  teste e o shellcheck passaram** (inclusive Windows, antes não validado).
- Workflow manual de mutação (`mutmut<3`) para lógica crítica pura.
- `pyproject.toml` com extras `dev`, `fast`, `socks`; módulo `opcional` no wheel.
- README atualizado para 4.1 com CLI, recursos opt-in e estado real de testes.

## Critérios de aceite — estado honesto

| Critério | Estado | Evidência |
|---|---|---|
| Suíte verde sem rede | **Atendido** | `empacotamento-suite.txt` (364 passed) |
| Lint limpo | **Atendido** | `ruff-final.txt` |
| Regressões antes/depois | **Atendido** | `docs/evidencias/*-antes.txt` / `*-depois.txt` |
| Empacotamento + smoke | **Atendido local** | `build.txt` |
| Mutação em lógica crítica | **Parcial** | `politica_erros.py` 100/100; `dedup.py` não executado |
| Cobertura crítica ≥90% | **Não atingido** | 86% total; `copier.py` 82% |
| CI multi-plataforma | **Executado e verde** | 6 jobs: Ubuntu+Windows × 3.10/3.11/3.12 |
| Validação real no Telegram | **Não executada** | sem credenciais/rede real |
| Exactly-once distribuído | **Impossível/ não prometido** | reconciliação conservadora; ambiguidade bloqueia |

## Limitações e riscos residuais

1. **Cobertura**: `copier.py` ainda tem muitos ramos de UI/espelho/erro não
   exercitados; a meta de 90% no conjunto crítico permanece aberta.
2. **Retry na mesma execução**: após efeito remoto e antes da confirmação, uma
   retentativa imediata ainda pode duplicar; a reconciliação forte ocorre no
   início da execução e na verificação, não em cada retry interno.
3. **IDs de destino armazenados**: `_send` confia no `dest_msg_id` persistido sem
   revalidar existência remota em todos os caminhos.
4. **Flood entre processos/DC**: estado de flood é por processo/capacidade
   compartilhada; não há coordenação multiprocesso nem particionamento por DC.
5. **Menu interativo**: `main.py` converte SIGTERM/SIGBREAK em interrupção limpa,
   mas não usa o cancelamento cooperativo de tarefa da CLI (`_com_sinais`); o
   cancelamento no menu depende do `KeyboardInterrupt` propagar pelo asyncio.
6. **FakeClient**: não modela paginação real do Telegram (offset_id/offset_rate),
   apenas ordem/limit/tópico; suficiente para os testes atuais.
7. **Windows**: a suíte passou no CI em Windows × 3.10/3.11/3.12. Uso interativo
   real (console, fechar janela, .exe) ainda não foi exercitado.
8. **Segurança administrativa**: `assinatura.pfx` foi removido do índice, mas o
   histórico não foi reescrito; revogação/reemissão e limpeza de histórico exigem
   ação do proprietário (`docs/SEGURANCA.md`).

## Como reproduzir

```bash
python -m pip install -r requirements-dev.txt -e ".[fast,socks]"
ruff check --exclude data --exclude .venv .
python -m pytest -o addopts='' -q --timeout=60
python -m build
mutmut run --paths-to-mutate=politica_erros.py \
  --runner="python -m pytest -o addopts='' -q --timeout=30 tests/test_politica_erros.py"
```

## Próximos passos recomendados

1. Elevar cobertura do `copier.py` (espelho, recuperação de placeholders, ramos de
   erro e UI) até ≥90% no conjunto crítico.
2. Executar a matriz CI no GitHub e o workflow de mutação para `dedup.py`.
3. Validar em conta/canal de teste reais: login, flood, referência expirada,
   migração de DC/CDN, vazão, operação desassistida de 12h e fechamento no Windows.
4. Projetar coordenação de flood por DC/multiprocesso e fencing token para o mesmo
   par origem/destino.
5. Revalidar IDs de destino persistidos antes de confiar em retomadas longas.
