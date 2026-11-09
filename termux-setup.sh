#!/usr/bin/env bash
#
# termux-setup.sh — instala e verifica o TELECOPY no Termux (Android).
#
# Uso (dentro do Termux):
#     bash termux-setup.sh
#
# Um comando, sem clonar antes:
#     bash <(curl -fsSL https://raw.githubusercontent.com/montx2/telecopy/main/termux-setup.sh)
#
# Opções:
#     --verify-only    só verifica a instalação atual (não instala nada)
#     --no-upgrade     pula o `pkg upgrade` (mais rápido)
#     --with-cryptg    tenta instalar cryptg (opcional; acelera a criptografia)
#     --dir PATH       diretório do projeto (padrão: ./telecopy ou o repo atual)
#     --ref REF        branch/tag ao clonar (padrão: main)
#     --help           mostra esta ajuda
#
# Variáveis de ambiente:
#     TELECOPY_REF          equivale a --ref
#     TELECOPY_DIR          equivale a --dir
#     TELECOPY_REPO         URL do git (padrão: repositório oficial)
#     TELECOPY_SETUP_FORCE  =1 permite rodar fora do Termux (só para teste/CI)
#     NO_COLOR              desliga cores
#
# Por que este script existe: no Termux, `pip install cryptography` e
# `pip install psutil` normalmente FALHAM (Rust / headers do Android). O caminho
# confiável é instalar as versões prontas via `pkg` e usar pip apenas para o que
# é Python puro. Por isso NÃO rode `pip install -r requirements.txt` no Termux.
#
# O script é idempotente: pode rodar de novo sem quebrar nada.

set -euo pipefail

# ── Aparência / log ──────────────────────────────────────────────────────────

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_OK=$'\033[32m'; C_ERR=$'\033[31m'; C_WARN=$'\033[33m'
    C_INFO=$'\033[36m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_OK=""; C_ERR=""; C_WARN=""; C_INFO=""; C_DIM=""; C_OFF=""
fi

STEP=0
step() { STEP=$((STEP + 1)); printf '\n%s[%d/%d]%s %s\n' "$C_INFO" "$STEP" "$TOTAL" "$C_OFF" "$1"; }
ok()   { printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$1"; }
warn() { printf '  %s!%s %s\n' "$C_WARN" "$C_OFF" "$1"; }
err()  { printf '  %s✗%s %s\n' "$C_ERR" "$C_OFF" "$1" >&2; }
dim()  { printf '    %s%s%s\n' "$C_DIM" "$1" "$C_OFF"; }
die()  { err "$1"; exit "${2:-1}"; }

TOTAL=6

# ── Argumentos ───────────────────────────────────────────────────────────────

VERIFY_ONLY=0
DO_UPGRADE=1
WITH_CRYPTG=0
PROJECT_DIR="${TELECOPY_DIR:-}"
GIT_REF="${TELECOPY_REF:-main}"
GIT_REPO="${TELECOPY_REPO:-https://github.com/montx2/telecopy.git}"

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --verify-only) VERIFY_ONLY=1; TOTAL=3; shift ;;
        --no-upgrade)  DO_UPGRADE=0; shift ;;
        --with-cryptg) WITH_CRYPTG=1; shift ;;
        --dir)  PROJECT_DIR="${2:-}"; [[ -n "$PROJECT_DIR" ]] || die "--dir exige um caminho" 2; shift 2 ;;
        --ref)  GIT_REF="${2:-}";     [[ -n "$GIT_REF" ]]     || die "--ref exige um valor" 2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "opção desconhecida: $1 (use --help)" 2 ;;
    esac
done

# ── Ambiente ────────────────────────────────────────────────────────────────

is_termux() { [[ "${PREFIX:-}" == *com.termux* ]] && [[ -d "${PREFIX:-/nonexistent}" ]]; }

IN_TERMUX=0
if is_termux; then
    IN_TERMUX=1
elif [[ "${TELECOPY_SETUP_FORCE:-0}" != "1" ]]; then
    err "Este script foi feito para o Termux (Android)."
    dim "No PC use:  python -m pip install -r requirements-dev.txt -e '.[fast,socks]'"
    dim "Para testar a lógica fora do Termux: TELECOPY_SETUP_FORCE=1 bash $0 --verify-only"
    exit 2
fi

