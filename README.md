# TELECOPY

```
 ████████╗███████╗██╗     ███████╗ ██████╗ ██████╗ ██████╗ ██╗   ██╗
    ██╔══╝██╔════╝██║     ██╔════╝██╔════╝██╔═══██╗██╔══██╗╚██╗ ██╔╝
    ██║   █████╗  ██║     █████╗  ██║     ██║   ██║██████╔╝  ╚████╔╝
    ██║   ██╔══╝  ██║     ██╔══╝  ██║     ██║   ██║██╔═══╝    ╚██╔╝
    ██║   ███████╗███████╗███████╗╚██████╗╚██████╔╝██║         ██║
    ╚═╝   ╚══════╝╚══════╝╚══════╝ ╚═════╝ ╚═════╝ ╚═╝         ╚═╝
```

**Replicador de canais Telegram — rápido, limpo e sem duplicatas.**
**Telegram channel replicator — fast, clean and duplicate-free.**

`v4.1.0` · Python 3.10+ · Windows / Linux / macOS

---

## Índice

1. [O que o programa faz](#1-o-que-o-programa-faz)
2. [Instalação rápida](#2-instalação-rápida)
3. [Primeira execução (passo a passo)](#3-primeira-execução-passo-a-passo)
4. [Os três modos de cópia](#4-os-três-modos-de-cópia)
5. [Menu completo](#5-menu-completo)
6. [Como retomar cópias interrompidas](#6-como-retomar-cópias-interrompidas)
7. [Deduplicação: como funciona](#7-deduplicação-como-funciona)
8. [Onde ficam seus dados](#8-onde-ficam-seus-dados)
9. [Solução de problemas](#9-solução-de-problemas)
10. [Testes automatizados](#10-testes-automatizados)
11. [Compilar o .exe e o instalador](#11-compilar-o-exe-e-o-instalador)
12. [Arquitetura do código](#12-arquitetura-do-código)
13. [Perguntas frequentes](#13-perguntas-frequentes)

---

## 1. O que o programa faz

O TELECOPY copia **mensagens de qualquer canal ou grupo do Telegram que você
consegue ler** para **qualquer canal ou grupo em que você é administrador**:
textos, fotos, vídeos, áudios, documentos, stickers e álbuns, com legendas e
formatação preservadas.

| Recurso | Detalhe |
|---|---|
| **Histórico completo** | Copia desde a primeira mensagem do canal |
| **Retomada exata** | Interrompeu? Ao voltar, continua de onde parou (sem repetir nada) |
| **Anti-duplicata** | 3 camadas (LRU → Bloom → SQLite) impedem o mesmo arquivo de ser enviado duas vezes |
| **Multi-destino** | Envia o mesmo conteúdo para vários canais em sequência |
| **Modo espelho** | Fica observando o canal e copia novas mensagens em tempo real |
| **Modo encaminhar** | Alternativa ultrarrápida (usa o encaminhamento nativo do Telegram) |
| **Álbuns** | Preserva grupos de mídia na mesma ordem, em uma única publicação |
| **Filtros** | Só fotos, só vídeos, só documentos, só links, recentes, originais… |
| **Anti-flood** | Respeita automaticamente o tempo de espera pedido pelo Telegram |
| **Limite diário** | Avisa e bloqueia antes de você arriscar a conta |
| **Fila de repetição** | Mensagens que falharam ficam guardadas para nova tentativa |
| **Segurança** | Credenciais cifradas com AES-256-GCM no seu computador |
| **Português / Inglês** | Interface bilíngue, trocada dentro do programa |
| **Backup e manutenção** | Backup do banco, limpeza de temporários, compactação |

---

## 2. Instalação rápida

### Pré-requisito: Python 3.10 ou mais novo

- **Windows:** baixe em <https://www.python.org/downloads/> e **marque a caixa
  “Add python.exe to PATH”** durante a instalação.
- **Linux:** `sudo apt install python3 python3-pip`
- **macOS:** `brew install python` (ou o instalador do site oficial)

### Instalar o TELECOPY

```bash
git clone https://github.com/montx2/telecopy.git
cd telecopy
python -m pip install -r requirements.txt
python main.py
```

Se preferir, use o executável pronto: dê **duplo clique em `Telecopy.exe`**
(veja a seção 11 para gerar o seu).

### Verificar se está tudo certo

```bash
python main.py --check     # ambiente, banco, hardware e acesso ao Telegram
python demo.py             # cópia de demonstração 100% offline
```

O `--check` mostra a versão do Python, a pasta de dados, a integridade do banco,
o hardware detectado e se os servidores do Telegram estão alcançáveis.

O `demo.py` executa uma cópia completa com um Telegram **simulado** — não pede
login, não acessa a internet e não altera seus dados. É a forma mais rápida de
confirmar que a instalação está funcionando.

---

## 3. Primeira execução (passo a passo)

O programa é um guia: ele mesmo explica cada etapa na tela.

### Passo 1 — Pegar as credenciais do Telegram

1. Abra <https://my.telegram.org/apps> e entre com **o número da sua conta**.
2. Clique em **“API development tools”**.
3. Preencha qualquer nome de aplicativo (ex.: `telecopy`) e confirme.
4. Copie dois valores:
   - **App api_id** — um número (ex.: `1234567`)
   - **App api_hash** — 32 caracteres (ex.: `0123456789abcdef0123456789abcdef`)

### Passo 2 — Informar as credenciais

Na tela “CONFIGURAÇÃO INICIAL”, cole o **API ID** e o **API Hash**.
Eles são gravados cifrados em `data/vault/credentials.enc` — nunca saem do
seu computador.

### Passo 3 — Fazer login (uma única vez)

1. Digite o telefone com DDI (pode usar espaços, hífen e parênteses): `+55 11 99999-9999`.
2. O TELECOPY informa o método escolhido pelo Telegram. O código pode chegar no chat oficial **“Telegram” em outro aparelho já conectado**, por SMS ou por outro método — ele não aparece nesta janela.
3. Digite o código. Se você usa verificação em duas etapas, informe a senha.

A sessão fica salva no diretório de dados do TELECOPY e, nas próximas vezes,
o programa deve abrir direto no menu sem pedir o código novamente.
**Não compartilhe seu API Hash, código de login nem senha 2FA.**

> **Requisitos para copiar:** ser **membro** do canal de origem e
> **administrador** do destino existente (com permissão de postar). Se o destino
> for criado automaticamente, a conta que executa o TELECOPY vira sua
> administradora; a origem continua exigindo acesso de membro.

---

## 4. Os três modos de cópia

### `[1] Copiar canal` — clonar o histórico

Copia as mensagens existentes da origem para o destino. Você pode escolher um
destino que já existe ou pedir ao TELECOPY para criar um novo automaticamente.

- **Reenviar** (padrão): baixa e publica novamente. O destino **não** mostra
  “Encaminhado de…”. Ideal para backups e para reconstruir um canal.
- **Encaminhar**: usa o encaminhamento nativo. É de 5 a 20 vezes mais rápido,
  mas o destino mostra o canal de origem.
- **Usar um destino existente**: se origem e destino forem fóruns e você
  pressionar ENTER nos dois prompts de tópico, o TELECOPY **recria toda a
  estrutura de tópicos**: confere o que já existe, reutiliza o que estiver
  associado, cria os tópicos ausentes e envia cada mensagem ao tópico
  equivalente, na ordem original. O resumo mostra quantos tópicos serão
  reaproveitados e quantos serão criados. Tópicos com título duplicado e sem
  ícone distintivo são recusados para evitar enviar material ao lugar errado.
  Se a cópia não ficar completa, opções como fechar/ocultar tópicos não são
  aplicadas, para que a retomada não fique bloqueada. Se você digitar um tópico
  do destino com a origem sendo um fórum inteiro, o programa avisa que **todas**
  as mensagens irão para esse tópico e pede confirmação — não há como recriar a
  estrutura nesse caso. Se o destino for um supergrupo sem tópicos e sua conta
  for administradora, o TELECOPY oferece ativar o modo fórum
  (`channels.ToggleForumRequest`) em vez de falhar.
- **Criar destino automaticamente**: cria um canal/grupo privado do tipo da
  origem, copia título e descrição quando disponíveis e, se a origem for um
  fórum, recria os tópicos (nomes, ícones de emoji/cor e opções de
  fixado/fechado/oculto) e envia cada mensagem ao tópico correspondente. O
  título começa igual ao da origem e pode ser alterado antes da criação.

Na cópia de fórum inteiro, o TELECOPY desativa a deduplicação por conteúdo e a
assinatura nesta execução: avisos repetidos em tópicos diferentes são conteúdo
legítimo, e uma assinatura alteraria o texto original. Respostas dentro dos
tópicos são religadas às cópias das mensagens respondidas quando o checkpoint
origem→destino está disponível. Ao final, o programa mostra uma conferência por
tópico (concluídas/esperadas) e só marca a execução como completa se não houver
falhas, itens não reproduzíveis ou mensagens faltantes.

A ordem dos tópicos é reconstruída como no original: como o Telegram ordena os
tópicos pela última atividade, o TELECOPY lê a lista da origem (do mais recente
para o mais antigo) e **cria os tópicos do menos ativo para o mais ativo**, para
que o resultado final fique com a mesma ordem. Tópicos fixados são reordenados
ao final por `ReorderPinnedForumTopics`. Se o Telegram recusar o emoji de um
tópico (o identificador do emoji pertence ao dono original), o tópico é criado
de novo só com a cor e, se ainda falhar, sem ícone — com um aviso no resumo, sem
abortar a clonagem.

Se você já tinha copiado esse par origem→destino com uma versão antiga, pode
haver checkpoints de uma execução em que tudo caiu no General. Ao preparar o
fórum, o TELECOPY avisa e oferece **zerar apenas os checkpoints desse par**;
sem isso, "retomar" pularia as mensagens que já estavam no General e elas nunca
chegariam ao tópico correto.

O Telegram não permite uma cópia 100% idêntica: o destino não recebe o username
público, membros nem permissões da origem (a foto de perfil só é copiada quando
o clone automático é criado e a opção está ligada); datas originais, autores, IDs,
reações, votos/resultado de enquetes e várias mensagens de serviço não podem ser
reproduzidos como no original. Botões de bot/callback, mídia paga, localização ao
vivo e dados animados não são recriados como se fossem o original; o que a API
não permite reproduzir é registrado como falha, não como sucesso. A cor de um
tópico já existente não é editável pela API; para reproduzir a cor, use um destino
novo. A ordem visual final continua sendo decidida pelo Telegram (última
atividade e fixados); o TELECOPY reproduz a ordem de atividade da origem e
recola os fixados, mas não pode fixar um tópico em uma posição arbitrária. O
destino automático fica privado e a conta que o criou já é administradora.
Confira o resumo antes de confirmar. Se uma cópia de fórum for interrompida,
escolha o mesmo destino, pressione ENTER para todos os tópicos e mantenha
`data/telecopy.db`: o mapa de tópicos e os checkpoints retomam sem recriar o que
já foi associado/enviado.

Antes de iniciar você escolhe delay, número de workers (arquivos em andamento), filtro e se quer
retomar cópias anteriores. Um resumo aparece antes da confirmação.

### `[2] Multi-destino` — um conteúdo, vários canais

Escolha a origem e depois **adicione quantos destinos quiser** (opção `3`
remove o último). Ao final, o programa copia para todos em sequência e mostra
o total geral.

Se a origem for um fórum inteiro, **cada destino que também seja fórum recebe a
própria estrutura de tópicos** antes do envio: o TELECOPY planeja os tópicos de
cada destino, cria os que faltam e encaminha cada mensagem pelo mapa daquele
destino. Um destino com problema (por exemplo, título duplicado e ambíguo) é
informado e ignorado, sem interromper os demais. Em um destino que ainda não é
fórum, o programa oferece ativar os tópicos; se você recusar (ou não for
administrador), aquele destino recebe tudo em sequência, sem estrutura de
tópicos, e os outros seguem normais.

### `[3] Modo espelho` — sincronização em tempo real

Deixe a janela aberta: cada mensagem nova na origem é copiada na hora para o
destino. Use `Ctrl+C` para sair.

Com fóruns, o espelho também é fiel: cada mensagem nova é roteada para o tópico
equivalente do destino (`reply_to_top_id`, ou General quando não há tópico). Se
aparecer um **tópico novo na origem**, o TELECOPY cria o tópico no destino
naquele momento — inclusive nome, cor e emoji — e o salva no mapa para as
mensagens seguintes. Ao iniciar o espelho de um fórum, a estrutura existente é
conferida e completada do mesmo jeito que em `Copiar canal`.

Com origem e destino não sendo fóruns, nada muda: as mensagens são copiadas em
sequência para o destino.

### `[4] Clone automático` — um passo só

Escolha **apenas a origem**. O TELECOPY monta o destino com o **mesmo tipo** da
origem (canal → canal, grupo → grupo, fórum → fórum com todos os tópicos), mostra
**um único resumo** (tipo, título, nº de tópicos, filtro, delay e workers das suas
preferências) e, com uma confirmação, cria o destino, recria a estrutura, copia
tudo e apresenta o relatório por tópico.

O título é `"<título da origem> (Clone)"`; o sufixo é configurável em
Configurações → `a` e o limite de 128 caracteres do Telegram é respeitado (a base
é cortada, o sufixo nunca). O destino nasce **privado**: o Telegram não copia
username, membros nem permissões. A foto de perfil é copiada (Configurações → `f`)
e, se a origem tiver conteúdo protegido (`noforwards`), a proteção é aplicada no
destino. Falhar a foto ou a proteção apenas gera aviso — a cópia continua.

**Idempotência.** O par origem → destino é gravado no banco assim que o Telegram
confirma a criação, **antes** de criar os tópicos. Numa próxima execução para a
mesma origem, o TELECOPY verifica se o clone ainda existe e se você continua
administrador e pergunta se quer **reutilizar** (continuar/sincronizar) ou
**criar outro**. Se a criação parar no meio, o programa oferece **continuar no
destino criado** ou **excluir apenas o canal criado nesta execução** — nunca
chats que o programa não criou.

**Criar destino automaticamente em qualquer modo.** No seletor de destino de
`Copiar canal`, `Multi-destino` e `Modo espelho`, a tecla `[A]` prepara a criação
do destino (ele é criado quando você confirma o fluxo; se a conta não tiver
nenhum chat elegível, a opção é oferecida direto). Para desligar a cópia e criar
só a estrutura, use Configurações → `k`.

**Limites.** `FloodWait` é aguardado automaticamente; se a conta atingir o limite
de canais (`CHANNELS_TOO_MUCH`) ou faltar permissão, o programa explica o motivo
e sugere usar um destino existente.

---

## 5. Menu completo

```
 [1] Copiar canal        Clonar todo o histórico de um canal
 [2] Multi-destino       Copiar para vários canais de uma vez
 [3] Modo espelho        Sincronizar novas mensagens em tempo real
 [4] Clone automático    Criar o destino e copiar tudo em um passo
 [5] Ferramentas         Diagnóstico, estatísticas e manutenção
 [6] Configurações       Preferências, idioma e segurança
 [0] Sair
```

**Ferramentas**

| Opção | Para que serve |
|---|---|
| `1` Consultar canal | Descobre nome, ID, tipo e nº de membros de qualquer canal/link |
| `2` Estatísticas | Totais da instalação, histórico de cópias e limite diário |
| `3` Fila de repetição | Mensagens que falharam e podem ser reenviadas agora |
| `4` Deduplicação | Estado das 3 camadas anti-duplicata |
| `5` Manutenção | Limpar temporários, backup do banco, remover histórico antigo, compactar |
| `6` Recuperar lacunas | Edita marcadores pendentes com o conteúdo real, sem criar mensagem nova |
| `7` Verificar ordem | Compara `msg_id × dest_msg_id` por tópico, offline, sem enviar nada |

**Configurações**

| Opção | Para que serve |
|---|---|
| `1` Intervalo entre envios | Pausa entre uploads (recomendado 0,9 s) |
| `2` Workers | Arquivos em andamento: download + pré-upload (o programa recomenda o ideal para seu PC) |
| `t` Transferência rápida | Liga/desliga pré-upload nos workers e transporte multi-conexão |
| `p` **PERFIL TURBO** | Detecta o hardware e abre todo o paralelismo que o PC aguenta |
| `n` Conexões por arquivo | 1–32 (padrão 8); meça com `bench_transfer.py` |
| `l` Janela | Lookahead de itens à frente do sender (0 = auto, 2 × workers) |
| `g` Limite de tmp | Orçamento de disco em GB para temporários em andamento (padrão 16) |
| `j` Conexões globais | Orçamento compartilhado entre workers (padrão 32, máx. 256) |
| `q` Teto diário local | Trava do programa (padrão 1000); `0` remove a parede no meio do canal |
| `3` Deduplicação | Liga/desliga a proteção anti-duplicata |
| `4` Retomar | Pula automaticamente o que já foi copiado |
| `5` Modo de envio | Reenviar (cópia limpa) ou Encaminhar (rápido) |
| `6` Assinatura | Texto fixo no fim de cada mensagem (ex.: `@meucanal`) |
| `7` Idioma | Português / English |
| `8` Exportar logs | Salva o log em `data/exports/` |
| `9` Sobre | Versão, autor e caminho dos dados |
| `a` Sufixo do clone | Texto anexado ao título da origem (padrão ` (Clone)`; `-` remove) |
| `f` Copiar foto | Copia a foto de perfil da origem ao criar um clone |
| `k` Copiar após criar | Se desligado, o clone automático só cria a estrutura |
| `h` Reservar posição | Liga/desliga o marcador de posição em falhas |
| `d` Entrega de mídia | `reference` (sem download/upload), `upload` ou `forward` |
| `s` Cópia no servidor | Prefere referências do servidor quando a entrega é por referência |
| `o` Política de falha | `placeholder` reserva e segue; `abort` pergunta `[r]/[p]/[a]` |
| `m` Retentativas | Tentativas extras por item antes da política de falha (0–10) |
| `b` Apagar histórico | Zera checkpoints, dedup e reservas (útil para recomeçar do zero) |
| `c` Redefinir credenciais | Apaga login e credenciais para configurar de novo |

---

## 6. Como retomar cópias interrompidas

Cada mensagem concluída é gravada na tabela `checkpoints` **antes** da próxima
começar. Ao rodar de novo no mesmo par origem → destino:

1. O programa lê os checkpoints e **descarta tudo o que já foi enviado**.
2. Mensagens que falharam aparecem na **Fila de repetição** (Ferramentas → 3),
   com a opção de tentar novamente na hora.
3. O programa evita reenviar o que já tem checkpoint concluído, pulado ou
   reservado por marcador. Antes de enviar, um *preflight* por tópico aborta com
   `order.violation` se encontrar uma posição resolvida depois de uma posição
   não resolvida no mesmo tópico — **inclusive quando a pendência ficou fora da
   coleta atual** (filtro diferente ou mensagem removida da origem). Mensagem que
   deixou de existir na origem resolve a posição como skip ao reprocessar em
   `[5] → 3`, para não travar o preflight para sempre.

Exactly-once absoluto através de `kill -9` não é prometido: existe uma janela
entre o Telegram aceitar uma mensagem e o SQLite gravar a confirmação. A
contratualística aqui é **detectar e abortar**: se o estado ficar ambíguo ou já
houver violação de ordem, a cópia para e sugere reconstruir o tópico/destino em
vez de “consertar” silenciosamente acrescentando mensagens fora de ordem.

Para **recomeçar do zero** em um par: Configurações → `b`.

### 6.1 Reserva de posição em falhas

A reserva é **opcional e vem desligada por padrão**; pode ser ligada em `[6] Configurações → [h]`.
Com ela ligada, quando um item falha na cópia/espelho/clone o app cria no destino um
marcador de texto **na posição sequencial da mensagem original** (e no mesmo tópico,
quando houver mapa de fórum): `⏳ Conteúdo pendente — mensagem N. Motivo: ...`.
A legenda completa, entidades e assinatura ficam guardadas no banco; o checkpoint
permanece `ERROR`, o item continua na fila de falhas e **não** entra na deduplicação.
O marcador conta como envio confirmado no limite diário. Reexecutar a cópia não cria
um segundo marcador para o mesmo item.

Para preencher as lacunas use `[5] Ferramentas → [6] Recuperar lacunas` (ou o caminho
legado `[5] → [3] Reprocessar falhas`). A recuperação **edita a mesma mensagem** do
destino com a mídia/legenda real, mantendo a posição; edições não consomem limite
diário e nunca criam uma mensagem nova para o item reservado. Quando a legenda
estoura o limite, as continuações já foram **pré-reservadas na criação do marcador**
(tabela aditiva `placeholder_parts`): a recuperação edita o marcador e os slots
existentes, na ordem. Esses slots contam no limite diário no momento da reserva, não
na recuperação. Se a origem não tiver mais a mensagem, o conteúdo for não suportado
ou a edição falhar definitivamente, o marcador vira `❌ Conteúdo indisponível` com o
motivo real; nunca fica um buraco invisível.

Álbuns com falha parcial são divididos em **segmentos contíguos**: o que já foi
confirmado permanece, o marcador ocupa a posição da mensagem que falhou e o restante
do álbum só segue depois dessa posição reservada. A recuperação de mídia por
referência tenta editar com `InputMedia`; em `FileReferenceExpired/Invalid` atualiza
a referência uma vez e, se preciso, cai para upload forte — sempre editando o slot
reservado. Marcadores legados sem slots pré-reservados não são “consertados” com
mensagens novas: a recuperação reporta falha/irrecuperável em vez de furar a ordem.

As tabelas `placeholders` e `placeholder_parts` são aditivas e idempotentes,
compatíveis com bancos da v4.0; não há `DROP TABLE` destrutivo. `[b] Zerar dados`
também apaga as reservas. Com a reserva desligada (`[h]`), o comportamento de falha
é o opt-out legado explícito.

### 6.2 Entrega por referência e ordem por tópico

O modo padrão de entrega é `upload` (baixar + reenviar), necessário para origem
protegida (`noforwards`). `RunConfig.delivery` aceita `reference|upload|forward`;
`server_copy` liga/desliga a preferência por referência quando a entrega é
`reference`; `[d]` e `[s]` persistem em `prefs`. `[5] Modo de envio` continua
sincronizado com `forward`. Os modos `reference` e `forward` não foram alterados
pela otimização de transferência.

Invariantes implementados:

- por par origem→destino **e por tópico de destino**, os `msg_id` de origem
  entregues/reservados têm `dest_msg_id` estritamente crescente;
- falha transitória nunca avança: até `max_retries + 1` tentativas, backoff
  2/4/8/16/32 s, FloodWait espera `seconds + 3`. A retentativa **reaproveita o ID
  já confirmado** e reenvia apenas o que falta (continuações pendentes, blocos
  restantes do álbum) — nunca duplica o que o Telegram já aceitou;
- falha permanente reserva o marcador antes de avançar (`on_failure=placeholder`)
  ou pausa/pergunta (`abort`: `[r]`etomar no ponto, `[p]`osition/reservar, `[a]`bortar);
- `DONE` só com ID de destino confirmado; dedup só para conteúdo entregue;
  filtros/skips exigem confirmação e contam como `intentional_skips`;
- álbuns por referência em blocos de 10 com IDs retornados estritamente crescentes;
  cascata referência → refresh único → upload forte;
- espelho serializado por tópico (`_mirror_locks`) e fila de repetição seguem a
  mesma política: retentam o transitório e, na falha definitiva, reservam a
  posição ou param — nunca entregam o item seguinte sobre uma posição aberta;
  multi-destino já era sequencial;
- o modo **encaminhar** (`delivery=forward`, alias legado `forward_mode`) obedece
  ao mesmo contrato: retries, marcador na posição, `on_failure` e `DONE` apenas com
  ID confirmado e estritamente crescente. Resposta ambígua (sem ID utilizável)
  aborta sem reservar nem reenviar;
- métricas `server_copies`, `bytes_saved`, `intentional_skips`; limite diário por
  envio confirmado, incluindo continuações de legenda, marcadores e slots
  pré-reservados.

Benchmark fake (100 vídeos de 8192 B, workers 8, delay 0,1 s, dedup off, sem rede):

| Métrica | Antes `4e7994f` | Depois `f57f469` |
|---|---:|---:|
| Tempo decorrido | 10,120 s | 10,144 s |
| Downloads | 100 | 0 |
| Envios com arquivo local | 100 | 0 |
| `server_copies` | 0 | 100 |
| Bytes transferidos | 819200 | 0 |
| `bytes_saved` | 0 | 819200 |
| Concluídas / erros | 100 / 0 | 100 / 0 |

O fake limita payloads a 8192 B e não tem latência real; o tempo é dominado pelo
delay artificial e pelo pipeline ordenado. **Não é uma promessa de velocidade em
canal real.** Nenhum teste com credenciais/canal real foi executado nesta entrega.

Riscos conhecidos: o servidor pode recusar uma referência (proteção, DC, expiração);
a cascata cobre isso com refresh/upload. Regras de edição variam por conteúdo/idade;
erros de edição são tratados sem derrubar o lote. Histórico já fora de ordem não é
reparado por edição — o verificador `[5] → 7` detecta e a reparação exige reconstruir
o tópico/destino.

### 6.3 Transferência rápida no modo Reenviar (A+B+C)

Objetivo: aumentar MB/s agregado de download + upload sem quebrar ordem estrita,
checkpoints, dedup, retomada, álbuns, tópicos, replies, assinatura, placeholder,
`DAILY_LIMIT` nem FloodWait. **Nenhum ganho é afirmado sem medição real.**

- **Pipeline (A):** com `fast_transfer=True`, cada worker baixa e pré-envia o
  arquivo (`InputFile`/`InputFileBig`) e, em álbuns, também registra
  `UploadMediaRequest(peer, ...)`. O sender continua sequencial e só monta
  `SendMediaRequest`/`SendMultiMediaRequest`, preservando `_media_options`
  (duração, w/h, voice note, spoiler, force_document). Checkpoint/dedup/
  `mark_processed` só depois do envio confirmado. Se uma parte/arquivo pré-enviado
  expirar, há **um único re-upload** a partir do temporário.
- **Transporte (B):** `fasttransfer.py` reimplementa partes paralelas a partir de
  <https://core.telegram.org/api/files>: blocos de 512 KiB alinhados (offset/limit
  múltiplos de 4096, limit divide 1 MiB, sem cruzar fronteira de 1 MiB), gravados
  por offset; upload com `SaveBigFilePart` (>10 MiB) ou `SaveFilePart` + MD5.
  Trata `FileMigrate`, `FileReferenceExpired` (refresh via `get_messages`),
  FloodWait, timeout e retry por bloco; valida tamanho final e limite de partes.
  Qualquer falha cai no `download_media`/`upload_file` padrão com log (CDN redirect
  também cai no padrão). Senders exportados são reutilizados por DC e fechados no
  shutdown/Ctrl+C; o DC da sessão compartilha o sender principal com várias
  requisições em voo. Telethon fixado em `1.45.0` pelo uso de APIs internas de
  sender/exportação.
- **Backpressure (C):** janela de índices `[próximo_a_enviar, próximo_a_enviar +
  lookahead)` e orçamento `max_tmp_gb` validado contra o disco livre (deixa 20%
  fora). `lookahead=0` usa `2 × workers`. Álbum conta como um item; item maior que
  o orçamento passa sozinho quando chega ao cabeçote. O cabeçote nunca fica
  bloqueado pelos demais, evitando deadlock. `MAX_WORKERS_UP` limita pré-uploads
  simultâneos; `BUFFER_RAM_MAX` limita o orçamento de partes em memória.
- **Auto-proteção que também acelera de volta:** FloodWait ou 5 timeouts seguidos
  reduzem as conexões pela metade, mas a capacidade **sobe de novo** (um degrau a
  cada 48 partes confirmadas) até o valor inicial — antes ficava lenta para o
  resto da execução. A penalidade de flood decai 1 s a cada 5 envios confirmados
  (teto 12 s) e zera em `run()`.
- **Premium / `FLOOD_PREMIUM_WAIT`:** é teto de velocidade **da conta**, não
  excesso de requisições. O cliente espera o tempo pedido **sem cortar conexões**
  e sem gastar tentativas do bloco, em vez de abortar para o caminho lento.
  Contas Premium não recebem esse erro de download
  (<https://core.telegram.org/api/premium>).
- **cryptg:** `main.py --check` e o início da cópia avisam se `cryptg` não puder
  ser importado (AES em Python puro limita a criptografia). `build.bat` inclui
  `cryptg` no Nuitka.
- **Timeout:** o `timeout=12` do `TelegramClient` é timeout de conexão no Telethon
  e foi mantido; as requisições de parte do caminho rápido usam `PART_TIMEOUT=30`
  sem alterar o timeout de conexão.

Configuração (`RunConfig` / prefs / menu Configurações):

| Campo | Padrão | Faixa | Efeito |
|---|---:|---|---|
| `fast_transfer` | `True` | bool | `False` reproduz o caminho legado (upload no sender), mantendo C e D |
| `connections_per_file` | `8` | 1–32 | senders/requisições em voo por arquivo |
| `lookahead` | `0` | 0 = auto | itens à frente do sender |
| `max_tmp_gb` | `16` | >0, limitado pelo disco | orçamento de temporários em andamento |
| `max_total_connections` | `32` | 1–256 | orçamento global compartilhado |
| `workers` | auto | 1–16 | arquivos em andamento (I/O-bound: pode passar do nº de CPUs) |
| `delay` | `0.35` | ≥0 | espera entre envios; `0` = sem espera |
| `daily_limit` | `1000` | ≥0 | guard **local**; `0` = sem teto |

### 6.3.1 Ordem nunca depende da velocidade (canal de curso em fórum)

O paralelismo é só na **preparação**. O envio é um `for index in range(total)`
que espera o item daquele índice: nada é enviado porque "ficou pronto primeiro".
Por isso `workers`, `connections_per_file` e o turbo **não** reordenam aulas.

`tests/test_ordem_curso.py` trava o cenário real: aulas 321→329 em dois tópicos,
com álbuns no meio, workers terminando **fora de ordem** de propósito e turbo
ligado. O teste exige:

- destino na sequência exata da origem (Aula 321, 322, 323...);
- cada aula no tópico correto do destino (`dest_topic` no checkpoint);
- álbuns contíguos e agrupados via `UploadMedia` + `SendMultiMedia`;
- `done` só com ID de destino confirmado;
- retomada após interrupção sem repetir e sem inverter.

**Atenção às prefs salvas:** os defaults novos não sobrescrevem o que já está em
`prefs.json`. Se o banner ainda mostra `0.9s`, aplique `[6] Configurações → [p]`
(perfil turbo) ou ajuste `[1]` intervalo e `[2]` workers manualmente.

### 6.3.2 O que fica idêntico e o que não fica (clone de curso)

**Fica idêntico:** ordem das mensagens, texto/legendas e entidades, álbuns
agrupados, tópicos e o roteamento de cada mensagem, replies, tópicos criados na
ordem de atividade, ícones/fixado/fechado. Na cópia de fórum o programa
**desliga dedup e assinatura sozinho** (`core.py`, caminho `setup.faithful or
plan.forum`): repetições de material entre tópicos são conteúdo legítimo e não
podem ser descartadas. `tests/test_ordem_curso.py` prova os dois lados — com dedup
desligado a aula repetida chega; com dedup ligado ela pode sumir.

**Não fica idêntico (limites do Telegram, não do programa):**

| Item | O que acontece |
|---|---|
| Legenda > 1024 caracteres | vira a mídia + continuações em mensagens seguintes; o texto chega **integral e na ordem**, mas o destino tem mais mensagens que a origem |
| Enquete/quiz, dado, localização, contato, mídia paga | não são recriáveis por upload; entram como **não suportado** e a conferência não fecha como completa |
| Data/hora, visualizações, reações, comentários | não são copiados: a cópia é uma mensagem nova, sua |
| Autor | é você — é o que significa "cópia limpa, sem encaminhado de" |

**Como conferir em vez de confiar:** ao terminar, o programa imprime
`Conferência: ...` (`result.complete` / `result.incomplete`) e a
**CONFERÊNCIA POR TÓPICO** com esperado / enviadas / faltando. A cópia só é
declarada completa com zero erros, zero não suportados, zero faltando e nenhum
marcador em aberto. Se não fechar, retome: o checkpoint continua de onde parou.

### 6.4 Perfil turbo: velocidade máxima do PC

`[6] Configurações → [p]` aplica de uma vez o maior paralelismo que o hardware
comporta, calculado por RAM disponível, CPUs e disco livre:

```
workers                  = min(16, RAM/0,35 GB, CPUs x 3, disco/2 GB)
connections_per_file     = min(16, max(4, workers x 2))
max_total_connections    = min(128, workers x connections_per_file)
max_tmp_gb               = min(64, max(8, disco livre x 0,5))
delay                    = 0,2 s
daily_limit              = 0 (sem parede local)
```

O turbo **não** toca em ordem estrita, checkpoint, dedup, retomada, álbuns nem na
política de FloodWait. Gargalos artificiais removidos nesta revisão:

| Antes | Depois | Efeito |
|---|---|---|
| pré-upload limitado a `workers // 2` | acompanha os workers | estágio mais lento deixa de serializar |
| `workers` travado em 6 | até 16, por hardware | mais arquivos em andamento |
| `delay` mínimo 0,1 s | mínimo 0 s | sem espera artificial entre envios |
| conexões/arquivo máx. 8 | máx. 32 | mais partes em voo por arquivo |
| pool global máx. 64 | máx. 256 | orçamento não estrangula os workers |
| pool só reduzia, nunca recuperava | recupera até o inicial | velocidade volta após o flood |
| 3 timeouts cortavam conexões | 5 timeouts | menos falso positivo |
| fallback com partes de 128–256 KiB | 512 KiB | 2–4x menos idas e voltas no plano B |
| `request_retries=1` | `5` | menos queda precoce para o caminho lento |
| teto local de 1000 msgs/dia fixo | configurável, `0` = sem teto | não interrompe canal de 1.279 msgs |

Benchmark real (não executado nesta entrega contra o Telegram):

```bash
python bench_transfer.py --source LINK --msg-id N --connections 1,2,4,8,16
```

Ele baixa e reenvia o arquivo para as Mensagens Salvas e imprime MB/s padrão vs.
rápido, recomendando o menor `connections_per_file` dentro de 5% do melhor ponto
medido. **Valide com ~10 mensagens num canal de teste antes de rodar o canal
inteiro.** Não foi possível testar contra o Telegram real: FloodWait de upload
agressivo, expiração de partes pré-enviadas em produção, migração de DC real,
CDN, throughput em link de 1 Gbps e comportamento de conta Premium/non-Premium.

Licenças: nenhum código foi copiado de FastTelethon/mautrix-telegram. O
`mautrix/telegram` verificado é AGPL-3.0; o repositório `FastTelethonhelper`
localizado declara MIT, mas a técnica foi reimplementada do zero pela documentação
oficial e pela API do Telethon para evitar dependência de código copyleft.

---

## 7. Deduplicação: como funciona

```
 Mensagem recebida
       │
       ▼
 ┌──────────────┐  acerto   ┌────────────────────────┐
 │ Cache LRU    │──────────►│  Já enviada — ignora   │
 │ (memória)    │           └────────────────────────┘
 └──────┬───────┘
        │ não está
        ▼
 ┌──────────────┐  ausente  ┌────────────────────────┐
 │ Bloom Filter │──────────►│  Nova com certeza      │
 └──────┬───────┘           │  → enviar              │
        │ “talvez exista”   └────────────────────────┘
        ▼
 ┌──────────────┐  acerto   ┌────────────────────────┐
 │ SQLite       │──────────►│  Confirmada — ignora   │
 └──────┬───────┘           └────────────────────────┘
        │ não está (falso positivo do Bloom ≈ 0,1 %)
        ▼
     Enviar e registrar nas 3 camadas
```

O hash é calculado sobre o **conteúdo real**: identidade do arquivo no Telegram
(documento/foto) + legenda. Textos usam o texto + o dia — assim avisos iguais
em dias diferentes continuam sendo copiados.

A comparação é sempre **por canal de destino**: o mesmo arquivo pode ir para o
canal A e para o canal B, mas nunca duas vezes para o canal A.

---

## 8. Onde ficam seus dados

Os dados ficam na pasta `data/`:

- Rodando `python main.py`: `data/` ao lado do projeto.
- Executável no Windows: `%LOCALAPPDATA%\TELECOPY\data` (estável mesmo se o
  `.exe` for movido). macOS e Linux usam a pasta de dados do usuário.
- Se uma versão portátil antiga já tinha dados ao lado do executável, eles são
  migrados para o local estável na primeira execução.

A pasta contém:

```
data/
├── session.session        login do Telegram (mantenha privado!)
├── telecopy.db            banco SQLite (progresso, dedup, estatísticas)
├── prefs.json             suas preferências
├── stats.json             números acumulados
├── daily.json             contador do limite diário
├── vault/
│   ├── credentials.enc    API ID/Hash cifrados (AES-256-GCM)
│   ├── local.key          chave local do cofre
│   └── legacy_v3/         backup do cofre antigo (se existir)
├── logs/telecopy.log      log rotativo (2 MB por arquivo, 3 backups)
├── exports/               logs exportados
├── backups/               backups do banco
└── tmp/                   arquivos temporários (limpos automaticamente)
```

**Portabilidade:** copiar a pasta `data/` para outro computador mantém o login
e as credenciais funcionando (a chave do cofre viaja junto). Se o cofre não
puder ser lido, o programa mostra o caminho e pede as credenciais novamente.

> Backup recomendado: copie `data/` para um pendrive ou nuvem de tempos em
> tempos. **Não** compartilhe `session.session` nem `vault/`: quem tiver esses
> arquivos pode entrar na sua conta.

---

## 9. Solução de problemas

| Sintoma | Solução |
|---|---|
| `pip não é reconhecido` | Reinstale o Python marcando “Add to PATH”, ou use `python -m pip install -r requirements.txt` |
| “Dependências ausentes” | Rode o comando de `pip install` que o programa mostra |
| “Não foi possível conectar ao Telegram” | Verifique internet/firewall/proxy; em redes corporativas pode ser necessário liberar a porta 443. Use `--debug` para ver detalhes na pasta de dados indicada pelo programa |
| O código de login não chegou | Confira o método indicado pelo TELECOPY: o código pode aparecer no chat oficial “Telegram” em outro aparelho conectado, por SMS ou outro método. Não solicite códigos em sequência; o Telegram pode aplicar limite temporário |
| A cópia mostra `0 enviadas` | Veja a primeira falha exibida. Para canais, a conta precisa de permissão para publicar no destino; em canais com restrição de encaminhamento, o TELECOPY usa automaticamente o modo de reenvio (download + upload). Falhas repetidas agora interrompem a execução cedo |
| API ID/Hash é pedido em toda inicialização | Veja o caminho mostrado após “Credenciais salvas e verificadas”. Execute `python main.py --check` e confirme que a pasta de dados está gravável; executáveis usam o diretório estável do usuário |
| “Canal não encontrado” | Confirme que você é **membro** (origem) e **admin** (destino) e que o link/ID está correto. Use Ferramentas → 1 para testar |
| “Você precisa ser ADMINISTRADOR no destino” | No Telegram: canal → Administradores → adicionar você com permissão *Postar mensagens* |
| Telegram pede espera (FloodWait) | O programa aguarda sozinho e aumenta o intervalo. Se acontecer muito, aumente o delay para 2–3 s |
| Cópia muito lenta no modo Reenviar | Meça `bench_transfer.py`, ajuste `connections_per_file` (Configurações → n) e workers/arquivos em andamento (→ 2). Origem protegida não pode usar Encaminhar/reference |
| Travou / fechou no meio | Abra de novo e responda **sim** em “retomar” — nada será repetido |
| “Limite de canais” ao criar destino | O Telegram limita quantos canais/grupos uma conta pode criar. Use um destino existente (Copiar canal → 1) ou apague canais antigos |
| “Já existe um clone desta origem” | O TELECOPY oferece reutilizar o clone (continuar/sincronizar) ou criar outro. Se a estrutura ficou incompleta, dá para continuar no destino criado ou excluir só ele |
| “O TELECOPY já está aberto” | Feche a outra janela (proteção contra dois processos no mesmo banco) |
| Sessão expirou / “sessão inválida” | Configurações → `c` (redefinir credenciais) e faça login novamente |
| Quero apagar absolutamente tudo | Feche o programa e delete a pasta `data/` |

---

## 10. Testes automatizados

O projeto inclui uma suíte que exercita o motor completo **sem acessar o
Telegram** (um cliente falso simula downloads, uploads, álbuns, FloodWait e
falhas de rede):

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -v
```

A suíte cobre formatadores, cofre criptográfico, banco, dedup, filtros,
traduções, motor de cópia (ordem, álbuns, retomada, erros, FloodWait e
encaminhamento), roteamento de tópicos de fórum, replies dentro de tópicos,
criação automática de fórum, complemento de tópicos em destino existente,
paginação de tópicos, legendas longas com entidades, enquetes nativas, mídia não
reproduzível, conferência por tópico, fila de repetição, preferências e menus.
O clone automático tem suíte própria (`tests/test_auto_clone.py`): canal, grupo e
fórum criados do zero, reuso de clone existente, recusa de clone sem admin,
falha parcial com retomada, exclusão apenas do canal criado, foto com falha e
`[A]` nos três modos.

WP1/WP2 acrescentam `tests/test_ordem.py`, `tests/test_ui_order.py`,
`tests/test_retry_parcial.py`, `tests/test_forward_ordem.py` e
`tests/test_ordem_preflight.py` e `tests/test_mirror_ordem.py`. Estado verificado
nesta entrega:
**357 passed** (`python -m pytest -o addopts='' -q`, suíte isolada, sem rede).
Ruff: `All checks passed` (regras E9/F63/F7/F82/F401/F841, excluindo `data/`).
Cobertura crítica medida: 86% no conjunto (`docs/evidencias/coverage-critica.txt`);
a meta de 90% ainda não foi atingida no `copier.py` (82%). Mutação:
`politica_erros.py` 100/100 mutantes mortos (`docs/evidencias/mutacao-politica.txt`).

### Rodar no celular (Android / Termux)

Um comando, dentro do Termux:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/montx2/telecopy/main/termux-setup.sh)
```

Ou, já dentro do repositório: `bash termux-setup.sh`. O script instala as
dependências nativas via `pkg` (com fallback de compilação), instala o resto via
pip, clona/reusa o projeto, **verifica automaticamente** com `doctor --json` e
ativa o wake-lock. É idempotente; use `--verify-only` para só diagnosticar.

Guia completo, opções e solução de problemas: [`docs/TERMUX.md`](docs/TERMUX.md).
No iPhone não é prático — use um servidor com o celular como controle.

### CLI não interativa (Fase 4/5)

```bash
telecopy clone --src @origem --dst @destino --perfil recomendado --retomar --json
telecopy clone --src @origem --dst @destino --filtro photos --inicio 22:00 --fim 06:00   --bandwidth 5000000 --report-dir data/relatorios
telecopy verify --src @origem --dst @destino --reparar-confirmados --json
telecopy archive --src @origem --out data/arquivo --limite 1000
telecopy ui --report-dir data/relatorios --token SEU_TOKEN   # somente leitura, 127.0.0.1
telecopy doctor --auth --json
telecopy --proxy socks5://user:pass@host:1080 clone ...
```

Recursos P1 são **opt-in** e expostos apenas pela CLI (não pelo menu interativo):
proxy SOCKS/HTTP, janela de horário (`--inicio/--fim`, inclusive virando meia-noite),
limitador local de banda (`--bandwidth`, token bucket — não mede a rede real),
reescrita de texto (`--reescrever-de/-para`; remove entidades quando altera o texto e é
recusada em modo forward), webhook de conclusão (`--notificar-url`; falha só registra log),
arquivo offline (`archive`: `manifest.jsonl` + `media/`) e UI local somente leitura com token.
Códigos de saída: `0` sucesso, `1` erros/incompleto, `2` interrupção/credenciais/uso inválido.

A auditoria final (`AUDITORIA.md`) encontrou 7 defeitos que a suíte anterior não
cobria. A verificação posterior achou mais 2 da mesma classe no espelho e na fila
de repetição (D8/D9). Todos os 9 foram corrigidos e viraram testes de regressão.
As sondas originais estão em `audit/` para reprodução independente.

| Invariante / recurso | Teste representativo |
|---|---|
| Mídia por referência sem download | `test_video_por_referencia_sem_download` |
| Cascata refresh/upload e álbuns | `tests/test_ordem.py` (referência, fallback, álbum parcial) |
| Transitório não avança | `test_falha_transitoria_nao_avanca` |
| 200 mensagens com falhas aleatórias | `test_ordem_com_200_mensagens_e_falhas_aleatorias` |
| Retomada após interrupção | `test_retomada_apos_interrupcao_continua_na_ordem` |
| Preflight detecta violação | `test_detecta_violacao_de_ordem_existente` |
| Fórum: ordem independente por tópico | `test_forum_ordem_independente_por_topico` |
| Espelho serializado | `test_espelho_dez_mensagens_em_ordem` |
| Prompt de aborto `[r]` | `test_abort_prompt_retoma_no_mesmo_ponto` |
| Verificador offline `[5] → 7` | `test_tool_order_reporta_violacao` |
| Retentativa não reenvia item confirmado | `test_retry_nao_reenvia_midia_ja_confirmada` |
| Retentativa não reenvia bloco de álbum | `test_retry_nao_reenvia_bloco_de_album_confirmado` |
| Cada envio conta no diário | `test_cada_envio_confirmado_conta_no_limite_diario` |
| Forward: barreira e retries | `test_forward_falha_permanente_reserva_posicao` |
| Forward: DONE exige ID | `test_forward_sem_id_confirmado_nao_vira_done` |
| Forward ambíguo aborta | `test_forward_ambiguo_aborta_sem_reservar` |
| Preflight não é cegado por filtro | `test_preflight_nao_e_cegado_por_filtro` |
| Verificador sem falso positivo | `test_tool_order_pendencia_final_nao_e_violacao` |
| Espelho reserva posição na falha | `test_espelho_falha_permanente_reserva_posicao` |
| Espelho aborta sem seguir | `test_espelho_abort_nao_segue_adiante` |
| Fila de repetição sem buraco | `test_fila_de_repeticao_nao_abre_buraco` |

Também é possível rodar sem pytest:

```bash
python tests/test_core.py
python tests/test_app_flow.py
```

---

## 11. Compilar o `.exe` e o instalador

No **Windows**, com Python instalado:

```bat
build.bat
```

O script instala dependências, roda os testes e gera `Telecopy.exe` com Nuitka.

Para gerar o instalador, instale o [Inno Setup](https://jrsoftware.org/isdl.php)
e rode:

```bat
iscc setup.iss
```

O instalador sai em `dist\Telecopy_Setup_v4.0.0.exe`. Ele instala em
`%LOCALAPPDATA%\TELECOPY` (sem exigir administrador) e **preserva** a pasta
`data` na desinstalação.

---

## 12. Arquitetura do código

```
main.py            entrada, argumentos, instância única, checagem de ambiente
core.py            App (login + menus), ChannelPicker, CredentialManager
channel_clone.py   criação de destino e clonagem da estrutura de fóruns/tópicos
copier.py          motor: coleta → agrupa álbuns → pipeline download/pré-upload → envio ordenado
fasttransfer.py    transporte multi-conexão por arquivo (partes MTProto) com fallback
bench_transfer.py  benchmark real padrão vs rápido e recomendação de conexões
database.py        repositório SQLite (WAL): checkpoints, dedup, sessões, falhas
dedup.py           LRU + Bloom Filter + hash de conteúdo
filters.py         filtros combináveis de mensagens
security.py        cofre AES-256-GCM portátil
retry_queue.py     fila persistente de mensagens com falha
system_analyzer.py perfil de hardware e recomendação de workers
prefs.py           preferências do usuário (JSON atômico)
utils.py           logging com rotação, estatísticas, limite diário, formatadores
i18n.py            traduções PT-BR / EN
config.py          caminhos, constantes, cores, UI e RunConfig
tests/             suíte automatizada + cliente Telegram simulado
```

**Desempenho:** `workers` significa arquivos em andamento (download + pré-upload
quando `fast_transfer=True`), com envio estritamente sequencial para manter a
ordem original. Workers recomendados: 1 para cada 0,5 GB de RAM livre, limitado
pelo dobro das CPUs (máx. 16). Conexões por arquivo e
orçamento global são separados de workers; meça antes de aumentar.

---

## 13. Perguntas frequentes

**Preciso deixar o computador ligado?**
Sim, durante a cópia. Se desligar, é só abrir de novo e escolher “retomar”.

**Posso copiar de um canal privado?**
Sim, desde que sua conta seja membro dele.

**O canal de origem precisa ser meu?**
Não. Basta você conseguir ler as mensagens.

**Perco a conta por usar isso?**
O programa respeita os limites oficiais: delay configurável, limite diário de
1.000 mensagens e espera automática em FloodWait. Ao atingir o limite diário, a
execução é interrompida sem marcar as mensagens restantes como falha; retome no
dia seguinte com o mesmo destino e o mesmo banco. Mesmo assim, use intervalos
conservadores (0,9 s ou mais) em canais grandes.

**Funciona com tópicos de fórum?**
Sim, e o destino fica igual ao fórum de origem. Ao selecionar um grupo do tipo
Fórum, o programa pede o ID ou link do tópico; **ENTER significa "todos os
tópicos"**. Para recriar a estrutura inteira, escolha o destino e pressione
ENTER também no prompt do destino: o TELECOPY reutiliza os tópicos que já
existem, cria os ausentes com nome, cor, emoji e estado (fixado/fechado/oculto)
e envia cada mensagem ao tópico correspondente, na ordem original. Vale para
`Copiar canal` (destino existente ou criado), para cada destino de
`Multi-destino` e para o `Modo espelho` — que cria na hora um tópico que surgir
na origem. A ordem dos tópicos é reconstruída criando do menos ativo para o
mais ativo, e os fixados são recolocados ao final. Use ENTER também no filtro
para não deixar mensagens de fora. A conferência final mostra
concluídas/esperadas por tópico.

E se eu digitar um tópico do destino com a origem sendo o fórum inteiro? O
programa avisa que **tudo** vai para aquele tópico e pede confirmação, porque
nesse caso não há como reproduzir a estrutura. Já se o destino for um
supergrupo sem tópicos e você for administrador, ele oferece ativar o modo fórum
antes de copiar. Um emoji recusado pelo Telegram não aborta mais a clonagem: o
tópico é criado sem o emoji (e, se preciso, sem cor), com aviso. Se você copiou
esse par com uma versão anterior que mandava tudo para o General, o programa
avisa dos checkpoints antigos e oferece zerar **apenas** os desse par — senão
"retomar" pularia justamente as mensagens que precisam ser reenviadas ao tópico
certo.

**Dá para adicionar uma assinatura no fim das mensagens?**
Sim: Configurações → `6`. O limite de 1.024 caracteres de legenda é respeitado
automaticamente.

**Meus dados vão para algum servidor?**
Não. Tudo roda na sua máquina; a única conexão externa é com o próprio Telegram
(e uma consulta opcional de versão no GitHub, que pode ser desligada).

---

**TELECOPY v4.0.0** · by montx2 ·
SQLite WAL · AES-256-GCM · Bloom Filter · Async · PT/EN
