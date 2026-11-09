# Pesquisa comparativa — Telegram, transferência e retomada

**Consulta:** 2026-11-08 (UTC). **Amostra:** 33 repositórios consultados, além de cinco tópicos do GitHub. Não é um ranking de velocidade: os projetos têm objetivos diferentes (clone, backup, bridge, biblioteca e transporte).

## 1. Método e limites da evidência

- Metadados, licença, árvore, README, trechos de código e issues abertas/fechadas foram consultados pela API do GitHub. O recorte reproduzível está em `docs/evidencias/pesquisa-github.json`; a ferramenta de coleta é `tools/pesquisar_github.py`. Os downloads integrais ficaram em `data/pesquisa/`, fora do Git.
- A primeira coleta encontrou limite secundário da API; outra tentativa usou uma busca com operadores demais (HTTP 422). Ambas foram interrompidas. A coleta final serializou consultas, respeitou intervalos e limitou operadores. Não se interpretou erro de acesso como ausência de funcionalidade.
- `atividade` abaixo é `pushed_at`, não data do último commit útil nem garantia de manutenção. A árvore e seu SHA identificam o recorte; o branch pode avançar depois da consulta.
- Foram buscados `flood`, `duplicate`, `resume`, `album`, `Windows`, `FileReference`, `memory`, `forum`, `crash` e `slow`; as issues recentes também foram filtradas por `order`, `ban` e `topic`. A amostra não é exaustiva. Uma issue fechada documenta um modo de falha observado, **não prova que a versão atual ainda o tenha**.
- `?` significa **não verificado no recorte**, não “não existe”. A contagem de testes é de caminhos de teste encontrados na árvore (amostra limitada a 40), não testes executados. Não executei as suítes desses 33 projetos.
- GitLab/Codeberg não foram consultados: não estão entre os hosts liberados neste ambiente. Nenhuma conclusão depende deles.
- `core.telegram.org` retornou 403 na leitura direta. Trechos indexados da documentação oficial foram acessíveis pela busca e estão ligados abaixo; a página completa não foi verificada. A implementação instalada de Telethon 1.45.0 foi inspecionada localmente.
- Nenhum código de terceiros foi incorporado. Ideias foram reimplementadas. Em particular, não se deve confundir a licença MIT de um helper independente com a licença da ideia/original nem com AGPL/GPL de outros projetos.

### Correções de hipóteses sobre os nomes

`erfnzdeh/Telegram-CLI` redirecionou para `tlgrcli/tlgr`; `KurimuzonAkuma/pyrogram` para `kurigram-org/kurigram`; `expectocode/telegram-export` para `tnjd/telegram-export`. `mautrix/telegram` retornou linguagem principal **Go** e código `pkg/connector`, não o antigo bridge Python que eu esperava: a comparação identifica o snapshot atual, não presume sua história. `painor/FastTelethon`, `telegram-upload/telegram-upload`, `bipinkrish/Forward-Client`, `SubhradeepSS/Telegram-Channel-Cloner` e `colinshark/Telegram-Channel-Mirror` não foram encontrados nesses nomes. Foram usados substitutos verificáveis: `MiyukiKun/FastTelethonhelper`, `Nekmo/telegram-upload` e `AbirHasan2005/Forward-Client`; não inventei atividade dos dois últimos nomes de clonadores.

`fabianonline/telegram_backup` está **arquivado desde 2020**, embora a descrição inicial falasse em Java: a árvore atual contém Kotlin e um teste Java. `tg-archive` não está arquivado no GitHub, mas seu README declara manutenção limitada. `alfem/telegram-download-daemon` tem licença identificada pela API como `NOASSERTION`; o texto baixado contém GPL-3.0 e um cabeçalho de outro projeto. Tratar como licença a esclarecer, não como permissiva. `niubility6/telegram-backup` não declarou licença no recorte.

## 2. Tabela comparativa

Abreviações: **WF** = watermark/último ID; **DB** = banco persistente; **lib** = comportamento delegado à biblioteca; **snapshot** = somente o que foi lido. Ausência de prova nunca foi usada como prova de ausência.