# O shebang /usr/bin/env não existe no Android; corrige para execução direta.
if [[ "$IN_TERMUX" == "1" ]] && command -v termux-fix-shebang >/dev/null 2>&1; then
    termux-fix-shebang "$0" >/dev/null 2>&1 || true
fi

printf '%sTELECOPY — instalação no Termux%s\n' "$C_INFO" "$C_OFF"
[[ "$IN_TERMUX" == "1" ]] || warn "Fora do Termux (modo de teste): etapas de pkg serão puladas."

# Aviso sobre Termux da Play Store (abandonado, repositórios quebrados).
if [[ "$IN_TERMUX" == "1" ]]; then
    tv="${TERMUX_VERSION:-}"
    if [[ -n "$tv" ]] && [[ "$tv" == 0.1* || "$tv" == "0.101" ]]; then
        warn "Termux $tv parece ser a versão antiga da Play Store."
        dim "Instale pelo F-Droid ou GitHub oficial, senão o pkg pode falhar."
    fi
fi

# ── Helpers ──────────────────────────────────────────────────────────────────

PY=""
pick_python() {
    for cand in python python3; do
        if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; return 0; fi
    done
    return 1
}

has_module() { [[ -n "$PY" ]] && "$PY" -c "import $1" >/dev/null 2>&1; }

pkg_install() {
    # noninteractive + confold: não trava pedindo confirmação de config.
    DEBIAN_FRONTEND=noninteractive pkg install -y \
        -o Dpkg::Options::=--force-confold "$@"
}

# Instala um módulo nativo: pkg primeiro, compilação como fallback.
ensure_native() {
    local module="$1" pkgname="$2"
    if has_module "$module"; then
        ok "$module já disponível"
        return 0
    fi
    if [[ "$IN_TERMUX" == "1" ]]; then
        dim "tentando pkg install $pkgname (caminho confiável)"
        if pkg_install "$pkgname" && has_module "$module"; then
            ok "$module instalado via pkg"
            return 0
        fi
        warn "pkg install $pkgname não resolveu; tentando compilar via pip"
    fi
    build_native_fallback "$module"
}

build_native_fallback() {
    local module="$1"
    [[ "$IN_TERMUX" == "1" ]] || { err "$module ausente (fora do Termux, instale manualmente)"; return 1; }
    if [[ "$module" == "cryptography" ]]; then
        dim "instalando toolchain Rust (pode demorar)"
        pkg_install rust binutils libffi openssl libcrypt >/dev/null 2>&1 || \
            pkg_install rust binutils libffi openssl || true
        local target
        target="$(rustc -Vv 2>/dev/null | awk '/^host:/ {print $2}')" || target=""
        if [[ -n "$target" ]]; then
            CARGO_BUILD_TARGET="$target" "$PY" -m pip install --no-cache-dir cryptography && \
                { ok "$module compilado via pip"; return 0; }
        else
            "$PY" -m pip install --no-cache-dir cryptography && \
                { ok "$module compilado via pip"; return 0; }
        fi
    else
        dim "instalando toolchain C"
        pkg_install clang make libandroid-execinfo >/dev/null 2>&1 || pkg_install clang make || true
        "$PY" -m pip install --no-cache-dir "$module" && { ok "$module compilado via pip"; return 0; }
    fi
    err "não foi possível instalar $module"
    return 1
}

# pip para pacotes Python puro; se der erro de permissão, tenta venv.
VENV_DIR=""
pip_pure() {
    "$PY" -m pip install --upgrade "$@" && return 0
    warn "pip global falhou; tentando venv com --system-site-packages"
    dim "(o venv precisa enxergar cryptography/psutil instalados via pkg)"
    local venv="${PROJECT_DIR:-$PWD}/.venv-termux"
    "$PY" -m venv --system-site-packages "$venv" || return 1
    # shellcheck disable=SC1091
    source "$venv/bin/activate"
    VENV_DIR="$venv"
    PY="$venv/bin/python"
    "$PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$PY" -m pip install --upgrade "$@"
}

# ── Etapa 1: pacotes do sistema ──────────────────────────────────────────────

