"""Verificação origem→destino e reparos apenas quando confirmados.

Não apaga nem reenvia mensagens. Um reparo só grava checkpoint quando há
intenção durável e correspondência única no destino; ambiguidade vira pendência.
A origem é percorrida por inteiro por padrão: checkpoint ausente também é
pendência, não sucesso silencioso. ``limite`` torna a leitura parcial e o
resultado nunca é marcado como completo nesse modo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from copier import Copier, is_service, iter_peer_messages
from filters import apply as filter_apply
from database import Repository, Status
from i18n import t


@dataclass
class ResultadoVerificacao:
    source_id: int
    dest_id: int
    verificados: int = 0
    ok: int = 0
    ausentes: List[int] = field(default_factory=list)
    ambiguos: List[int] = field(default_factory=list)
    divergentes: List[int] = field(default_factory=list)
    ignorados: List[int] = field(default_factory=list)
    reparados: List[int] = field(default_factory=list)
    ordem_ok: bool = True
    parcial: bool = False
    detalhes: List[Dict[str, Any]] = field(default_factory=list)

    def como_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id, "dest_id": self.dest_id,
            "verificados": self.verificados, "ok": self.ok,
            "ausentes": self.ausentes, "ambiguos": self.ambiguos,
            "divergentes": self.divergentes, "ignorados": self.ignorados,
            "reparados": self.reparados, "ordem_ok": self.ordem_ok,
            "parcial": self.parcial, "detalhes": self.detalhes,
            "complete": (not self.ausentes and not self.ambiguos
                         and not self.divergentes and self.ordem_ok
                         and not self.parcial),
        }


async def _historico_destino(client: Any, dest: Any, topic: Optional[int],
                             limite: Optional[int] = None) -> List[Any]:
    kwargs: Dict[str, Any] = {}
    if limite is not None:
        kwargs["limit"] = int(limite)
    if topic:
        kwargs["reply_to"] = int(topic)
    return [msg async for msg in client.iter_messages(dest, **kwargs)]


async def _buscar_ids(client: Any, dest: Any, ids: List[int]) -> Dict[int, Any]:
    if not ids:
        return {}
    found = await client.get_messages(dest, ids=sorted(set(ids)))
    if found is None:
        return {}
    if not isinstance(found, list):
        found = [found]
    return {int(getattr(m, "id", 0)): m for m in found if m is not None}


def _assinatura_destino(msg: Any) -> str:
    texto = getattr(msg, "message", "") or ""
    media = getattr(msg, "media", None)
    doc = getattr(media, "document", None)
    photo = getattr(media, "photo", None)
    identidade = "none"
    if doc is not None:
        identidade = f"doc:{getattr(doc, 'id', 0)}:{getattr(doc, 'size', 0)}"
    elif photo is not None:
        identidade = f"photo:{getattr(photo, 'id', 0)}"
    return f"{identidade}|{texto.strip()}"


async def verificar_par(client: Any, repo: Repository, source: Any, dest: Any,
                        *, reparar: bool = False, limite: Optional[int] = None,
                        max_messages: Optional[int] = None,
                        filter_code: str = "all",
                        source_topic: Optional[int] = None,
                        dest_topic: Optional[int] = None,
                        topic_map: Optional[Dict[int, int]] = None,
                        topic_order: Optional[Sequence[int]] = None) -> ResultadoVerificacao:
    """Compara a origem com checkpoints e com o destino real.

    ``reparar=True`` só confirma vínculos ausentes quando existe intenção
    durável e a correspondência no destino é única. Nunca cria mensagem nem
    remove duplicata remotamente. ``max_messages`` restringe a origem (ensaio).
    """
    source_id = int(getattr(source, "id", 0) or 0)
    dest_id = int(getattr(dest, "id", 0) or 0)
    resultado = ResultadoVerificacao(source_id, dest_id)
    if limite is not None:
        resultado.parcial = True

    rows = {int(row["msg_id"]): row for row in repo.checkpoint_rows(source_id, dest_id)}
    source_messages: List[Any] = []
    if topic_map:
        collector = Copier(client, repo=repo, rich_ok=False)
        collected = await collector._collect(
            source, filter_code, source_topic, topic_map, topic_order=topic_order
        )
        for msg in collected:
            source_messages.append(msg)
            if max_messages is not None and len(source_messages) >= int(max_messages):
                break
            if limite is not None and len(source_messages) >= int(limite):
                break
    else:
        async for msg in iter_peer_messages(client, source, topic_id=source_topic):
            if is_service(msg) or not filter_apply(filter_code, msg):
                continue
            source_messages.append(msg)
            if max_messages is not None and len(source_messages) >= int(max_messages):
                break
            if limite is not None and len(source_messages) >= int(limite):
                break

    known_ids = [int(row["dest_msg_id"]) for row in rows.values()
                 if row.get("status") == Status.DONE.value and row.get("dest_msg_id")]
    remote_by_id = await _buscar_ids(client, dest, known_ids)
    history: Dict[Any, List[Any]] = {}

    async def candidates(topic: Any) -> List[Any]:
        if topic not in history:
            history[topic] = await _historico_destino(client, dest, topic, limite)
        return history[topic]

    last_by_topic: Dict[Any, int] = {}
    roots = set(topic_map or {})

    def note_order(msg_id: int, topic: Any, remote_id: int) -> None:
        if remote_id <= last_by_topic.get(topic, 0):
            resultado.ordem_ok = False
            resultado.detalhes.append({"msg_id": msg_id, "motivo": t("verify.order")})
        last_by_topic[topic] = max(last_by_topic.get(topic, 0), remote_id)

    for msg in source_messages:
        resultado.verificados += 1
        msg_id = int(msg.id)
        row = rows.get(msg_id)
        status = row.get("status") if row else None
        mapped_topic = None
        if topic_map:
            src_tid = Copier._source_topic_id(msg, roots) or 1
            mapped_topic = topic_map.get(src_tid)
        topic = (row.get("dest_topic") if row else None) or mapped_topic or dest_topic
        if status == Status.SKIPPED.value:
            resultado.ignorados.append(msg_id)
            resultado.detalhes.append({"msg_id": msg_id, "motivo": t("verify.skipped")})
            continue
        dest_msg_id = row.get("dest_msg_id") if row else None
        if status == Status.DONE.value and dest_msg_id:
            remote = remote_by_id.get(int(dest_msg_id))
            if remote is None:
                resultado.ausentes.append(msg_id)
                resultado.detalhes.append({"msg_id": msg_id, "motivo": t("verify.missing")})
                continue
            intent = repo.intent_for(source_id, dest_id, msg_id, 0)
            if intent.get("fingerprint") and Copier._destination_fingerprint(remote, intent) != intent["fingerprint"]:
                resultado.divergentes.append(msg_id)
                resultado.detalhes.append({"msg_id": msg_id, "motivo": t("verify.divergent")})
                continue
            resultado.ok += 1
            note_order(msg_id, topic, int(dest_msg_id))
            continue

        intent = repo.intent_for(source_id, dest_id, msg_id, 0)
        fingerprint = intent.get("fingerprint")
        matches: List[Any] = []
        if fingerprint:
            matches = [m for m in await candidates(topic)
                       if Copier._destination_fingerprint(m, intent) == fingerprint]
        if len(matches) == 1:
            remote_id = int(matches[0].id)
            resultado.ok += 1
            if reparar:
                repo.upsert_checkpoint(source_id, dest_id, msg_id, Status.DONE,
                                       dest_msg_id=remote_id, dest_topic=topic)
                repo.confirm_intent(source_id, dest_id, msg_id, 0, remote_id)
                resultado.reparados.append(msg_id)
            note_order(msg_id, topic, remote_id)
        elif len(matches) > 1:
            resultado.ambiguos.append(msg_id)
            resultado.detalhes.append({"msg_id": msg_id, "motivo": t("verify.ambiguous")})
        else:
            resultado.ausentes.append(msg_id)
            motivo = t("verify.missing") if fingerprint else t("verify.no_checkpoint")
            resultado.detalhes.append({"msg_id": msg_id, "motivo": motivo})
    return resultado
