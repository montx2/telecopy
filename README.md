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

`v4.0.0` · Python 3.10+ · Windows / Linux / macOS

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
  pressionar ENTER nos dois prompts de tópico (copiar todos), o TELECOPY confere
  a estrutura antes de enviar. Ele reutiliza tópicos já associados, cria os que
  faltam no seu grupo e mostra no resumo quantos serão reaproveitados/criados.
  Tópicos com título duplicado e sem ícone distintivo são recusados para evitar
  enviar material ao lugar errado. Se a cópia não ficar completa, opções como
  fechar/ocultar tópicos não são aplicadas, para que a retomada não fique
  bloqueada.
- **Criar destino automaticamente**: cria um canal/grupo privado do tipo da
  origem, copia título e descrição quando disponíveis e, se a origem for um
  fórum, recria os tópicos (nomes, ícones e opções de fixado/fechado/oculto) e
  envia cada mensagem ao tópico correspondente. O título começa igual ao da
  origem e pode ser alterado antes da criação.

Na cópia de fórum inteiro, o TELECOPY desativa a deduplicação por conteúdo e a
assinatura nesta execução: avisos repetidos em tópicos diferentes são conteúdo
legítimo, e uma assinatura alteraria o texto original. Respostas dentro dos
tópicos são religadas às cópias das mensagens respondidas quando o checkpoint
origem→destino está disponível. Ao final, o programa mostra uma conferência por
tópico (concluídas/esperadas) e só marca a execução como completa se não houver
falhas, itens não reproduzíveis ou mensagens faltantes.

O Telegram não permite uma cópia 100% idêntica: o destino não recebe o username
público, foto, membros nem permissões da origem; datas originais, autores, IDs,
reações, votos/resultado de enquetes e várias mensagens de serviço não podem ser
reproduzidos como no original. Botões de bot/callback, mídia paga, localização ao
vivo e dados animados não são recriados como se fossem o original; o que a API
não permite reproduzir é registrado como falha, não como sucesso. A cor de um
tópico já existente não é editável pela API; para reproduzir a cor, use um destino
novo. A ordem visual dos tópicos depende de fixados/atividade do Telegram. O
destino automático fica privado e a conta que o criou já é administradora.
Confira o resumo antes de confirmar. Se uma cópia de fórum for interrompida,
escolha o mesmo destino, pressione ENTER para todos os tópicos e mantenha
`data/telecopy.db`: o mapa de tópicos e os checkpoints retomam sem recriar o que
já foi associado/enviado.

Antes de iniciar você escolhe delay, número de workers, filtro e se quer
retomar cópias anteriores. Um resumo aparece antes da confirmação.

### `[2] Multi-destino` — um conteúdo, vários canais

Escolha a origem e depois **adicione quantos destinos quiser** (opção `3`
remove o último). Ao final, o programa copia para todos em sequência e mostra
o total geral.

### `[3] Modo espelho` — sincronização em tempo real

Deixe a janela aberta: cada mensagem nova na origem é copiada na hora para o
destino. Use `Ctrl+C` para sair.

---

## 5. Menu completo

```
 [1] Copiar canal        Clonar todo o histórico de um canal
 [2] Multi-destino       Copiar para vários canais de uma vez
 [3] Modo espelho        Sincronizar novas mensagens em tempo real
 [4] Ferramentas         Diagnóstico, estatísticas e manutenção
 [5] Configurações       Preferências, idioma e segurança
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

**Configurações**

| Opção | Para que serve |
|---|---|
| `1` Intervalo entre envios | Pausa entre uploads (recomendado 0,9 s) |
| `2` Workers | Downloads simultâneos (o programa recomenda o ideal para seu PC) |
| `3` Deduplicação | Liga/desliga a proteção anti-duplicata |
| `4` Retomar | Pula automaticamente o que já foi copiado |
| `5` Modo de envio | Reenviar (cópia limpa) ou Encaminhar (rápido) |
| `6` Assinatura | Texto fixo no fim de cada mensagem (ex.: `@meucanal`) |
| `7` Idioma | Português / English |
| `8` Exportar logs | Salva o log em `data/exports/` |
| `9` Sobre | Versão, autor e caminho dos dados |
| `b` Apagar histórico | Zera checkpoints e dedup (útil para recomeçar do zero) |
| `c` Redefinir credenciais | Apaga login e credenciais para configurar de novo |

---

## 6. Como retomar cópias interrompidas

Cada mensagem concluída é gravada na tabela `checkpoints` **antes** da próxima
começar. Ao rodar de novo no mesmo par origem → destino:

1. O programa lê os checkpoints e **descarta tudo o que já foi enviado**.
2. Mensagens que falharam aparecem na **Fila de repetição** (Ferramentas → 3),
   com a opção de tentar novamente na hora.
3. O programa evita reenviar o que já tem checkpoint concluído. Em uma queda
   exatamente entre o envio e a gravação do checkpoint, o Telegram pode ter
   aceitado a mensagem sem o TELECOPY receber a confirmação; nesse caso raro a
   retomada pode duplicar o item. Confira o destino antes de apagar checkpoints.

Para **recomeçar do zero** em um par: Configurações → `b`.

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
| Cópia muito lenta | Aumente workers (Configurações → 2) ou use **Encaminhar** (Configurações → 5) |
| Travou / fechou no meio | Abra de novo e responda **sim** em “retomar” — nada será repetido |
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
copier.py          motor: coleta → agrupa álbuns → download paralelo → envio ordenado
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

**Desempenho:** download em paralelo (I/O bound) com envio estritamente
sequencial, para que o destino fique na ordem original. Workers recomendados:
1 para cada 0,5 GB de RAM livre, limitado pelo número de CPUs (máx. 8).

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
Sim. Ao selecionar um grupo do tipo Fórum, o programa pede o ID ou link do
tópico (ou ENTER para todos). Para copiar a estrutura inteira para outro fórum
seu, escolha o destino existente e pressione ENTER nos prompts de tópico da
origem e do destino; os tópicos ausentes são criados após a confirmação. Use
ENTER também no filtro para não deixar mensagens de fora. A conferência final
mostra concluídas/esperadas por tópico.

**Dá para adicionar uma assinatura no fim das mensagens?**
Sim: Configurações → `6`. O limite de 1.024 caracteres de legenda é respeitado
automaticamente.

**Meus dados vão para algum servidor?**
Não. Tudo roda na sua máquina; a única conexão externa é com o próprio Telegram
(e uma consulta opcional de versão no GitHub, que pode ser desligada).

---

**TELECOPY v4.0.0** · by montx2 ·
SQLite WAL · AES-256-GCM · Bloom Filter · Async · PT/EN
