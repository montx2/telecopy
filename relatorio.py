"""Relatório final HTML+JSON, sem dependências novas."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from i18n import t


def montar_relatorio(resultado: Dict[str, Any], *, perfil: Optional[Dict[str, Any]] = None,
                     verificacao: Optional[Dict[str, Any]] = None,
                     schema_version: int = 1) -> Dict[str, Any]:
    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "schema_version": schema_version,
        "perfil": perfil or {},
        "perfil_nota": t("report.profile_note"),
        "resultado": resultado,
        "verificacao": verificacao,
        "limitacoes": t("report.limitations_body"),
    }


def escrever_relatorio(relatorio: Dict[str, Any], destino: Path) -> Dict[str, Path]:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = destino / f"relatorio-{when}.json"
    html_path = destino / f"relatorio-{when}.html"
    json_path.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    def _cell(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return f"<pre>{html.escape(json.dumps(value, ensure_ascii=False))}</pre>"
        return html.escape(str(value))

    rows = "".join(
        f"<tr><th>{html.escape(str(k))}</th><td>{_cell(v)}</td></tr>"
        for k, v in (relatorio.get("resultado") or {}).items()
    )
    verify = relatorio.get("verificacao") or {}
    vrows = "".join(
        f"<tr><th>{html.escape(str(k))}</th><td>{_cell(v)}</td></tr>"
        for k, v in verify.items()
    )
    body = f"""<!doctype html>
<html lang="pt-BR"><meta charset="utf-8"><title>{html.escape(t('report.title'))}</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;max-width:900px}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:.4rem;text-align:left}}
th{{width:30%;background:#f5f5f5}}.note{{color:#555}}</style>
<h1>{html.escape(t('report.title'))}</h1>
<p>{html.escape(t('report.generated', when=relatorio.get('gerado_em', '')))}</p>
<h2>{html.escape(t('report.profile'))}</h2><p class="note">{html.escape(relatorio.get('perfil_nota', ''))}</p>
<pre>{html.escape(json.dumps(relatorio.get('perfil') or {}, ensure_ascii=False, indent=2))}</pre>
<h2>{html.escape(t('report.result'))}</h2><table>{rows}</table>
<h2>{html.escape(t('report.verification'))}</h2><table>{vrows}</table>
<h2>{html.escape(t('report.limitations'))}</h2><p>{html.escape(relatorio.get('limitacoes', ''))}</p>
</html>"""
    html_path.write_text(body, encoding="utf-8")
    return {"json": json_path, "html": html_path}