| Projeto | Linguagem / licença | Push UTC | Ordem | Retomada | Flood | Dedup | Álbum / fórum | Arquivos / memória | Testes na árvore | Limite principal |
|---|---|---|---|---|---|---|---|---|---|---|
| [aahnik/tgcf](https://github.com/aahnik/tgcf) | Python / MIT | 2024-05-06 | reverse=True + offset | WF em configuração após envio | sleep no catch; sem retry explícito do item | WF, não conteúdo | ? / issues abertas | delegado à lib | flood pode avançar o iterador; #486/#432 | 0 caminhos |
| [iyear/tdl](https://github.com/iyear/tdl) | Go / AGPL-3.0 | 2026-11-06 | asc/desc; opção agrupado | progresso persistente; issue #1327 | ? | ? | detecta grupos / flag topic | CLI de arquivos; limite não medido | clone não é backup fiel completo; falha histórica de progresso concorrente | 20 caminhos |
| [knadh/tg-archive](https://github.com/knadh/tg-archive) | Python / MIT | 2026-03-01 | lotes reverse=True | SQLite incremental | threshold da lib; catch de leitura | ? | ? / issue #133 | mídia local; memória não medida | README declara manutenção limitada; #97/#146 | 0 caminhos |
| [fabianonline/telegram_backup](https://github.com/fabianonline/telegram_backup) | Kotlin / GPL-3.0 | 2020-04-27 (arquivado) | histórico API; não auditado fim a fim | SQLite incremental (README) | obeyFloodWait | ? | ? / ? | mídia em arquivos | arquivado em 2020; #84/#133 | 0 caminhos |
| [alfem/telegram-download-daemon](https://github.com/alfem/telegram-download-daemon) | Python / a esclarecer | 2023-06-21 | fila; não provada ordem estrita | estado de downloads no código | ? | nomes/IDs de download | ? / ? | daemon local | licença a esclarecer; #56/#61 | 0 caminhos |
| [klept0/Telegram-Forwarder-Bot-v2](https://github.com/klept0/Telegram-Forwarder-Bot-v2) | Python / MIT | 2026-11-01 | fila e ordem original declarada | JSON por origem/filtro/data | ? | WF por escopo | álbum sim / ? | não medido | watermark não reconcilia efeito remoto ambíguo | 9 caminhos |
| [tlgrcli/tlgr](https://github.com/tlgrcli/tlgr) | Python / MIT | 2026-10-28 | issue #49: ID não é data | ? | ? | ? | ? / ? | cliente/daemon | não é clonador completo; ordenação por data reportada incorreta | ≥40 caminhos |
| [Illgot/Telegram-CLI](https://github.com/Illgot/Telegram-CLI) | ? / MIT | 2026-02-04 | lotes de forward | progresso no código | sleep longo + retry | checkpoint | ? / ? | delegado à lib | recursão de retry e ausência de testes amostrados | 0 caminhos |
| [tdlib/td](https://github.com/tdlib/td) | C++ / BSL-1.0 | 2026-11-08 | referência de protocolo | estado local de arquivo | referência de protocolo | identidade de arquivo | API de grupos / API de tópicos | gerenciador de arquivos | biblioteca, não clone pronto; não comparar velocidade diretamente | 24 caminhos |
| [LonamiWebs/Telethon](https://github.com/LonamiWebs/Telethon) | Python / MIT | 2026-02-21 (arquivado) | iteradores/paginação | não é checkpoint de clone | threshold/retries da lib | não é motor de dedup | álbum alto nível / reply_to | partes/streaming | dependência fixada; APIs internas exigem regressão | 20 caminhos |
| [pyrogram/pyrogram](https://github.com/pyrogram/pyrogram) | Python / LGPL-3.0 | 2024-12-23 (arquivado) | iteradores/paginação | não é checkpoint de clone | tratamento na lib | não é motor de dedup | álbum / tópicos na API | streaming | biblioteca; não avaliar clone sem aplicação | 6 caminhos |
| [kurigram-org/kurigram](https://github.com/kurigram-org/kurigram) | Python / LGPL-3.0 | 2026-11-08 | iteradores/paginação | não é checkpoint de clone | tratamento na lib | não é motor de dedup | API de mídia / tópicos | streaming | fork de biblioteca; snapshot redirecionado | ≥40 caminhos |
| [mautrix/telegram](https://github.com/mautrix/telegram) | Go / AGPL-3.0 | 2026-11-08 | bridge; issue histórica #484 | estado de bridge | issue histórica #1079 | hash/eventos no código | grupos / portais de tópicos | reupload/streaming | objetivo Matrix, não clone Telegram→Telegram; AGPL | ≥40 caminhos |
| [MiyukiKun/FastTelethonhelper](https://github.com/MiyukiKun/FastTelethonhelper) | Python / MIT | 2022-02-04 | não é sender de clone | não é checkpoint de clone | ? | não se aplica | ? / ? | partes paralelas | helper MIT; não confundir com licença de outros FastTelethon | 0 caminhos |
| [Dineshkarthik/telegram_media_downloader](https://github.com/Dineshkarthik/telegram_media_downloader) | Python / MIT | 2026-04-27 | download sequencial no recorte | IDs/estado persistente | ? | pular já baixados | ? / ? | mídia local | backup/download, não publicação ordenada | 12 caminhos |
| [Nekmo/telegram-upload](https://github.com/Nekmo/telegram-upload) | Python / MIT | 2024-06-18 | ordenação de arquivos locais | ? | tratamento no recorte | ? | agrupamento / ? | upload de arquivos grandes | upload local→Telegram, não clone de histórico | 17 caminhos |
| [tnjd/telegram-export](https://github.com/tnjd/telegram-export) | Python / MPL-2.0 | 2019-10-27 (arquivado) | exportação por contexto | SQLite | ? | ? | ? / ? | exportação local | redirecionado; projeto antigo, não clonador | 1 caminhos |
| [danog/MadelineProto](https://github.com/danog/MadelineProto) | PHP / AGPL-3.0 | 2026-11-08 | biblioteca RPC | estado de cliente | tratamento de RPC | não é dedup de clone | álbuns / fóruns | streaming | biblioteca PHP AGPL; não importar código | ≥40 caminhos |
| [AbirHasan2005/Forward-Client](https://github.com/AbirHasan2005/Forward-Client) | Python / MIT | 2023-03-15 | forward de histórico | ? | catch/sleep no recorte | ? | ? / ? | delegado à lib | bot de encaminhamento; não auditado integralmente | 0 caminhos |
| [EverythingSuckz/TG-FileStreamBot](https://github.com/EverythingSuckz/TG-FileStreamBot) | Go / AGPL-3.0 | 2026-03-07 | servir mídia, não clonar | não é checkpoint de clone | issue #87 | ? | ? / ? | streaming HTTP | objetivo diferente; snapshot atual em Go | 0 caminhos |
| [rfsbraz/telegram-downloader](https://github.com/rfsbraz/telegram-downloader) | Python / MIT | 2026-11-06 | downloads sequenciais | IDs + .partial | prazo global no código | deduplicador local | ? / seleção de tópicos | streaming, arquivo atômico | issue #83 ilustra flood de autorização; não é clone fiel | ≥40 caminhos |
| [smilekevin/TGPorter](https://github.com/smilekevin/TGPorter) | Python / MIT | 2026-10-31 | ranges por offset | checkpoints no recorte | cooldown no recorte | estado de tarefas | ? / ? | partes paralelas | não executei sua suíte nem medi sua recuperação | ≥40 caminhos |
| [odysseusmax/tg-index](https://github.com/odysseusmax/tg-index) | Python / GPL-3.0 | 2023-01-24 | índice HTTP | ? | ? | ? | ? / ? | streaming | índice não é cópia ordenada | 0 caminhos |
| [nktknshn/tgmount](https://github.com/nktknshn/tgmount) | Python / Apache-2.0 | 2024-07-07 | filesystem virtual | ? | ? | ? | ? / ? | leituras parciais | #6 bloqueio após horas; não é clonador | 0 caminhos |
| [niubility6/telegram-backup](https://github.com/niubility6/telegram-backup) | ? / não declarada | 2022-09-05 | derivado de tg-archive | incremental (README) | herdado/lib | ? | ? / ? | site/arquivos locais | sem licença declarada; não reutilizar código | 0 caminhos |
| [GeiserX/Telegram-Archive](https://github.com/GeiserX/Telegram-Archive) | Python / GPL-3.0 | 2026-11-07 | arquivo incremental | SQLite/PostgreSQL | orçamento único no recorte | identidades/hashes | mídia / tópicos | chunks limitados em memória | GPL; #232/#295 mostram falhas reais, não corrigidas por declaração | ≥40 caminhos |
| [botnick/telegram-media-downloader](https://github.com/botnick/telegram-media-downloader) | Go / MIT | 2026-11-07 | download | progresso no recorte | tratamento no recorte | hash/estado no recorte | ? / ? | streaming Go | #111 limita comparação de throughput; não medir aqui | ≥40 caminhos |
| [Normalizex/telegram-backup](https://github.com/Normalizex/telegram-backup) | Python / MIT | 2022-07-14 | script de backup | ? | ? | ? | ? / ? | arquivos locais | recorte pequeno; nenhuma garantia de clone | 0 caminhos |
| [renkagod/tg-chat-dump](https://github.com/renkagod/tg-chat-dump) | Python / MIT | 2026-11-01 | dump de histórico | estado no recorte | ? | IDs no recorte | ? / tópicos | JSON/mídia | dump local, não publicação | 3 caminhos |
| [warl0ckwraith/telegram-migrator](https://github.com/warl0ckwraith/telegram-migrator) | Python / MIT | 2026-09-06 | migração de histórico | checkpoint no recorte | retry/sleep no recorte | estado no recorte | ? / ? | não medido | poucos testes amostrados; não confiar emalegação de zero perda | 2 caminhos |
| [xsukax/xsukax-Telegram-Backup](https://github.com/xsukax/xsukax-Telegram-Backup) | Python / GPL-3.0 | 2025-10-12 | backup | progresso no recorte | ? | ? | ? / ? | arquivos locais | GPL; sem testes amostrados | 0 caminhos |
| [plzhans/telegram-chat-exporter](https://github.com/plzhans/telegram-chat-exporter) | TypeScript / AGPL-3.0 | 2026-11-03 | exportação | ? | ? | ? | ? / ? | exportação TypeScript | AGPL; objetivo diferente de clone | 0 caminhos |
| [q909717714/TGArchiveManager](https://github.com/q909717714/TGArchiveManager) | Python / MIT | 2026-08-11 | gerenciador de arquivo | progresso no recorte | ? | estado/hashes no recorte | ? / ? | mídia local | não executei sua suíte; não comparar velocidade | 5 caminhos |

## 3. O que o TeleCopy já apresenta de forte — evidência local

1. Um sender espera `ready[index]`; workers só preparam. O cenário de curso com dois tópicos, álbuns e preparação fora de ordem já existe em `tests/test_ordem_curso.py`. Isso é mais específico que a simples alegação de “ordem cronológica” de vários READMEs.
2. Checkpoints por par origem/destino e continuações parciais permitem não repetir uma mídia já aceita quando falta continuação. Há testes de retry parcial e ordem por tópico.
3. Cópia de fórum desativa dedup de conteúdo e assinatura: material repetido pode ser legítimo. O teste de aula repetida continua sendo um requisito, não uma otimização a remover.
4. O transporte rápido já separa preparação de envio, escreve por offset, limita leases e possui fallback. A pesquisa valida a direção geral, mas **não valida os bugs de unidades e flood encontrados nesta intervenção**.
5. O modo padrão não precisa baixar mídia quando uma referência é permitida e escolhida. Essa opção não foi alterada para perseguir velocidade.

Esses são diferenciais **observados no código e nos testes locais**, não uma alegação de superioridade universal. Um backup com arquivo offline ou uma bridge com banco transacional pode ser mais adequada para outro problema.

## 4. Ideias a adotar, reimplementadas do zero

| Ideia e fonte | Decisão local | Trade-off |
|---|---|---|
| Prazo compartilhado de flood, `rfsbraz/telegram-downloader` (MIT) | Um portão no pool, comum a todas as partes; líder registra o episódio | Coalescer reduz tempestade, mas não remove o limite de vazão da conta |
| Um único orçamento de flood, `GeiserX/Telegram-Archive` (GPL-3.0) | Registrar antes de esperar; não aplicar outra penalidade no encaminhamento da exceção | Esgotar o orçamento causa fallback, não ignorar a espera |
| Escrita por offset e verificação de cobertura, documentação oficial e transportes consultados | Manter 512 KiB, validar tamanho e remontagem; testes SHA-256 | Hash local não prova identidade de uma mensagem já publicada |
| Estado incremental em DB, `tg-archive` (MIT), `telegram_backup` (GPL-3.0) | Manter SQLite; adicionar intenção antes de efeitos remotos na auditoria | Janela de incerteza exige reconciliação, não “DONE antecipado” |
| Retomada por escopo, `klept0/...` (MIT) | Não usar somente origem como chave: manter origem, destino e tópico | Mais estado, menos falsos “já concluídos” |
| Validação após transporte, `rfsbraz` e `TGPorter` (MIT) | Não aceitar arquivo vazio/curto; preservar causa da falha | Um fallback pode precisar recomeçar bytes, nunca marcar envio antes da confirmação |
| Licenciamento explícito, todos os projetos | Registrar fonte da ideia e licença; nenhuma cópia de implementação | AGPL/GPL não são incompatíveis com estudar ideias, mas não autorizam copiar sem cumprir a licença |

**Não adotei** comentários de código como especificação: um downloader GPL consultado descreve limite de 512 KiB por requisição, enquanto o trecho indexado da documentação oficial atual informa até 1 MiB, com divisibilidade e alinhamento. O projeto continua em 512 KiB por compatibilidade com Telethon 1.45.0; não há alegação de limite universal baseada naquele comentário.

## 5. Modos de falha minerados das issues → regressão

| Evidência | Modo de falha | Teste/decisão |
|---|---|---|
| [tgcf #371](https://github.com/aahnik/tgcf/issues/371), [#605](https://github.com/aahnik/tgcf/issues/605) | Flood pode deixar mensagem para trás | `test_ordem_curso.py`; novo teste de orçamento/fallback íntegro. No código consultado, o tratamento externo de flood não reexecuta explicitamente a mensagem que falhou |
| [tgcf #486](https://github.com/aahnik/tgcf/issues/486), [#603](https://github.com/aahnik/tgcf/issues/603) | Duplicação em retomada/encaminhamento | Investigação da janela envio→checkpoint; teste de queda nesse ponto, não só antes do envio |
| [tgcf #432](https://github.com/aahnik/tgcf/issues/432), [mautrix #484](https://github.com/mautrix/telegram/issues/484) | Ordem de mensagens/álbum invertida | Sender por índice e `test_curso_forum_sai_identico_a_origem_com_turbo` |
| [tdl #1327](https://github.com/iyear/tdl/issues/1327) | Progresso compartilhado mutado durante interrupção | Serialização de checkpoints e teste de cancelamento/queda; não copiar o modelo Go |
| [tdl #1158](https://github.com/iyear/tdl/issues/1158), [tgcf #637](https://github.com/aahnik/tgcf/issues/637) | Tópico de fórum ignorado | Dois tópicos e retorno ao primeiro em `test_ordem_curso.py` |
| [tg-archive #97](https://github.com/knadh/tg-archive/issues/97), [telegram_backup #133](https://github.com/fabianonline/telegram_backup/issues/133) | Espera interminável/sem progresso visível | Estado explícito no painel e orçamento por bloco; waits longos não são ignorados |
| [telegram_backup #84](https://github.com/fabianonline/telegram_backup/issues/84) | Flood de mídia derruba a execução | Premium reduz capacidade, espera portão e recupera gradualmente |
| [alfem #56](https://github.com/alfem/telegram-download-daemon/issues/56) | Referência expirada durante download | `test_download_file_migrate_e_refresh_de_referencia`; política explícita para referência inválida |
| [alfem #61](https://github.com/alfem/telegram-download-daemon/issues/61), [#55](https://github.com/alfem/telegram-download-daemon/issues/55) | Caracteres especiais e nomes iguais | Sonda de nome com CR/LF; temporários precisam de identidade única, nome original preservado no upload |
| [rfsbraz #83](https://github.com/rfsbraz/telegram-downloader/issues/83) | Flood de `ExportAuthorization` confundido com referência expirada | Não embrulhar FloodError como indisponibilidade de sender; teste de exportação com flood |
| [rfsbraz #78](https://github.com/rfsbraz/telegram-downloader/issues/78), [mautrix #292](https://github.com/mautrix/telegram/issues/292) | Sessão expirada/duplicada vira loop de retries | Política terminal de autenticação; não trocar contas para contornar limite |
| [GeiserX #232](https://github.com/GeiserX/Telegram-Archive/issues/232) | Flood no meio do arquivo reinicia do byte zero | Retry por parte; fallback apenas com orçamento esgotado, com aviso e integridade |
| [GeiserX #295](https://github.com/GeiserX/Telegram-Archive/issues/295) | Retry reinicia paginação inteira | Manter cursor de coleta; não transformar flood em novo scan completo |
| [GeiserX #280](https://github.com/GeiserX/Telegram-Archive/issues/280), [telegram_backup #123](https://github.com/fabianonline/telegram_backup/issues/123) | Nome de arquivo inválido no Windows | Sanitize somente temporário; preservar nome lógico original |
| [tdl #1217](https://github.com/iyear/tdl/issues/1217), [mautrix #380](https://github.com/mautrix/telegram/issues/380) | Telemetria/log idêntico parece travamento | Um aviso por episódio, contador de partes, detalhes no arquivo |
| [tgmount #6](https://github.com/nktknshn/tgmount/issues/6), [tdl #850](https://github.com/iyear/tdl/issues/850) | Bloqueio/atividade após horas | Teste de cancelamento e soak simulado; soak não substitui teste real de 12 horas |
| [tg-archive #146](https://github.com/knadh/tg-archive/issues/146), [#75](https://github.com/knadh/tg-archive/issues/75) | Banco locked/migração repetida | WAL, busy timeout e migração versionada com upgrade/rollback |

Os testes novos desta tabela são identificados pelo cenário; o relatório final distingue os já executados dos ainda pendentes. Não conto uma issue lida como teste aprovado.

## 6. Protocolo e controle de concorrência

Referências oficiais acessíveis por trechos indexados:

- Partes de upload, alinhamento, limite de download e atualização de referências: [1](https://core.telegram.org/api/files). O máximo de partes vem de configuração da conta; não fixar 2 GB/4 GB como verdade permanente.
- `FLOOD_WAIT` e `FLOOD_PREMIUM_WAIT` são erros 420 com espera obrigatória: [1](https://core.telegram.org/api/errors). O nome do erro não permite concluir que a conta do relato não é Premium.
- `FLOOD_PREMIUM_WAIT` também é associado a limite de vazão; a documentação pede repetir após o prazo e limitar avisos: [1](https://core.telegram.org/api/files). Reduzir conexões é uma escolha conservadora de controle de carga, **não prova de aumento de velocidade**.
- Fórum usa `top_msg_id`, com exceção do tópico Geral: [1](https://core.telegram.org/api/forum).
- Máximo de dez mídias por grupo e `messages.uploadMedia` antes de `sendMultiMedia`: [2](https://core.telegram.org/api/files).
- Limites de legenda dependem da configuração da conta: [1](https://core.telegram.org/api/config). Os valores 1024/4096 e 2048 Premium são hipóteses usuais, não constantes universais a impor sem consultar a conta.

Padrões estudados: AIMD (aumento aditivo, corte multiplicativo), token bucket para vazão, circuit breaker para dependência indisponível, full jitter para evitar sincronização e idempotência para efeitos colaterais. A visão da AWS sobre orçamento de retries e idempotência foi acessível em [2](https://d1.awsstatic.com/builderslibrary/pdfs/timeouts-retries-and-backoff-with-jitter.pdf). Não se implementa um circuito que “pula” mensagens: aqui o portão segura a preparação, enquanto o sender continua esperando o índice correto.

## 7. Decisões rejeitadas

- **Rodízio de contas/proxies/sessões para fugir de flood ou ban:** rejeitado por conformidade.
- **Contornar `noforwards`:** rejeitado. Vários projetos oferecem isso; não é uma funcionalidade a copiar. Exigir confirmação de propriedade/autorização e manter recusa padrão onde a API não permite.
- **Enviar assim que uma parte/arquivo fica pronto:** rejeitado; fere ordem estrita.
- **Marcar concluído após pré-upload:** rejeitado; upload de bytes não é mensagem publicada.
- **Dedup por conteúdo como padrão em fórum:** rejeitado; aulas repetidas são conteúdo legítimo.
- **Trocar Telethon ou importar código AGPL/GPL:** fora da stack/limites de licença.
- **Persistir só capacidade máxima de hardware:** rejeitado; histórico de throttle por conta/DC deve informar o próximo início.
- **Prometer exatamente uma publicação sem reconciliação:** rejeitado; rede e processo podem falhar em lados distintos de uma confirmação. Intenção + consulta ao destino + bloqueio de ambiguidade é mais honesto.

## 8. Dez melhorias adicionais descobertas

Fora dos itens já enumerados na missão, proponho:

| Melhoria | Prioridade | Esforço | Risco |
|---|---|---|---|
| 1. Prazo de flood persistido para não esquecer uma espera ao reiniciar | P0 | médio | baixo |
| 2. Manifesto de posse de temporários com PID/boot, evitando apagar arquivo de outro processo | P0 | médio | médio em Windows |
| 3. Identificador de correlação por episódio/item no log detalhado | P0 | baixo | baixo |
| 4. Snapshot com `max_id` explícito para separar backlog de mensagens novas | P0 | médio | médio; requer teste de canal crescente |
| 5. Validador de invariantes `DONE ⇒ ID remoto`, sem reparação automática | P0 | baixo | baixo |
| 6. Detector de mudança de identidade/tamanho da mídia durante refresh | P0 | baixo | baixo |
| 7. Watchdog de disco antes de admitir novo item, além do orçamento inicial | P0 | médio | baixo |
| 8. Coalescer notificações de progresso por tarefa, mantendo máxima monotônica | P0 | baixo | baixo |
| 9. Lock com fencing token para impedir dois clones no mesmo par | P0 | alto | alto se integrado a processos remotos |
| 10. Matriz de seeds de scheduling nos testes de ordem, não só um atraso fixo | P0 | baixo | baixo |
| 11. Normalização/validação de entidades antes da divisão, sem descartar tipos desconhecidos | P1 | médio | médio |
| 12. Relatório com versão de schema e comando de reprodução da verificação | P1 | baixo | baixo |

Prioridade não significa implementação autorizada nesta entrega. As escolhas de menor risco são correlação, validadores e testes de seeds; mudanças de concorrência entre processos exigem projeto próprio.

## 9. Reprodução da pesquisa

Com `gh` autenticado e acesso ao GitHub:

```bash
python tools/pesquisar_github.py
```

A ferramenta consulta apenas dados públicos, não importa código pesquisado para o programa e não envia dados do usuário. O JSON registra URLs, datas, licenças, caminhos e trechos. Para auditoria independente, abra os links das issues e os blobs da árvore registrada; não considere a tabela um benchmark.
