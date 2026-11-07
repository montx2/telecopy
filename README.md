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
**Telegram channel replicator — fast, clean, and duplicate-free.**

---

## O que faz / What it does

Copia mensagens (texto, fotos, vídeos, áudio, documentos) de qualquer canal ou grupo que você lê para qualquer canal ou grupo que você administra. Lembra o que já foi copiado, pula duplicatas automaticamente e respeita os limites de taxa do Telegram.

Copies messages (text, photos, videos, audio, documents) from any Telegram channel or group you can read, to any channel or group you manage. It remembers what was already copied, skips duplicates automatically, and respects Telegram's rate limits.

---

## Funcionalidades / Features

| | PT | EN |
|---|---|---|
| **Resume** | Retoma de onde parou após interrupção | Picks up exactly where it left off |
| **Deduplicação** | 3 camadas: LRU → Bloom Filter → SQLite | 3-layer engine prevents duplicate uploads |
| **Filtros** | Fotos, vídeos, áudio, documentos, links, recentes... | Photos, videos, audio, docs, links, recent... |
| **Multi-destino** | Um canal de origem → vários destinos | One source → several channels at once |
| **Criptografia** | AES-256-GCM, credenciais protegidas | AES-256-GCM encrypted credentials |
| **Idioma** | Português e Inglês, escolha na primeira execução | Portuguese and English, chosen at first run |
| **Progresso** | Barras animadas com `rich`, fallback de texto | Animated bars with `rich`, text fallback |
| **Limite diário** | Avisa antes de atingir o limite recomendado | Warns before reaching Telegram's daily cap |
| **Retry queue** | Mensagens que falharam entram em fila automática | Failed messages are queued and retried |

---

## Instalação / Installation

```bash
git clone https://github.com/montx2/telecopy.git
cd telecopy
pip install -r requirements.txt
python main.py
```

**Python 3.10+** é necessário / required.

---

## Configuração inicial / First-time setup

1. Acesse / Go to: **https://my.telegram.org/apps**
2. Crie um app e copie o **API ID** e o **API Hash**
3. Execute `python main.py` e informe as credenciais quando solicitado
4. Selecione o idioma desejado (Português ou English)

As credenciais são armazenadas localmente com **AES-256-GCM**.  
Credentials are stored locally with **AES-256-GCM**.

---

## Uso / Usage

```
  TELECOPY  v3.0.0  ·  por montx2
  ════════════════════════════════════════════════════════════════════════════

  [1]  Copiar canal       — Replica um canal ou grupo para outro
  [2]  Multi-destino      — Enviar para vários canais de uma vez
  [3]  Ferramentas        — Utilitários e diagnósticos
  [4]  Configurações      — Personalizar o aplicativo
  [0]  Sair
```

---

## Arquitetura / Architecture

```
main.py
  └── core.py          FSM principal (auth, menus, fluxos)
        ├── copier.py  Motor de cópia produtor/consumidor
        ├── dedup.py   Deduplicação LRU + Bloom + SQLite
        ├── database.py Repositório SQLite (WAL, thread-safe)
        ├── filters.py  Filtros de mensagens
        ├── i18n.py     Internacionalização PT/EN
        ├── security.py Vault AES-256-GCM
        ├── utils.py    Logging, stats, formatadores
        ├── retry_queue.py Fila persistente de retry
        └── config.py   Constantes, paleta de cores, UI
```

### Deduplicação em detalhe / Deduplication in detail

Cada mensagem passa por 3 camadas antes de ser enviada:

```
 Mensagem recebida
       │
       ▼
  ┌─────────┐   hit    ┌─────────────────────┐
  │ LRU     │─────────►│  Já enviada — skip  │
  │ Cache   │          └─────────────────────┘
  └────┬────┘
       │ miss
       ▼
  ┌─────────┐  ausente ┌─────────────────────┐
  │ Bloom   │─────────►│  Definitivamente     │
  │ Filter  │          │  nova — enviar       │
  └────┬────┘          └─────────────────────┘
       │ "pode existir"
       ▼
  ┌─────────┐   hit    ┌─────────────────────┐
  │ SQLite  │─────────►│  Confirmado — skip  │
  │  (WAL)  │          └─────────────────────┘
  └────┬────┘
       │ miss (falso positivo do Bloom, ~0.1%)
       ▼
  Enviar mensagem
```

---

## Estrutura de arquivos / File structure

```
telecopy/
├── main.py
├── config.py
├── core.py
├── copier.py
├── database.py
├── dedup.py
├── filters.py
├── i18n.py
├── security.py
├── utils.py
├── retry_queue.py
├── requirements.txt
├── README.md
└── data/                  ← criado automaticamente / auto-created
    ├── telecopy.db        ← banco SQLite principal
    ├── vault/             ← credenciais cifradas
    ├── logs/              ← logs do sistema
    ├── exports/           ← logs exportados
    └── tmp/               ← arquivos temporários
```

---

## Troubleshooting

**"Canal não encontrado"**  
Verifique se você é membro do canal ou grupo no Telegram.  
Confirm you are a member of the channel or group in Telegram.

**FloodWait**  
O sistema aguarda automaticamente o tempo exigido pelo Telegram.  
The tool automatically waits the required time.

**Memória insuficiente**  
Reduza `BUFFER_RAM_MAX` em `config.py`.  
Reduce `BUFFER_RAM_MAX` in `config.py`.

---

## Dependências / Dependencies

| Pacote | Versão | Obrigatório |
|---|---|---|
| telethon | ≥ 1.35.0 | Sim |
| cryptography | ≥ 41.0.0 | Sim |
| psutil | ≥ 5.9.0 | Sim |
| rich | ≥ 13.0.0 | Não (recomendado) |

---

**TELECOPY v3.0.0** · by montx2  
SQLite WAL · AES-256-GCM · Bloom Filter · Async · PT/EN
