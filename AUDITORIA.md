# Auditoria final WP1/WP2 — PR #7

Auditado em `142ad94`; correções entregues até `f57f469`.

**Veredito da auditoria: NÃO estava tudo ok.** A suíte estava verde (161 passed),
mas não cobria os caminhos onde o contrato de ordem quebrava. Foram confirmados
**7 defeitos por execução**, não por leitura. Dois eram críticos.

A afirmação anterior de "WP1/WP2 concluído" estava errada nesses pontos: o README
e o corpo do PR diziam "transitório nunca avança" e "DONE só com ID confirmado", e
ambos eram falsificados abaixo.

**Estado atual: os 7 corrigidos**, com testes de regressão. Suíte **179 passed**,
sondas **11/11 passed**, pyflakes sem achados novos. Ver §6 para o fechamento.

---

## 1. Defeitos confirmados

| # | Severidade | Achado | Evidência executada | Regressão? |
|---|---|---|---|---|
| D1 | **Crítico** | `_send` reenvia itens **já confirmados** quando o sender retenta | PROBE1: 2 envios para 1 msg (ids 1001, 1002) · PROBE2: 22 envios para 12 msgs · PROBE7: caminho upload também | Sim — loop de retry do WP2 |
| D2 | Alto | Limite diário subcontado quando D1 ocorre | PROBE8: 3 envios reais, 1 contado | Sim, consequência de D1 |
| D3 | Alto | Modo **forward** não tem barreira WP2: falha sem marcador e segue | PROBE3: msg2 falha → encaminhou `[1, 3, 4]`, 0 marcadores | Contrato WP2 nunca foi ligado ao `_forward_loop` |
| D4 | Alto | Forward grava **DONE com `dest_msg_id NULL`** | PROBE4b: `status=done, dest_msg_id=None` | Pré-existente, mas viola invariante que WP2 afirma garantir |
| D5 | Alto | Preflight só olha mensagens **coletadas**: filtro esconde pendência anterior | PROBE6: msg2 ERROR sem marcador + filtro `videos` → msg3 entregue | Sim — preflight novo incompleto |
| D6 | Médio | Verificador `[5] → 7` dá **falso positivo** | PROBE5: pendência no fim da sequência reportada como violação | Sim — verificador novo |
| D7 | Médio | `delivery="forward"` é **ignorado** pelo motor | PROBE9: `forwarded=0`, fez cópia | Sim — campo novo |

### D1 — detalhe (o mais grave)

`copier.py`, `_send`: o laço `while index < len(plan)` decide o que enviar
olhando só `failed` / `native` / `path` / `reference`. Ele **nunca consulta
`entry["dest_msg_id"]`**, embora o resto do método consulte (o `except` evita
reservar quem já tem ID, e o registro final usa
`sent_ids.get(msg.id) or entry.get("dest_msg_id")`).

Consequência: qualquer falha transitória **depois** de um envio confirmado dentro
do mesmo item faz o retry reenviar o que já foi aceito pelo Telegram.

Caminho reproduzido: vídeo com legenda > 1024 unidades. A mídia entra (id 1001), a
continuação de texto falha 1× (`OSError`), o sender retenta, e a mídia entra de
novo (id 1002). O destino fica com **duas cópias** e a sequência de `msg_id` por
`dest_msg_id` deixa de ser estritamente crescente. Em álbum de 12 com falha no 2º
bloco, o primeiro bloco de 10 é reenviado inteiro.

Correção proposta: no início de cada iteração, se `entry.get("dest_msg_id")`,
reaproveitar o ID (`sent_ids[msg.id] = ...`) e avançar sem reenviar; para grupos,
enviar apenas os entries sem ID confirmado, preservando a ordem do segmento.

### D3/D4 — modo forward

`_forward_loop` é o único caminho de entrega que não passou pelo WP2: não usa
`max_retries`, não reserva marcador, não aplica `on_failure` e segue para o
próximo item após `_mark_failed`. Além disso `_forward_item` pode devolver
`[None] * len(ids)` (resposta não parseável) e `_mark_done` aceita
`dest_msg_id=None`, gravando DONE sem confirmação.

Isso é uma **decisão de projeto**, não só um bug: ou o forward ganha o contrato
completo, ou deixa de ser oferecido quando a ordem estrita é exigida.

### D5 — preflight

`_preflight_order(messages, ...)` itera apenas sobre as mensagens coletadas. Um
item que falhou sem marcador e foi excluído por filtro numa reexecução fica
invisível, e o item seguinte é entregue — exatamente o buraco que o preflight
deveria impedir. Correção: consultar as pendências do banco por tópico
(`order_pending_before`) independentemente do filtro ativo.

### D6 — verificador

`Repository.order_rows` ordena por `dest_topic, dest_msg_id`; no SQLite **NULL
ordena primeiro**. Um item não entregue no fim da sequência (estado normal depois
de uma falha com `[h]` desligado) aparece antes dos resolvidos e o `_tool_order`
interpreta como "resolvido após não resolvido". Correção: ordenar colocando NULL
por último (`ORDER BY dest_topic, dest_msg_id IS NULL, dest_msg_id, part_index`)
e tratar a pendência final como pendência, não violação.

### D7 — duas fontes de verdade

`run()` decide com `self.cfg.forward_mode`; `cfg.delivery` só é consultado em
`_prepare` para `== "reference"`. A UI mantém os dois em sincronia, então o bug
não aparece pelo menu, mas `RunConfig(delivery="forward")` (prefs, uso
programático, testes) copia em vez de encaminhar. Correção: derivar `forward` de
`delivery` como fonte única, mantendo `forward_mode` como compatibilidade.

---

