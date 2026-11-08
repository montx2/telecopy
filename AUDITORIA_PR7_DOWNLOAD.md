# Auditoria do PR #7 — plano para motor download + upload

## Estado e baseline

- Base auditada: `origin/main` em `588ad071bab3d4731aea62c90e6c46de8f13b32b`.
- A branch fixa desta sessão está no mesmo commit; nenhuma mudança de produção foi feita durante a FASE 0.
- `pytest -q`: 185 passaram (11 arquivos `test_*.py`).
- Pyflakes, nos arquivos Python rastreados: dois avisos preexistentes — `copier.py:308` (`split_text`) e `security.py:68` (`exc`).
- `data/` voltou ao estado inicial (não presente); nenhum dado do usuário foi mantido ou alterado.
- O diff real `origin/main~1..origin/main` é de 21 arquivos, +4.200/−255 linhas. Isso difere dos 24 arquivos, +6.912/−388 linhas indicados na solicitação; a auditoria usa o diff existente no checkout.

## Inventário dos hunks

Os números após `+` indicam a linha inicial de cada hunk adicionado no diff real. Hunks do mesmo bloco funcional estão agrupados na linha da tabela.

| Arquivo / hunks | Classe | Avaliação |
|---|---|---|
| `AUDITORIA.md +1` | [risco] | Relatório e afirmações de auditoria; documentação, não runtime. |
| `README.md +296,+315,+333,+349,+561` | [motor referência/placeholder] [risco] | Documenta rotas, placeholders, ordem e medições anteriores; revalidar os claims antes de reutilizar. |
| `audit/probes_1.py +1`; `probes_2.py +1`; `probes_3_mirror.py +1` | [ordem/segurança] [risco] | Sondas para forward, retry e espelho. Manter como ferramentas de diagnóstico offline, não como lógica de runtime. |
| `config.py +145,+713,+723,+747` | [motor referência/placeholder] [risco] | Sobe retries e adiciona `placeholder`, `delivery`, `server_copy`, `on_failure`, `max_retries`; os defaults atuais preferem referência e placeholder. |
| `copier.py +22,+35,+39,+54,+255,+342,+349,+369,+375` | [correção real] [motor referência/placeholder] | Imports, serialização JSON de entidades para recuperação e métricas de referência/placeholder. |
| `copier.py +521,+577,+581,+589,+609,+621,+675,+758,+762` | [ordem/segurança] [motor referência/placeholder] [risco] | `run`, preflight e auditoria; inclui `noforwards + forward` forçando referência. |
| `copier.py +1006,+1042,+1061,+1064,+1066,+1070,+1079,+1085,+1087,+1089,+1093,+1106,+1114,+1116,+1121,+1122,+1124,+1126,+1134,+1169,+1176,+1178,+1180,+1183` | [ordem/segurança] [risco] | Reserva de posição e `_forward_loop`: valida IDs, retries e barreira de ordem; preservar e revalidar falhas ambíguas. |
| `copier.py +1251,+1258,+1291,+1297,+1300,+1311,+1314,+1321,+1326,+1327,+1330,+1337,+1344` | [ordem/segurança] [risco] | Pipeline downloader/sender e barreira de envio; falha terminal deve bloquear todos os índices posteriores. |
| `copier.py +1481,+1500,+1538,+1581,+1586,+1590` | [correção real] [motor referência/placeholder] | `_prepare`: retry/refresh de download e escolha da referência; preparação da reserva de posição. |
| `copier.py +1718,+1721,+1752,+1826,+1829,+1937,+2116,+2157,+2168,+2192,+2197,+2282,+2287` | [correção real] [motor referência/placeholder] | `_send`, mídia/álbuns, referência e upload; referência permanece modo opcional, não padrão. |
| `copier.py +2483,+2487,+2489,+2502,+2504,+2507,+2510,+2515,+2527,+2531,+2558,+2572,+2580` | [ordem/segurança] [motor referência/placeholder] [risco] | `retry_messages` e política de falha; placeholders podem avançar com conteúdo incompleto. |
| `copier.py +2673,+2691,+2747,+2752,+2757,+2776,+2790,+2797` | [ordem/segurança] [correção real] | Espelho, checkpoints e estado final; preservar serialização por tópico e confirmação antes de `DONE`. |
| `core.py +1187,+1440,+1601` | [ordem/segurança] [risco] | Confirmação de filtros e dedup no clone; filtros/dedup alteram a sequência selecionada. |
| `core.py +1492,+1536,+1541,+1959,+2245` | [correção real] [motor referência/placeholder] | Agregação de métricas e resultados de multi-destino/fila. |
| `core.py +2086,+2105,+2271,+2473,+2502,+2521,+2537,+2558` | [ordem/segurança] [motor referência/placeholder] | Ferramentas de verificação da ordem e recuperação de placeholders; manter verificação, não usar recuperação no caminho padrão. |
| `core.py +2786,+2802` | [correção real] | Exibição do resultado e das falhas/placeholders. |
| `database.py +12,+29,+140` | [motor referência/placeholder] | Tabelas/estados aditivos de placeholders. Manter código/schema, deixar inativos no padrão. |
| `database.py +265,+416,+954` | [correção real] [motor referência/placeholder] | Saúde/reset da base passam a incluir as novas tabelas. |
| `database.py +664` | [ordem/segurança] [motor referência/placeholder] | Métodos de posição e ordem; preservar consultas de ordem, não depender de reservas no modo padrão. |
| `i18n.py +302` | [ordem/segurança] [motor referência/placeholder] | Textos PT/EN de ordem, falha, entrega e recuperação. Manter textos de ordem/falha. |
| `prefs.py +25,+48,+58,+79,+90` | [motor referência/placeholder] [risco] | Sincroniza opções no topo e em `config`, mas não adiciona versão nem marcador de escolha explícita. |
| `tests/fake_telegram.py +18,+156,+222,+234,+265,+320,+332,+350,+359,+361` | [correção real] [ordem/segurança] [motor referência/placeholder] | Fake modela erro de referência/edição e IDs confirmados do forward; preservar e estender para uploads. |
| `tests/test_app_flow.py +337,+351` | [refactor] | Limpeza pequena de variável/import sem efeito no comportamento. |
| `tests/test_core.py +17,+483,+785,+817` | [correção real] [ordem/segurança] [motor referência/placeholder] | Fake confirma IDs do forward; ajustes de testes para mídia não suportada e retry. |
| `tests/test_forward_ordem.py +1` | [ordem/segurança] | Regressões de forward ordenado; manter. |
| `tests/test_mirror_ordem.py +1` | [ordem/segurança] | Regressões de ordem do espelho; manter. |
| `tests/test_ordem.py +1` | [ordem/segurança] [motor referência/placeholder] | Cobertura de ordem, referência e modos de entrega; manter ordem e referência explícita. |
| `tests/test_ordem_preflight.py +1` | [ordem/segurança] | Regressões do preflight; manter. |
| `tests/test_placeholder.py +1` | [motor referência/placeholder] | Cobertura do mecanismo de placeholder; manter código/testes, mas tirar o comportamento do padrão. |
| `tests/test_retry_parcial.py +1` | [correção real] [ordem/segurança] | Evita duplicar conteúdo confirmado ao retentar; manter. |
| `tests/test_ui_order.py +1` | [ordem/segurança] | Cobertura da ferramenta de ordem; manter. |