if [[ "$VERIFY_ONLY" == "0" ]]; then
    step "Base do Termux (python, git)"
    if [[ "$IN_TERMUX" == "1" ]]; then
        if ! pkg update -y; then
            err "pkg update falhou."
            dim "Causa comum: Termux da Play Store. Troque pelo F-Droid/GitHub oficial."
            dim "Ou escolha outro espelho:  termux-change-repo"
            die "não foi possível atualizar os repositórios" 1
        fi
        if [[ "$DO_UPGRADE" == "1" ]]; then
            DEBIAN_FRONTEND=noninteractive pkg upgrade -y \
                -o Dpkg::Options::=--force-confold || warn "pkg upgrade falhou; seguindo"
        fi
        pkg_install python git || die "não foi possível instalar python/git" 1
        command -v termux-wake-lock >/dev/null 2>&1 || pkg_install termux-api >/dev/null 2>&1 || true
    else
        warn "pulando pkg (fora do Termux)"
    fi
    pick_python || die "python não encontrado no PATH" 1
    ok "python: $("$PY" --version 2>&1)"

    # ── Etapa 2: dependências nativas ────────────────────────────────────────
    step "Dependências nativas via pkg (cryptography, psutil)"
    ensure_native cryptography python-cryptography || die \
        "cryptography é obrigatória (cofre de credenciais). Veja docs/TERMUX.md" 1
    ensure_native psutil python-psutil || die \
        "psutil é obrigatório (perfil de hardware). Veja docs/TERMUX.md" 1

    # ── Etapa 3: dependências Python puro ────────────────────────────────────
    step "Dependências Python puro via pip (telethon, rich)"
    "$PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
    pip_pure "telethon==1.45.0" "rich>=13.7.0" || die "falha ao instalar telethon/rich" 1
    ok "telethon e rich instalados"
    if [[ "$WITH_CRYPTG" == "1" ]]; then
        dim "cryptg é opcional; se falhar, o app funciona mais devagar"
        if "$PY" -m pip install cryptg; then ok "cryptg instalado"; else warn "cryptg não instalou (opcional)"; fi
    fi

    # ── Etapa 4: código do projeto ───────────────────────────────────────────
    step "Código do TELECOPY"
    if [[ -z "$PROJECT_DIR" ]]; then
        if [[ -f "$PWD/main.py" && -f "$PWD/telecopy_cli.py" ]]; then
            PROJECT_DIR="$PWD"
            dim "usando o repositório atual: $PROJECT_DIR"
        else
            PROJECT_DIR="$PWD/telecopy"
        fi
    fi
    if [[ -f "$PROJECT_DIR/main.py" && -f "$PROJECT_DIR/telecopy_cli.py" ]]; then
        ok "projeto já presente em $PROJECT_DIR"
        if [[ -d "$PROJECT_DIR/.git" ]] && command -v git >/dev/null 2>&1; then
            if git -C "$PROJECT_DIR" pull --ff-only >/dev/null 2>&1; then
                ok "git pull (fast-forward)"
            else
                warn "git pull não aplicado (mudanças locais?); usando o código atual"
            fi
        fi
    else
        command -v git >/dev/null 2>&1 || die "git ausente" 1
        dim "clonando $GIT_REPO (ref: $GIT_REF)"
        if ! git clone --depth 1 -b "$GIT_REF" "$GIT_REPO" "$PROJECT_DIR"; then
            err "não foi possível clonar a ref '$GIT_REF'."
            dim "A CLI 4.1 pode ainda não estar na main. Tente:"
            dim "  TELECOPY_REF=arena/859ccdf0-telecopy bash $0"
            die "clone falhou" 1
        fi
        ok "clonado em $PROJECT_DIR"
    fi
    cd "$PROJECT_DIR"
else
    step "Localizando projeto e interpretador"
    pick_python || die "python não encontrado" 1
    if [[ -z "$PROJECT_DIR" ]]; then
        if [[ -f "$PWD/main.py" ]]; then PROJECT_DIR="$PWD"; else PROJECT_DIR="$PWD/telecopy"; fi
    fi
    [[ -f "$PROJECT_DIR/main.py" ]] || die "projeto não encontrado em $PROJECT_DIR (use --dir)" 1
    cd "$PROJECT_DIR"
    ok "projeto: $PROJECT_DIR"
fi

# ── Etapa 5: verificação automática ──────────────────────────────────────────

step "Verificação automática (imports + doctor)"

MISSING=()
for mod in telethon cryptography psutil; do
    if has_module "$mod"; then ok "import $mod"; else err "import $mod FALHOU"; MISSING+=("$mod"); fi