## 2. O que está de fato ok (verificado)

- **Suíte**: 161 passed, isolada, sem rede.
- **Pyflakes**: nenhum achado novo. `copier.py:308 split_text` e
  `security.py:68 exc` foram confirmados **no baseline** `4e7994f`.
- **Schema**: banco criado com o schema antigo abre com o código novo, cria
  `placeholder_parts`, **preserva os dados**, e abrir 2× é idempotente.
  `reset_all` zera `placeholders` e `placeholder_parts`. Sem DROP destrutivo.
- **Ordem no caminho feliz**: o sender é um laço único por índice; os workers só
  preparam. Não há `as_completed` nem envio por ordem de conclusão.
- **Cancelamento**: `try/finally` cancelando os downloaders preservado.
- **Slots pré-reservados no limite diário**: PROBE10 — marcador + 2 slots = 3
  envios, 3 contados.
- **Recuperação**: edita marcador e slots; se falta slot, retorna `failed` sem
  criar mensagem nova.
- **Intocados**: `main.py`, `security.py`, `channel_clone.py`, `dedup.py`,
  `filters.py`, `requirements*.txt`, `.gitignore` — `git diff` vazio.
  `data/` intocado. Zero dependências novas.
- **Sem novos `except Exception: pass`**: os 3 novos blocos logam/tratam.

## 3. Nota de classificação

Continuações de legenda no **caminho normal** não entram no limite diário
(2 envios, 1 contado). Verificado no baseline `4e7994f`: comportamento
**idêntico**. É pré-existente, não regressão — mas vale decidir se entra no
escopo, já que os slots de marcador contam.

## 4. Reproduzir

As sondas estão em `audit/` (não commitadas, fora da suíte para não quebrar o
`pytest -q` verde). Cada teste asserts o comportamento **exigido**; falha =
defeito real.

```bash
WORK=$(mktemp -d); git ls-files -z | tar --null -T - -cf - | tar -xf - -C "$WORK"
cp audit/probes_1.py "$WORK/tests/test_audit_probe.py"
cp audit/probes_2.py "$WORK/tests/test_audit_probe2.py"
cd "$WORK" && python -m pytest -p no:cacheprovider -o addopts='' -q \
  tests/test_audit_probe.py tests/test_audit_probe2.py -s
```

Resultado na auditoria: **8 failed, 3 passed** (PROBE4 original era inconclusivo;
PROBE4b é a versão válida que confirmou D4). A PROBE3 foi reescrita para conferir o
invariante real (retentar na ordem **ou** reservar antes de seguir), porque a
assertiva original só aceitava a reserva e não previa a retentativa bem-sucedida.

Resultado após as correções: **11 passed**.

## 5. Impacto no que já foi publicado

README §6.2 e corpo do PR #7 afirmavam invariantes que D1/D3/D4/D5 quebravam. Ambos
foram atualizados junto com as correções.

## 6. Fechamento

| # | Correção | Commit | Regressão permanente |
|---|---|---|---|
| D1 | `_send` reaproveita `dest_msg_id` e reenvia só continuações/blocos faltantes | `62ccefa` | `tests/test_retry_parcial.py` (5 testes) |
| D2 | Cada envio confirmado, inclusive continuação, conta no diário | `62ccefa` | `test_cada_envio_confirmado_conta_no_limite_diario` |
| D3 | `_forward_loop` com retries, barreira posicional e `on_failure` | `19b56e1` | `tests/test_forward_ordem.py` |
| D4 | `_forward_ids_valid` + `_mark_done` recusa ID `None` | `19b56e1` | `test_forward_sem_id_confirmado_nao_vira_done` |
| D5 | Preflight considera pendências fora da coleta; mensagem apagada resolve como skip | `118a0fa` | `tests/test_ordem_preflight.py` (4 testes) |
| D6 | `order_rows` com NULL por último; verificador compara `msg_id`, não ordem de linha | `118a0fa` | `test_tool_order_pendencia_final_nao_e_violacao` |
| D7 | `delivery` e `forward_mode` sincronizados em `__post_init__` | `19b56e1` | `test_delivery_forward_e_honrado_pelo_motor` |

### Efeitos colaterais encontrados ao re-auditar as correções

- Reservar marcador sobre um forward com resposta ambígua criaria duplicata. Agora
  estado ambíguo **aborta sem reservar e sem reenviar** (`f57f469`,
  `test_forward_ambiguo_aborta_sem_reservar`).
- Parar cedo no forward não marcava `_stop`, então o resultado dizia "completo".
  Corrigido no mesmo commit.
- Contagem de álbuns duplicava quando a retentativa enviava o bloco restante;
  agora conta uma vez por item (`f57f469`).

### Testes que passaram a ser fiéis

`test_forward_em_forum_usa_o_topico_destino` passava **por causa** do D4: o fake
devolvia resposta `None` e o item virava DONE sem ID. O fake agora confirma um ID
por `random_id`, e `FakeClient.forward_messages` devolve IDs próprios e crescentes
por mensagem (antes repetia o mesmo ID para o lote inteiro).

### Números finais

- Suíte: **179 passed** (isolada, sem rede). Baseline pré-WP: 143.
- Sondas da auditoria: **11/11 passed**.
- Pyflakes: só `copier.py split_text` e `security.py exc`, ambos confirmados no
  baseline `4e7994f`.
- Benchmark fake remedido em `f57f469`: 10,144 s, 0 downloads, 100 envios por
  referência, 819200 B evitados, 100 concluídas / 0 erros. Tempo dominado pelo
  delay artificial — segue **não** sendo promessa de velocidade real.
- Nenhum teste com credenciais ou canal real foi executado.
