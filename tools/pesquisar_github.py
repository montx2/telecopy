"""Coleta evidências públicas do GitHub, sem importar código para o projeto.

Ferramenta de desenvolvimento: usa gh já autenticado e a biblioteca padrão.
Os documentos baixados ficam em data/pesquisa (ignorado); o recorte auditável
fica em docs/evidencias/pesquisa-github.json. Ausência de evidência não prova
ausência de uma funcionalidade. Não consulta credenciais nem faz upload.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
BRUTO = RAIZ / "data" / "pesquisa"
SAIDA = RAIZ / "docs" / "evidencias" / "pesquisa-github.json"
TERMOS = re.compile(r"flood|duplicate|\border\b|\bban\b|filereference|file_reference|resume|album|topic|forum|slow|memory|crash|windows|checkpoint|hash|license|restrict", re.I)
CANDIDATOS = [
    "aahnik/tgcf", "iyear/tdl", "knadh/tg-archive", "fabianonline/telegram_backup",
    "alfem/telegram-download-daemon", "klept0/Telegram-Forwarder-Bot-v2",
    "erfnzdeh/Telegram-CLI", "Illgot/Telegram-CLI", "tdlib/td",
    "LonamiWebs/Telethon", "pyrogram/pyrogram", "KurimuzonAkuma/pyrogram",
    "mautrix/telegram", "MiyukiKun/FastTelethonhelper", "Dineshkarthik/telegram_media_downloader",
    "Nekmo/telegram-upload", "expectocode/telegram-export",
    "danog/MadelineProto", "AbirHasan2005/Forward-Client", "EverythingSuckz/TG-FileStreamBot",
    "rfsbraz/telegram-downloader", "smilekevin/TGPorter", "odysseusmax/tg-index",
    "nktknshn/tgmount", "niubility6/telegram-backup", "SubhradeepSS/Telegram-Channel-Cloner",
    "colinshark/Telegram-Channel-Mirror", "GeiserX/Telegram-Archive",
    "botnick/telegram-media-downloader", "Normalizex/telegram-backup",
    "renkagod/tg-chat-dump", "warl0ckwraith/telegram-migrator",
    "xsukax/xsukax-Telegram-Backup", "plzhans/telegram-chat-exporter",
    "q909717714/TGArchiveManager", "N4rr34n6/TelegramBackup",
]


def api(caminho: str, *, busca: bool = False) -> Any:
    # O limite secundário independe da cota nominal. Não paralelizar consultas.
    time.sleep(2.2 if busca else 1.2)
    for tentativa in range(2):
        resultado = subprocess.run(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", caminho],
            capture_output=True, text=True, timeout=45,
        )
        if not resultado.returncode:
            return json.loads(resultado.stdout)
        if "rate limit" in resultado.stderr.lower() and tentativa == 0:
            print("Limite secundário do GitHub: aguardando 120 s antes de continuar.", flush=True)
            time.sleep(120)
            continue
        raise RuntimeError(resultado.stderr[:400])
    raise RuntimeError("consulta não concluída")


def contextos(texto: str, limite: int = 10) -> list[dict[str, Any]]:
    linhas = texto.splitlines()
    indices = [i for i, linha in enumerate(linhas) if TERMOS.search(linha)]
    # Distribui a amostra pelo arquivo, em vez de só cabeçalhos e imports.
    passo = max(1, len(indices) // limite)
    return [{"linha": i + 1, "texto": "\n".join(linhas[max(0, i - 2):i + 4])[:1200]}
            for i in indices[::passo][:limite]]


def coletar(nome: str) -> dict[str, Any]:
    print(f"Consultando {nome}", flush=True)
    pasta = BRUTO / nome.replace("/", "__")
    cache = pasta / "evidencias.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    meta = api(f"repos/{nome}")
    nome = meta["full_name"]
    pasta.mkdir(parents=True, exist_ok=True)
    # GitHub permite no máximo cinco operadores OR/AND/NOT por consulta.
    # Duas buscas complementares + issues recentes cobrem todos os termos.
    candidatos_issues: list[dict[str, Any]] = []
    for grupo in (["flood", "duplicate", "resume", "album", "Windows"],
                  ["FileReference", "memory", "forum", "crash", "slow"]):
        consulta = f"search/issues?q=repo:{nome}+is:issue+is:closed+{'+OR+'.join(grupo)}&per_page=10"
        try:
            candidatos_issues.extend(api(consulta, busca=True).get("items", []))
        except RuntimeError as exc:
            print(f"Issues fechadas indisponíveis em {nome}: {exc}", flush=True)
    try:
        recentes = api(f"repos/{nome}/issues?state=all&sort=updated&direction=desc&per_page=100")
        candidatos_issues.extend(x for x in recentes if "pull_request" not in x and TERMOS.search((x.get("title") or "") + " " + (x.get("body") or "")))
    except RuntimeError as exc:
        print(f"Issues recentes indisponíveis em {nome}: {exc}", flush=True)
    issues = []
    vistos: set[int] = set()
    for item in candidatos_issues[:36]:
        if item["number"] in vistos:
            continue
        vistos.add(item["number"])
        issues.append({k: item.get(k) for k in ("number", "title", "state", "html_url", "created_at", "closed_at", "comments")})
        issues[-1]["body"] = (item.get("body") or "")[:1800]
    try:
        doc = api(f"repos/{nome}/readme")
        readme = base64.b64decode(doc.get("content", "")).decode("utf-8", "replace")
    except RuntimeError:
        readme = ""
    (pasta / "README.md").write_text(readme, encoding="utf-8")
    try:
        snapshot = api(f"repos/{nome}/git/trees/{meta['default_branch']}?recursive=1")
        arvore = snapshot.get("tree", [])
        arvore_sha = snapshot.get("sha")
    except RuntimeError:
        arvore = []
        arvore_sha = None
    caminhos = [x["path"] for x in arvore if x["type"] == "blob"]
    fontes = [x for x in arvore if x["type"] == "blob" and x.get("size", 0) < 180000
              and Path(x["path"]).suffix in {".py", ".go", ".ts", ".js", ".java", ".cpp", ".cs", ".php", ".rs"}
              and not any(p in x["path"].lower() for p in ("vendor/", "node_modules/", "/test", "test_"))]
    fontes.sort(key=lambda x: sum(p in x["path"].lower() for p in ("forward", "clone", "download", "backup", "archive", "filemanager", "client", "main")) * 100000 - x.get("size", 0), reverse=True)
    trechos = []
    for entrada in fontes[:7]:
        try:
            doc = api(f"repos/{nome}/contents/{entrada['path']}?ref={meta['default_branch']}")
            texto = base64.b64decode(doc.get("content", "")).decode("utf-8", "replace")
        except (RuntimeError, ValueError):
            continue
        (pasta / entrada["path"].replace("/", "__")).write_text(texto, encoding="utf-8")
        amostra = contextos(texto)
        if amostra:
            trechos.append({"path": entrada["path"], "amostras": amostra})
    licenca = (meta.get("license") or {}).get("spdx_id") or "não declarada"
    try:
        doc = api(f"repos/{nome}/license")
        texto_licenca = base64.b64decode(doc.get("content", "")).decode("utf-8", "replace")
        (pasta / "LICENSE.txt").write_text(texto_licenca, encoding="utf-8")
    except RuntimeError:
        texto_licenca = ""
    resultado = {
        "repo": nome, "url": meta["html_url"], "linguagem": meta.get("language"),
        "licenca": licenca, "licenca_inicio": texto_licenca[:450],
        "atividade_push": meta["pushed_at"], "arquivado": meta["archived"],
        "branch": meta["default_branch"], "arvore_sha": arvore_sha,
        "descricao": meta.get("description"), "readme_url": meta["html_url"] + "/blob/" + meta["default_branch"] + "/README.md",
        "readme_amostras": contextos(readme, 12), "estrutura": caminhos[:220],
        "testes_na_estrutura": [p for p in caminhos if re.search(r"(^tests?/|test_|_test\.|\.spec\.)", p)][:40],
        "fontes": trechos, "issues": issues,
        "consultado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (pasta / "evidencias.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultado


def main() -> None:
    BRUTO.mkdir(parents=True, exist_ok=True)
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    achados = list(CANDIDATOS)
    for topico in ("telegram-cloner", "telegram-forward", "telegram-backup", "telegram-downloader"):
        try:
            busca = api(f"search/repositories?q=topic:{topico}&sort=stars&per_page=8", busca=True)
            achados.extend(x["full_name"] for x in busca.get("items", []))
        except RuntimeError as exc:
            print(f"Busca de tópico indisponível: {exc}", flush=True)
    resultados: list[dict[str, Any]] = []
    erros: list[dict[str, str]] = []
    for nome in dict.fromkeys(achados):
        try:
            resultados.append(coletar(nome))
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            erros.append({"repo": nome, "erro": str(exc)})
            print(f"Falha verificável em {nome}: {exc}", flush=True)
        SAIDA.write_text(json.dumps({"projetos": resultados, "falhas": erros}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Concluído: {len(resultados)} projetos; {len(erros)} falhas de consulta.", flush=True)


if __name__ == "__main__":
    main()
