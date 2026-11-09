# Rodar o TELECOPY no Termux (Android)

Guia do script `termux-setup.sh`. **Leia os limites no fim** — rodar no celular é
possível, mas o Android é um host ruim para cópias longas.

## Antes de começar

Instale o **Termux pelo [F-Droid](https://f-droid.org/packages/com.termux/) ou pelas
[releases oficiais](https://github.com/termux/termux-app/releases)**.
**Não use a versão da Play Store**: ela está abandonada e os repositórios dela
falham no `pkg update`. Esse é o erro mais comum de instalação.

## Um comando

Dentro do Termux:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/montx2/telecopy/main/termux-setup.sh)
```

Ou, se você já clonou o repositório:

```bash
cd telecopy
bash termux-setup.sh
```

> **Use `bash termux-setup.sh`, não `./termux-setup.sh`.** O Android não tem
> `/usr/bin/env`, então o shebang padrão falha na execução direta. O script se
> auto-corrige com `termux-fix-shebang` quando disponível, mas chamar via `bash`
> funciona sempre.

### Se a CLI 4.1 ainda não estiver na `main`

Enquanto o PR da 4.1 não for mesclado, aponte para a branch:

```bash
TELECOPY_REF=arena/859ccdf0-telecopy \
  bash <(curl -fsSL https://raw.githubusercontent.com/montx2/telecopy/arena/859ccdf0-telecopy/termux-setup.sh)
```

## O que o script faz

| Etapa | Ação | Fallback |
|---|---|---|
| 1 | `pkg update` + `python`/`git` | se o repo falhar, indica F-Droid / `termux-change-repo` |
| 2 | `cryptography` e `psutil` via **pkg** | compila via pip com Rust/clang se o pkg não resolver |
| 3 | `telethon==1.45.0` + `rich` via pip | venv com `--system-site-packages` se o pip global falhar |
| 4 | Clona ou reusa o projeto | `git pull --ff-only`; respeita mudanças locais |
| 5 | Verifica imports + `doctor --json` | cai para `main.py --check` se o doctor não responder |
| 6 | Ativa `termux-wake-lock` e imprime os próximos passos | avisa se o Termux:API não estiver instalado |

O script é **idempotente**: pode rodar de novo sem quebrar nada.

## Por que não usar `pip install -r requirements.txt`

No Termux, `pip install cryptography` tenta compilar código Rust e normalmente
falha; `psutil` também falha ao compilar contra os headers do Android. As versões
prontas do Termux já vêm corrigidas:

```bash
pkg install python-cryptography python-psutil   # certo
pip install cryptography psutil                 # costuma quebrar
```

Por isso o script instala **nativas via `pkg`** e usa pip só para o que é Python
puro (`telethon`, `rich`).

## Opções

```bash
bash termux-setup.sh --verify-only    # só verifica, não instala
bash termux-setup.sh --no-upgrade     # pula o pkg upgrade (mais rápido)
bash termux-setup.sh --with-cryptg    # tenta o acelerador opcional
bash termux-setup.sh --dir ~/meuclone # usa outro diretório
bash termux-setup.sh --ref main       # escolhe branch/tag ao clonar
bash termux-setup.sh --help
```

Variáveis: `TELECOPY_REF`, `TELECOPY_DIR`, `TELECOPY_REPO`, `NO_COLOR`.
Códigos de saída: `0` ok, `1` falha de instalação/verificação, `2` uso ou ambiente inválido.

## Primeiro uso (login)

```bash
cd telecopy
python main.py
```

O menu pede `api_id` e `api_hash` (de [my.telegram.org](https://my.telegram.org))
e o código de login. A sessão fica em `data/`. Depois de logar uma vez:

```bash
python -m telecopy_cli clone --src @origem --dst @destino --perfil recomendado
python -m telecopy_cli doctor --json
```

## Cópias longas: o Android atrapalha

O Android mata processos em segundo plano. Para reduzir o risco:

```bash
termux-wake-lock
```

- Deixe o Termux **em primeiro plano** e o aparelho **na tomada**.
- **Desative a otimização de bateria** do Termux nas configurações do Android.
- Use **Wi-Fi** — clonar canal grande consome muitos dados e esquenta o aparelho.
- Instale o app **Termux:API** (além de `pkg install termux-api`) para o wake-lock funcionar.

Mesmo assim, uma cópia de muitas horas pode ser interrompida. **Para serviços
longos, o robusto é rodar num servidor sempre ligado** (VPS, Raspberry Pi, PC) e
usar o celular apenas como controle: SSH, a UI somente leitura (`telecopy ui`) por
túnel SSH, e o webhook de conclusão (`--notificar-url`).

## Solução de problemas

| Sintoma | Causa provável | Correção |
|---|---|---|
| `pkg update` falha / repositório indisponível | Termux da Play Store | instale pelo F-Droid/GitHub; ou `termux-change-repo` |
| pip tenta compilar `cryptography` (erro de Rust) | usou pip em vez de pkg | `pkg install python-cryptography` |
| pip falha ao compilar `psutil` | headers do Android | `pkg install python-psutil` |
| `ModuleNotFoundError: cryptography` dentro de venv | venv não enxerga o pkg | `python -m venv --system-site-packages .venv` |
| `./termux-setup.sh: bad interpreter` | Android sem `/usr/bin/env` | rode `bash termux-setup.sh` |
| telethon com versão diferente | versão não fixada | `pip install telethon==1.45.0` |
| processo morre sozinho | bateria/background | `termux-wake-lock` + desativar otimização |
| `telecopy: command not found` | não instalado como pacote | use `python -m telecopy_cli ...` |
| clone falhou na ref | CLI 4.1 ainda fora da `main` | `TELECOPY_REF=arena/859ccdf0-telecopy` |

## iPhone (iOS)

Não é prático: o iOS não oferece terminal com pip e compilação nativa acessível, e
mata background agressivamente. Use o cenário de **servidor + celular como controle**.

## Limites deste guia (honestidade)

- O script foi **validado em Linux** (sintaxe, shellcheck, ajuda, guarda de
  ambiente, verificação automática e caminhos de falha). **A execução em um
  aparelho Android real ainda não foi testada.**
- Os nomes de pacotes do Termux e as versões mudam com o tempo; se o `pkg` não
  encontrar um pacote, o script tenta o fallback de compilação e explica a falha.
- O TELECOPY como um todo **ainda não foi validado contra o Telegram real**
  (ver `docs/RELATORIO_FINAL.md`). Instalação verificada não é cópia verificada.