## Componentes a preservar / desativar no padrão

- Preservar `Copier._preflight_order`, `_forward_ids_valid`, `_forward_loop`, `_tool_order`, consultas de ordem no banco e testes de ordem/retry/espelho.
- Preservar migração aditiva de checkpoints e inicialização idempotente do schema. No diff do PR #7, `_init_schema` não foi reescrita; o schema executado por ela ganhou as tabelas de placeholder.
- Preservar sincronização de config em `prefs`, mas o PR #7 não adiciona `prefs_version` nem distingue intenção explícita. A migração nova deverá tratar valores iguais aos antigos defaults como herdados, conforme aprovação do usuário, mesmo que isso também migre uma escolha explícita que coincidia com o default antigo.
- `core._code_delivery_hint` não foi alterada pelo PR #7; deixá-la intocada.
- Preservar strings PT/EN de ordem/falha, `tests/fake_telegram.py` e `tests/test_retry_parcial.py`.
- Deixar fora do padrão, sem apagar nesta etapa, `_send_reference*`, reservas/recuperação de placeholders, `_tool_recover` e tabelas `placeholders*`.
- O código usa `order.violation`; não foi encontrada chave dedicada `order.blocked`. A parada atual é comunicada pelo resultado/erro do fluxo e deve ser revista ao blindar a ordem.

## Plano aprovado

1. Registrar esta auditoria como commit documental.
2. Em commit separado, defaults de upload/abort/sem placeholder, limite de workers e migração única de prefs versão 2, com testes de regressão.
3. Em commit separado, blindar e testar ordem por tópico, retries e retomada; falha terminal bloqueia o restante.
4. Implementar otimizações do plano uma por commit, cada uma com teste e benchmark antes/depois.
5. Implementar verificação somente leitura de fidelidade; manter as diferenças inevitáveis documentadas e não prometer igualdade de metadados que o Telegram não permite preservar.