done
if has_module rich; then ok "import rich"; else warn "rich ausente (interface fica simples, mas funciona)"; fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
    err "módulos obrigatórios ausentes: ${MISSING[*]}"
    dim "Rode de novo:  bash $0"
    dim "Se estiver em venv, recrie com:  python -m venv --system-site-packages .venv"
    die "verificação de imports falhou" 1
fi

# Confere a versão do Telethon: o transporte rápido depende de APIs internas.
TV="$("$PY" -c 'import telethon; print(telethon.__version__)' 2>/dev/null || echo "?")"
if [[ "$TV" == "1.45.0" ]]; then
    ok "telethon $TV (versão fixada pelo projeto)"
else
    warn "telethon $TV — o projeto fixa 1.45.0; o caminho rápido pode cair no modo padrão"
    dim "corrija com:  $PY -m pip install telethon==1.45.0"
fi

# doctor --json é a verificação oficial do próprio app.
DOCTOR_JSON="$("$PY" -m telecopy_cli doctor --json 2>/dev/null || true)"
if [[ -z "$DOCTOR_JSON" ]]; then
    warn "doctor não respondeu; caindo para 'python main.py --check'"
    "$PY" main.py --check || die "a verificação do app falhou" 1
else
    printf '%s' "$DOCTOR_JSON" | "$PY" -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("  ✗ doctor não emitiu JSON válido"); sys.exit(3)
checks = d.get("checks", [])
# Rede é requisito de USO, não de INSTALAÇÃO: estar offline não invalida o setup.
REDE = "Telegram (rede)"
bad = [c for c in checks if c.get("status") == "fail" and c.get("check") != REDE]
net = [c for c in checks if c.get("status") == "fail" and c.get("check") == REDE]
warns = [c for c in checks if c.get("status") == "warn"]
for c in bad:
    print("  ✗ %s: %s" % (c.get("check"), c.get("detail") or ""))
    if c.get("fix"):
        print("    → %s" % c["fix"])
for c in net:
    print("  ! %s: %s" % (c.get("check"), c.get("detail") or ""))
    print("    → instalação OK, mas sem acesso ao Telegram você não vai conseguir copiar.")
for c in warns:
    print("  ! %s: %s" % (c.get("check"), c.get("detail") or ""))
print("  ✓ doctor: %d checks, %d falha(s) de instalação, %d aviso(s)"
      % (len(checks), len(bad), len(warns) + len(net)))
sys.exit(1 if bad else 0)
' || die "o doctor apontou falhas (veja acima)" 1
fi

# ── Etapa 6: prontidão e próximos passos ─────────────────────────────────────

step "Pronto — próximos passos"

if [[ "$IN_TERMUX" == "1" ]]; then
    if command -v termux-wake-lock >/dev/null 2>&1; then
        if termux-wake-lock >/dev/null 2>&1; then
            ok "wake-lock ativado (evita que o Android durma o processo)"
        else
            warn "não consegui ativar o wake-lock; rode: termux-wake-lock"
        fi
    else
        warn "termux-wake-lock indisponível; instale o app Termux:API e 'pkg install termux-api'"
    fi
fi

printf '\n  %sInstalação verificada.%s Projeto em: %s\n' "$C_OK" "$C_OFF" "$PROJECT_DIR"
if [[ -n "$VENV_DIR" ]]; then
    printf '  %sAtive o venv antes de usar:%s  source %s/bin/activate\n' "$C_WARN" "$C_OFF" "$VENV_DIR"
fi

cat <<EOF

  1) Primeiro uso (login — pede api_id/api_hash de my.telegram.org):
       cd $PROJECT_DIR && $PY main.py

  2) Depois de logar, pode usar a CLI:
       $PY -m telecopy_cli clone --src @origem --dst @destino --perfil recomendado

  3) Só diagnosticar, sem instalar nada:
       bash $0 --verify-only

  ${C_WARN}Para cópias longas no celular:${C_OFF}
    · mantenha o Termux em primeiro plano e o aparelho na tomada
    · desative a otimização de bateria do Termux nas configurações do Android
    · use Wi-Fi (clonar canal grande consome muitos dados)
    · o Android pode matar o processo mesmo assim — para muitas horas,
      o robusto é rodar num servidor e usar o celular só como controle

  ${C_DIM}Este script foi validado em Linux; a execução em Android real ainda
  não foi testada. Guia e solução de problemas: docs/TERMUX.md${C_OFF}
EOF
