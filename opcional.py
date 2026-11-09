"""Recursos opcionais (P1): proxy, janela, banda, webhook, arquivo offline, UI local.

Nenhum recurso aqui é ligado por padrão. Rewriting/proxy/notificação exigem
flags explícitas; a UI local é somente leitura e protegida por token.
"""
from __future__ import annotations

import hmac
import html
import json
import secrets
import threading
import urllib.parse
import urllib.request
from datetime import datetime, time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from i18n import t
from utils import logger


def parse_proxy(url: str) -> Dict[str, Any]:
    """Converte URL socks5/socks4/http no formato de dict aceito pelo Telethon."""
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"socks5", "socks4", "http"}:
        raise ValueError(t("proxy.invalid", scheme=scheme or "?"))
    if not parsed.hostname or not parsed.port:
        raise ValueError(t("proxy.invalid", scheme=scheme))
    return {
        "proxy_type": scheme,
        "addr": parsed.hostname,
        "port": int(parsed.port),
        "rdns": True,
        "username": parsed.username,
        "password": parsed.password,
    }


def _parse_hhmm(value: str) -> dtime:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(value)
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(value)
    return dtime(hour, minute)


def dentro_janela(agora: datetime, inicio: Optional[str], fim: Optional[str]) -> bool:
    if not inicio and not fim:
        return True
    if not inicio or not fim:
        raise ValueError(t("schedule.incomplete"))
    start, end = _parse_hhmm(inicio), _parse_hhmm(fim)
    now = agora.time()
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def segundos_ate_janela(agora: datetime, inicio: Optional[str], fim: Optional[str]) -> float:
    if dentro_janela(agora, inicio, fim):
        return 0.0
    start = _parse_hhmm(inicio or "")
    target = agora.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if target <= agora:
        from datetime import timedelta
        target += timedelta(days=1)
    return max(0.0, (target - agora).total_seconds())


def reescrever_texto(texto: str, de: str, para: str) -> str:
    if not de:
        return texto
    return texto.replace(de, para)


def notificar_webhook(url: str, payload: Dict[str, Any], timeout: float = 5.0) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        logger.warning(t("notify.invalid_url"))
        return False
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 300
    except Exception as exc:
        logger.warning(t("notify.failed", reason=type(exc).__name__))
        return False


async def arquivar(client: Any, source: Any, destino: Path, *,
                   limite: Optional[int] = None, topico: Optional[int] = None) -> Dict[str, Any]:
    """Exportação offline somente leitura: manifesto JSONL + mídia baixada."""
    from copier import is_service, media_info

    destino = Path(destino)
    media_dir = destino / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    manifest = destino / "manifest.jsonl"
    total = midias = 0
    kwargs: Dict[str, Any] = {"reverse": True}
    if topico:
        kwargs["reply_to"] = int(topico)
    with manifest.open("w", encoding="utf-8") as fh:
        async for msg in client.iter_messages(source, **kwargs):
            if limite is not None and total >= int(limite):
                break
            total += 1
            has_media, media_type, size = media_info(msg)
            row: Dict[str, Any] = {
                "id": int(msg.id),
                "date": getattr(msg, "date", None).isoformat() if getattr(msg, "date", None) else None,
                "text": getattr(msg, "message", "") or "",
                "service": is_service(msg),
                "media_type": media_type if has_media else None,
                "size": size,
                "file": None,
            }
            if has_media and not is_service(msg):
                target = media_dir / f"{int(msg.id)}"
                try:
                    path = await client.download_media(msg, file=str(target))
                    if path:
                        row["file"] = str(Path(path).relative_to(destino))
                        midias += 1
                except Exception as exc:
                    row["error"] = type(exc).__name__
                    logger.warning(t("archive.media_failed", id=int(msg.id), reason=type(exc).__name__))
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"total": total, "midias": midias, "manifest": str(manifest), "dir": str(destino)}


class _UIHandler(BaseHTTPRequestHandler):
    server_version = "TelecopyUI/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _authorized(self) -> bool:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        expected = getattr(self.server, "telecopy_token", "")
        return bool(expected) and hmac.compare_digest(token, expected)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(401, t("ui.unauthorized").encode("utf-8"), "text/plain; charset=utf-8")
            return
        parsed = urllib.parse.urlparse(self.path)
        report_dir: Path = getattr(self.server, "telecopy_reports")
        if parsed.path in {"/", "/index.html"}:
            reports = sorted(report_dir.glob("relatorio-*.html"))
            links = "".join(
                f'<li><a href="/relatorio?file={html.escape(p.name)}&token={html.escape(getattr(self.server, "telecopy_token", ""))}">{html.escape(p.name)}</a></li>'
                for p in reports[-20:]
            )
            body = f"""<!doctype html><meta charset="utf-8"><title>{html.escape(t('ui.title'))}</title>
<h1>{html.escape(t('ui.title'))}</h1><p>{html.escape(t('ui.readonly'))}</p><ul>{links}</ul>"""
            self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/relatorio":
            qs = urllib.parse.parse_qs(parsed.query)
            name = (qs.get("file") or [""])[0]
            target = (report_dir / name).resolve()
            if not name or target.parent != report_dir.resolve() or not target.is_file():
                self._send(404, b"not found", "text/plain")
                return
            ctype = "text/html; charset=utf-8" if target.suffix == ".html" else "application/json"
            self._send(200, target.read_bytes(), ctype)
            return
        if parsed.path == "/health":
            self._send(200, json.dumps({"ok": True, "readonly": True}).encode(), "application/json")
            return
        self._send(404, b"not found", "text/plain")


def criar_servidor_ui(report_dir: Path, token: str, host: str = "127.0.0.1",
                      port: int = 8765) -> ThreadingHTTPServer:
    if not token:
        raise ValueError(t("ui.token_required"))
    server = ThreadingHTTPServer((host, port), _UIHandler)
    server.telecopy_token = token  # type: ignore[attr-defined]
    server.telecopy_reports = Path(report_dir)  # type: ignore[attr-defined]
    return server


def servir_ui(report_dir: Path, token: Optional[str], host: str, port: int) -> int:
    token = token or secrets.token_urlsafe(24)
    server = criar_servidor_ui(report_dir, token, host, port)
    print(t("ui.listening", host=host, port=server.server_address[1], token=token))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0
