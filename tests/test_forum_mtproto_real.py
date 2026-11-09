"""Testes realistas de clonagem de fórum de curso com semântica MTProto real.

Cobre especificamente os defeitos que não apareciam no ``FakeClient`` simples:
1. Paginação real de ``GetRepliesRequest`` / ``GetHistoryRequest`` em tópicos com
   mais de 100 mensagens (onde ``iter_messages(..., reverse=True)`` do Telethon
   truncava o último bloco por ``add_offset=-100``, derrubando 110 msgs para 84).
2. Paginação e ordenação de ``GetForumTopicsRequest`` usando a data real de
   ``top_message`` em ``response.messages`` (e não ``ForumTopic.date``, que no
   MTProto é a data de criação do tópico).
3. Replies aninhadas em tópicos sem ``reply_to_top_id`` (não podem vazar para o
   General) e mensagens órfãs de tópicos apagados na origem (não podem derrubar
   a cópia nem cair no General).
4. Cópia de curso com >1.000 mensagens, múltiplos módulos, vídeos grandes +
   álbuns de PDFs com tempos de download assimétricos e textos repetidos entre
   módulos: sequência 100% idêntica em cada tópico, sem intercalar módulos e
   sem teto artificial de 1.000 mensagens.
5. Proteção de retomada contra inversão ``29 -> 28`` após ``skipped`` antigo e
   retomada parcial de álbum sem reenviar partes já concluídas.
6. Fluxo CLI (``telecopy_cli``) clonando e verificando fórum completo.
"""

from __future__ import annotations

import asyncio
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="telecopy_mtproto_forum_")

import config  # noqa: E402

config.DATA_DIR = Path(_TMP)
config.TEMP_DIR = config.DATA_DIR / "tmp"
config.DB_FILE = config.DATA_DIR / "mtproto_forum.db"
config.STATS_FILE = config.DATA_DIR / "stats.json"
config.DAILY_FILE = config.DATA_DIR / "daily.json"
config.init_dirs()

import copier as cm  # noqa: E402
from channel_clone import ChannelCloner  # noqa: E402
from config import DAILY_LIMIT, RunConfig  # noqa: E402
from copier import Copier  # noqa: E402
from database import Repository, Status  # noqa: E402
from i18n import get_lang, set_lang  # noqa: E402
from telethon.tl import types as tl  # noqa: E402
from utils import DailyLimit  # noqa: E402
from verificacao import verificar_par  # noqa: E402
from tests.fake_telegram import (  # noqa: E402
    FakeClient,
    FakeMessage,
    channel,
    document_message,
    text_message,
    video_message,
)

BASE_TIME = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _topic_create_action(title: str) -> Any:
    return tl.MessageActionTopicCreate(title=title, icon_color=0x6FB9F0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    prev_lang = get_lang()
    set_lang("pt")
    temp = tmp_path / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "TEMP_DIR", temp)
    monkeypatch.setattr(cm, "TEMP_DIR", temp)
    limit = DailyLimit(tmp_path / "daily.json")
    monkeypatch.setattr(cm, "daily_limit", limit)
    repo = Repository(tmp_path / "repo.db")
    try:
        yield SimpleNamespace(repo=repo, limit=limit, temp=temp, root=tmp_path)
    finally:
        repo.close()
        set_lang(prev_lang)


class RealisticMTProtoForumClient(FakeClient):
    """Simula fielmente ``GetRepliesRequest``, ``GetHistoryRequest`` e ``GetForumTopicsRequest`` do Telegram."""

    _raw_mtproto_history = True

    def __init__(
        self,
        messages: List[FakeMessage],
        *,
        source_peer: Any,
        topics_meta: List[Dict[str, Any]],
        thread_membership: Dict[int, List[int]],
        download_jitter: Optional[Dict[int, float]] = None,
    ) -> None:
        super().__init__(messages)
        self.source_peer = source_peer
        self.register_entity(source_peer)
        self._by_id: Dict[int, FakeMessage] = {int(m.id): m for m in messages}
        self._threads: Dict[int, List[FakeMessage]] = {
            int(tid): [self._by_id[mid] for mid in sorted(mids) if mid in self._by_id]
            for tid, mids in thread_membership.items()
        }
        self._download_jitter = download_jitter or {}
        self.catalog: Dict[int, List[Any]] = {int(source_peer.id): self._build_source_topics(topics_meta)}
        self.top_messages_map: Dict[int, Dict[int, Any]] = {
            int(source_peer.id): self._build_top_messages(topics_meta)
        }
        self._next_dest_topic_id = 500
        self.created_channels: List[Any] = []
        self.created_topics: List[Any] = []
        self.pinned_orders: Dict[int, List[int]] = {}
        self.mtproto_history_calls = 0
        self.mtproto_replies_calls: Dict[int, int] = {}

    def _build_source_topics(self, topics_meta: List[Dict[str, Any]]) -> List[Any]:
        out = []
        for meta in topics_meta:
            out.append(
                SimpleNamespace(
                    id=int(meta["id"]),
                    title=str(meta["title"]),
                    icon_color=meta.get("icon_color", 0x6FB9F0),
                    icon_emoji_id=meta.get("icon_emoji_id"),
                    title_missing=False,
                    closed=bool(meta.get("closed", False)),
                    pinned=bool(meta.get("pinned", False)),
                    hidden=bool(meta.get("hidden", False)),
                    # No MTProto real, ForumTopic.date é a DATA DE CRIAÇÃO do tópico!
                    date=meta.get("created_date", BASE_TIME),
                    top_message=int(meta.get("top_message", meta["id"])),
                )
            )
        return out

    def _build_top_messages(self, topics_meta: List[Dict[str, Any]]) -> Dict[int, Any]:
        result: Dict[int, Any] = {}
        for meta in topics_meta:
            top_id = int(meta.get("top_message", meta["id"]))
            act_date = meta.get("activity_date") or (
                self._by_id[top_id].date if top_id in self._by_id else meta.get("created_date", BASE_TIME)
            )
            result[top_id] = SimpleNamespace(id=top_id, date=act_date)
        return result

    @staticmethod
    def _mtproto_slice(
        ordered_asc: List[FakeMessage],
        *,
        offset_id: int,
        add_offset: int,
        limit: int,
    ) -> List[FakeMessage]:
        """Semântica real do servidor Telegram para offsets em mensagens ordenadas decrescentemente."""
        desc = list(reversed(ordered_asc))  # [newest ... oldest]
        if offset_id <= 0:
            anchor = 0
        else:
            anchor = len(desc)
            for idx, item in enumerate(desc):
                if int(item.id) < int(offset_id):
                    anchor = idx
                    break
        start = anchor + int(add_offset)
        if start < 0:
            # Comportamento real do servidor Telegram que causava o bug do reverse=True:
            # quando add_offset=-100 ultrapassa o topo, start faz clamp em 0 e devolve
            # até `limit` mensagens sobrepostas já vistas!
            start = 0
        return desc[start : start + int(limit)]

    async def download_media(self, message, file=None, progress_callback=None):
        delay = self._download_jitter.get(int(message.id), 0.0)
        if delay > 0:
            await asyncio.sleep(delay)
        return await super().download_media(message, file=file, progress_callback=progress_callback)

    def _bump_dest_topic(self, peer_id: int, topic_id: Optional[int], msg_id: int) -> None:
        tid = int(topic_id) if topic_id else 1
        topics = self.catalog.get(peer_id)
        if not topics:
            return
        for tp in topics:
            if int(tp.id) == tid:
                tp.top_message = int(msg_id)
                self.top_messages_map.setdefault(peer_id, {})[int(msg_id)] = SimpleNamespace(
                    id=int(msg_id), date=BASE_TIME + timedelta(seconds=int(msg_id))
                )
                break
        self._resort_dest_catalog(peer_id)

    def _resort_dest_catalog(self, peer_id: int) -> None:
        topics = self.catalog.get(peer_id)
        if not topics:
            return
        pin_order = self.pinned_orders.get(peer_id, [])
        pinned = [tp for tp in topics if getattr(tp, "pinned", False)]
        if pin_order:
            rank = {tid: idx for idx, tid in enumerate(pin_order)}
            pinned.sort(key=lambda tp: rank.get(int(tp.id), 9999))
        unpinned = [tp for tp in topics if not getattr(tp, "pinned", False)]
        unpinned.sort(key=lambda tp: int(getattr(tp, "top_message", 0) or 0), reverse=True)
        self.catalog[peer_id] = pinned + unpinned

    def _record_send(self, entity, kind, **data):
        sent_msg = super()._record_send(entity, kind, **data)
        peer_id = int(getattr(entity, "id", 0) or 0)
        top_id = data.get("top_msg_id")
        if top_id is None:
            rep = data.get("reply_to")
            if isinstance(rep, int):
                known_tids = {int(tp.id) for tp in self.catalog.get(peer_id, [])}
                top_id = rep if rep in known_tids else 1
            elif rep is not None:
                top_id = getattr(rep, "top_msg_id", None) or getattr(rep, "reply_to_top_id", None) or getattr(rep, "reply_to_msg_id", None)
        self._bump_dest_topic(peer_id, top_id, int(sent_msg.id))
        return sent_msg

    async def __call__(self, request):
        name = type(request).__name__
        if name == "GetRepliesRequest":
            tid = int(request.msg_id)
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            self.mtproto_replies_calls[tid] = self.mtproto_replies_calls.get(tid, 0) + 1
            if peer_id == int(self.source_peer.id):
                # No Telegram real, GetReplies(1) NÃO devolve mensagens sem reply_to do General!
                thread = self._threads.get(tid, []) if tid != 1 else []
            else:
                thread = [
                    m for m in self.sent_messages.get(peer_id, [])
                    if getattr(getattr(m, "reply_to", None), "reply_to_top_id", None) == tid
                    or getattr(getattr(m, "reply_to", None), "reply_to_msg_id", None) == tid
                ]
            sliced = self._mtproto_slice(
                thread,
                offset_id=int(request.offset_id or 0),
                add_offset=int(request.add_offset or 0),
                limit=int(request.limit or 100),
            )
            return SimpleNamespace(messages=sliced, chats=[], users=[], count=len(thread))

        if name == "GetHistoryRequest":
            self.mtproto_history_calls += 1
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            if peer_id == int(self.source_peer.id):
                ordered = sorted(self._by_id.values(), key=lambda m: int(m.id))
            else:
                ordered = sorted(self.sent_messages.get(peer_id, []), key=lambda m: int(m.id))
            sliced = self._mtproto_slice(
                ordered,
                offset_id=int(request.offset_id or 0),
                add_offset=int(request.add_offset or 0),
                limit=int(request.limit or 100),
            )
            return SimpleNamespace(messages=sliced, chats=[], users=[], count=len(ordered))

        if name == "GetForumTopicsRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            topics = list(self.catalog.get(peer_id, []))
            top_map = self.top_messages_map.get(peer_id, {})
            if request.offset_topic or request.offset_id:
                cut = 0
                for idx, tp in enumerate(topics):
                    if int(tp.id) == int(request.offset_topic):
                        # Verifica se o cursor usou a data de top_message (e não created_date!)
                        expected_msg = top_map.get(int(tp.top_message))
                        if expected_msg is not None and request.offset_date is not None:
                            assert request.offset_date == expected_msg.date, (
                                f"Cursor GetForumTopics usou {request.offset_date} "
                                f"(provável topic.date de criação) em vez de "
                                f"top_message.date={expected_msg.date}!"
                            )
                        cut = idx + 1
                        break
                topics = topics[cut:]
            page = topics[: min(int(request.limit or 100), 50)]
            msgs = [top_map[int(tp.top_message)] for tp in page if int(tp.top_message) in top_map]
            return SimpleNamespace(
                count=len(self.catalog.get(peer_id, [])),
                topics=page,
                messages=msgs,
                chats=[],
                users=[],
                pts=1,
                order_by_create_date=False,
            )

        if name == "GetForumTopicsByIDRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            wanted = {int(x) for x in request.topics}
            found = [tp for tp in self.catalog.get(peer_id, []) if int(tp.id) in wanted]
            return SimpleNamespace(count=len(found), topics=found, messages=[], chats=[], users=[])

        if name == "GetFullChannelRequest":
            return SimpleNamespace(full_chat=SimpleNamespace(about="Curso completo"))

        if name == "CreateChannelRequest":
            created = channel(
                -9900 - len(self.created_channels),
                request.title,
                forum=bool(request.forum),
                broadcast=bool(request.broadcast),
            )
            created.creator = True
            self.created_channels.append(created)
            self.register_entity(created)
            if request.forum:
                gen = SimpleNamespace(
                    id=1, title="General", icon_color=0x6FB9F0, icon_emoji_id=None,
                    title_missing=False, closed=False, pinned=False, hidden=False,
                    date=BASE_TIME, top_message=1,
                )
                self.catalog[created.id] = [gen]
                self.top_messages_map[created.id] = {1: SimpleNamespace(id=1, date=BASE_TIME)}
            return SimpleNamespace(chats=[created], updates=[])

        if name == "ToggleForumRequest":
            ch = request.channel
            ch.forum = bool(request.enabled)
            if ch.forum and int(ch.id) not in self.catalog:
                gen = SimpleNamespace(
                    id=1, title="General", icon_color=0x6FB9F0, icon_emoji_id=None,
                    title_missing=False, closed=False, pinned=False, hidden=False,
                    date=BASE_TIME, top_message=1,
                )
                self.catalog[int(ch.id)] = [gen]
                self.top_messages_map[int(ch.id)] = {1: SimpleNamespace(id=1, date=BASE_TIME)}
            return SimpleNamespace(chats=[ch], updates=[])

        if name == "CreateForumTopicRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            self._ids += 1
            tid = max(self._next_dest_topic_id + 1, self._ids)
            self._next_dest_topic_id = tid
            self._ids = tid
            msg_id = tid
            tp = SimpleNamespace(
                id=tid,
                title=request.title,
                icon_color=getattr(request, "icon_color", 0x6FB9F0),
                icon_emoji_id=getattr(request, "icon_emoji_id", None),
                title_missing=bool(getattr(request, "title_missing", False)),
                closed=False,
                pinned=False,
                hidden=False,
                date=BASE_TIME + timedelta(seconds=msg_id),
                top_message=msg_id,
            )
            self.catalog.setdefault(peer_id, []).append(tp)
            self.top_messages_map.setdefault(peer_id, {})[msg_id] = SimpleNamespace(
                id=msg_id, date=tp.date
            )
            self._resort_dest_catalog(peer_id)
            self.created_topics.append(tp)
            return SimpleNamespace(
                updates=[tl.UpdateMessageID(id=tid, random_id=request.random_id)],
                chats=[],
                users=[],
            )

        if name == "EditForumTopicRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            for tp in self.catalog.get(peer_id, []):
                if int(tp.id) == int(request.topic_id):
                    if getattr(request, "title", None) is not None:
                        tp.title = request.title
                    if getattr(request, "closed", None) is not None:
                        tp.closed = bool(request.closed)
                    if getattr(request, "hidden", None) is not None:
                        tp.hidden = bool(request.hidden)
            return SimpleNamespace()

        if name == "UpdatePinnedForumTopicRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            for tp in self.catalog.get(peer_id, []):
                if int(tp.id) == int(request.topic_id):
                    tp.pinned = bool(request.pinned)
            self._resort_dest_catalog(peer_id)
            return SimpleNamespace()

        if name == "ReorderPinnedForumTopicsRequest":
            peer_id = int(getattr(request.peer, "id", 0) or 0)
            self.pinned_orders[peer_id] = [int(x) for x in request.order]
            self._resort_dest_catalog(peer_id)
            return SimpleNamespace()

        return await super().__call__(request)


def test_mtproto_topico_110_mensagens_nao_trunca_em_84(env) -> None:
    """Reproduz exatamente o cenário '16 - MÓDULO 05' (110 mensagens na origem vs 84 no destino)."""
    source = channel(-10055, "Matemática Online 2026", forum=True, broadcast=False)
    dest = channel(-20055, "Matemática Online 2026 (Clone)", forum=True, broadcast=False)

    topic_id = 500
    messages: List[FakeMessage] = [
        FakeMessage(
            id=topic_id,
            message="",
            date=BASE_TIME,
            action=_topic_create_action("16 - MÓDULO 05"),
        )
    ]
    thread_ids: List[int] = []

    # 16 mensagens de serviço (pins/avisos) + 110 mensagens reais = 126 entradas na thread (>100!)
    next_id = topic_id + 1
    expected_texts: List[str] = []
    for i in range(1, 17):
        messages.append(
            FakeMessage(
                id=next_id,
                message="",
                date=BASE_TIME + timedelta(seconds=next_id),
                action=tl.MessageActionPinMessage(),
                reply_to=SimpleNamespace(
                    reply_to_msg_id=topic_id, reply_to_top_id=topic_id, forum_topic=True
                ),
            )
        )
        thread_ids.append(next_id)
        next_id += 1

    for lesson_idx in range(1, 111):
        label = (
            "MAT 2 - Aula 27: Cones"
            if lesson_idx == 84
            else "MAT 2 - Aula 28: Troncos"
            if lesson_idx == 95
            else "MAT 2 - Aula 29: Esferas"
            if lesson_idx == 110
            else f"Módulo 05 - Item {lesson_idx:03d}"
        )
        reply_hdr = SimpleNamespace(
            reply_to_msg_id=topic_id, reply_to_top_id=topic_id, forum_topic=True
        )
        if lesson_idx in (84, 95, 109):
            actual_caption = label
            msg = video_message(next_id, caption=actual_caption, size=2048, reply_to=reply_hdr)
        elif lesson_idx in (80, 81, 82):
            actual_caption = label if lesson_idx == 82 else ""
            msg = document_message(
                next_id,
                name=f"cilindros_{lesson_idx}.pdf",
                caption=actual_caption,
                size=1024,
                grouped_id=7700,
                reply_to=reply_hdr,
            )
        else:
            actual_caption = label
            msg = text_message(next_id, actual_caption, reply_to=reply_hdr)
        msg.date = BASE_TIME + timedelta(seconds=next_id)
        messages.append(msg)
        thread_ids.append(next_id)
        expected_texts.append(actual_caption)
        next_id += 1

    # Confirma que o clamp real de add_offset=-100 devolve sobreposição no bloco 2:
    chunk1 = RealisticMTProtoForumClient._mtproto_slice(
        [m for m in messages if m.id in set(thread_ids)],
        offset_id=1,
        add_offset=-100,
        limit=100,
    )
    last_chunk1_id = chunk1[0].id  # maior ID do primeiro bloco de 100
    chunk2 = RealisticMTProtoForumClient._mtproto_slice(
        [m for m in messages if m.id in set(thread_ids)],
        offset_id=last_chunk1_id + 1,
        add_offset=-100,
        limit=100,
    )
    assert min(m.id for m in chunk2) <= last_chunk1_id, (
        "Simulador deve reproduzir o clamp de add_offset=-100 que truncava o Telethon em 84 msgs"
    )

    client = RealisticMTProtoForumClient(
        messages,
        source_peer=source,
        topics_meta=[
            {"id": topic_id, "title": "16 - MÓDULO 05", "top_message": thread_ids[-1]},
            {"id": 1, "title": "General", "top_message": 1},
        ],
        thread_membership={topic_id: thread_ids},
    )
    client.register_entity(dest)
    client.catalog[dest.id] = [
        SimpleNamespace(
            id=1, title="General", icon_color=0x6FB9F0, icon_emoji_id=None,
            title_missing=False, closed=False, pinned=False, hidden=False,
            date=BASE_TIME, top_message=1,
        )
    ]

    cloner = ChannelCloner(client, repo=env.repo)
    plan, initial_map, _ = asyncio.run(cloner.plan_existing(source, dest))
    topic_map = asyncio.run(cloner.ensure_topics(plan, dest, dict(initial_map)))
    topic_order = [tp.id for tp in ChannelCloner._creation_order(plan.topics)]

    cfg = RunConfig(workers=3, delay=0.0, use_dedup=False, signature="", daily_limit=0)
    copier = Copier(client, repo=env.repo, config=cfg, rich_ok=False)
    result = asyncio.run(
        copier.run(
            source,
            dest,
            dest_topic=topic_map.get(1),
            topic_map=topic_map,
            topic_order=topic_order,
        )
    )

    assert result["complete"] is True
    assert result["errors"] == 0
    assert result["done"] == 110, f"Esperava todas as 110 mensagens, obteve {result['done']}"
    delivered_texts = [
        item.get("text") if item.get("text") is not None else (item.get("caption") or "")
        for item in client.sent
    ]
    assert delivered_texts == expected_texts
    assert delivered_texts[-1] == "MAT 2 - Aula 29: Esferas"
    dest_ids = [item["msg_id"] for item in client.sent]
    assert dest_ids == sorted(dest_ids)


def test_mtproto_curso_completo_1200_msgs_multiplos_modulos_sem_pular_nem_intercalar(env) -> None:
    """Curso >1.000 msgs com módulos intercalados, replies sem top_id, tópico apagado e downloads lentos."""
    source = channel(-10088, "Curso Completo 2026", forum=True, broadcast=False)

    mod_ids = [101, 102, 103, 104]
    deleted_topic_id = 199
    messages: List[FakeMessage] = []
    threads: Dict[int, List[int]] = {tid: [] for tid in mod_ids}

    for idx, tid in enumerate(mod_ids, start=1):
        messages.append(
            FakeMessage(
                id=tid,
                message="",
                date=BASE_TIME + timedelta(days=idx),
                action=_topic_create_action(f"0{idx} - MÓDULO 0{idx}"),
            )
        )
    messages.append(
        FakeMessage(
            id=deleted_topic_id,
            message="",
            date=BASE_TIME + timedelta(days=5),
            action=_topic_create_action("TÓPICO APAGADO"),
        )
    )

    # Mensagens no General (sem reply_to e reply encadeada no General)
    g1 = text_message(210, "Bem-vindos ao Curso Completo 2026!")
    g1.date = BASE_TIME + timedelta(seconds=210)
    g2 = text_message(
        211,
        "Cronograma atualizado",
        reply_to=SimpleNamespace(reply_to_msg_id=210, reply_to_top_id=None, forum_topic=False),
    )
    g2.date = BASE_TIME + timedelta(seconds=211)
    messages.extend([g1, g2])

    # Mensagem órfã em tópico apagado (não pode derrubar nem cair no General!)
    orphan = text_message(
        220,
        "Órfã de tópico deletado",
        reply_to=SimpleNamespace(
            reply_to_msg_id=deleted_topic_id, reply_to_top_id=deleted_topic_id, forum_topic=True
        ),
    )
    orphan.date = BASE_TIME + timedelta(seconds=220)
    messages.append(orphan)

    # 1.120 mensagens intercaladas globalmente entre os 4 módulos (280 por módulo),
    # incluindo textos idênticos entre módulos, vídeos lentos, álbuns e replies sem reply_to_top_id!
    rng = random.Random(20261109)
    jitter: Dict[int, float] = {}
    expected_per_mod: Dict[int, List[str]] = {tid: [] for tid in mod_ids}
    last_msg_in_mod: Dict[int, int] = {}
    msg_id = 300

    for round_no in range(1, 281):
        for mod_idx, tid in enumerate(mod_ids, start=1):
            msg_id += 1
            if round_no % 25 == 0:
                # Texto repetido idêntico em todos os módulos (testa que dedup não come aulas!)
                caption = "Exercícios gerais de revisão do módulo"
            else:
                caption = f"M{mod_idx} - Aula {round_no:03d}"

            if round_no % 30 == 0 and tid in last_msg_in_mod:
                # Reply aninhada dentro do tópico SEM reply_to_top_id (só reply_to_msg_id + forum_topic=True)
                reply_hdr = SimpleNamespace(
                    reply_to_msg_id=last_msg_in_mod[tid],
                    reply_to_top_id=None,
                    forum_topic=True,
                )
            else:
                reply_hdr = SimpleNamespace(
                    reply_to_msg_id=tid,
                    reply_to_top_id=tid,
                    forum_topic=True,
                )

            if round_no % 40 == 0:
                m = video_message(msg_id, caption=caption, size=4096, reply_to=reply_hdr)
                jitter[msg_id] = 0.008  # vídeo demora mais para baixar que os PDFs seguintes!
            elif round_no % 40 in (1, 2):
                m = document_message(
                    msg_id,
                    name=f"m{mod_idx}_lista_{round_no}.pdf",
                    caption=caption,
                    size=512,
                    grouped_id=tid * 100_000 + (round_no // 40),
                    reply_to=reply_hdr,
                )
                jitter[msg_id] = 0.0005
            else:
                m = text_message(msg_id, caption, reply_to=reply_hdr)
                if rng.random() < 0.05:
                    jitter[msg_id] = 0.002

            m.date = BASE_TIME + timedelta(seconds=msg_id)
            messages.append(m)
            threads[tid].append(msg_id)
            last_msg_in_mod[tid] = msg_id
            expected_per_mod[tid].append(caption)

    total_expected = 2 + sum(len(v) for v in expected_per_mod.values())
    assert total_expected == 1122 > DAILY_LIMIT

    # Tópicos na ordem visual da API (do maior top_message para o menor; MÓDULO 01 fixado no topo):
    # Note que created_date está INVERTIDA de propósito para provar que usamos top_message.date!
    topics_meta = [
        {
            "id": 101,
            "title": "01 - MÓDULO 01",
            "pinned": True,
            "top_message": threads[101][-1],
            "created_date": BASE_TIME + timedelta(days=90),
            "activity_date": BASE_TIME + timedelta(seconds=threads[101][-1]),
        },
        {
            "id": 104,
            "title": "04 - MÓDULO 04",
            "top_message": threads[104][-1],
            "created_date": BASE_TIME + timedelta(days=1),
            "activity_date": BASE_TIME + timedelta(seconds=threads[104][-1]),
        },
        {
            "id": 103,
            "title": "03 - MÓDULO 03",
            "top_message": threads[103][-1],
            "created_date": BASE_TIME + timedelta(days=30),
            "activity_date": BASE_TIME + timedelta(seconds=threads[103][-1]),
        },
        {
            "id": 102,
            "title": "02 - MÓDULO 02",
            "top_message": threads[102][-1],
            "created_date": BASE_TIME + timedelta(days=60),
            "activity_date": BASE_TIME + timedelta(seconds=threads[102][-1]),
        },
        {
            "id": 1,
            "title": "General",
            "top_message": 211,
            "created_date": BASE_TIME,
            "activity_date": BASE_TIME + timedelta(seconds=211),
        },
    ]

    client = RealisticMTProtoForumClient(
        messages,
        source_peer=source,
        topics_meta=topics_meta,
        thread_membership=threads,
        download_jitter=jitter,
    )

    cloner = ChannelCloner(client, repo=env.repo)
    plan = asyncio.run(cloner.prepare(source, "Curso Completo 2026 (Clone)"))
    dest, topic_map = asyncio.run(cloner.create(plan))
    topic_order = [tp.id for tp in ChannelCloner._creation_order(plan.topics)]

    # Simula o que core.py faz na cópia fiel de fórum (sem dedup, sem assinatura, sem parede de 1.000):
    cfg = RunConfig(workers=4, delay=0.0, use_dedup=False, signature="", daily_limit=0)
    copier = Copier(client, repo=env.repo, config=cfg, rich_ok=False)
    result = asyncio.run(
        copier.run(
            source,
            dest,
            dest_topic=topic_map.get(1),
            topic_map=topic_map,
            topic_order=topic_order,
        )
    )
    assert result["complete"] is True
    assert result["errors"] == 0
    assert result["done"] == total_expected

    asyncio.run(cloner.apply_topic_settings(plan, dest, topic_map))

    def _sent_label(item: Dict[str, Any]) -> str:
        val = item.get("text") if item.get("text") is not None else (item.get("caption") or "")
        return str(val)

    # 1. Nenhuma mensagem órfã de tópico apagado foi enviada:
    all_texts = [_sent_label(s) for s in client.sent]
    assert "Órfã de tópico deletado" not in all_texts

    # 2. General recebeu exatamente as 2 mensagens do General (e nenhuma reply vazada dos módulos!):
    general_sent = [
        s for s in client.sent if s.get("top_msg_id") in (None, 1) and s.get("reply_to") not in set(topic_map.values()) - {1}
    ]
    assert [_sent_label(s) for s in general_sent] == [
        "Bem-vindos ao Curso Completo 2026!",
        "Cronograma atualizado",
    ]

    # 3. Cada módulo recebeu todas as 280 aulas na sequência EXATA 001..280, sem pulo nem inversão:
    for mod_idx, src_tid in enumerate(mod_ids, start=1):
        dst_tid = topic_map[src_tid]
        mod_sent = [
            s for s in client.sent
            if s.get("top_msg_id") == dst_tid or s.get("reply_to") == dst_tid
        ]
        assert len(mod_sent) == 280, f"Módulo {mod_idx}: esperava 280 msgs, obteve {len(mod_sent)}"
        assert [_sent_label(s) for s in mod_sent] == expected_per_mod[src_tid]
        mod_dest_ids = [s["msg_id"] for s in mod_sent]
        assert mod_dest_ids == sorted(mod_dest_ids)

    # 4. Zero intercalação entre tópicos: os envios de cada módulo formam um bloco contíguo!
    seen_finished_topics: set[int] = set()
    prev_topic: Optional[int] = None
    for s in client.sent:
        cur_topic = int(s.get("top_msg_id") or 1)
        if cur_topic != prev_topic:
            assert cur_topic not in seen_finished_topics, (
                f"Tópico {cur_topic} voltou a receber mensagens depois de já ter mudado de tópico!"
            )
            if prev_topic is not None:
                seen_finished_topics.add(prev_topic)
            prev_topic = cur_topic

    # 5. Ordem visual final da lista de tópicos no destino é IDÊNTICA à da origem!
    dest_visual_titles = [tp.title for tp in client.catalog[dest.id]]
    source_visual_titles = [tp.title for tp in client.catalog[source.id]]
    assert dest_visual_titles == source_visual_titles


def test_paginacao_topicos_acima_de_uma_pagina_usa_top_message_date(env) -> None:
    """Mais de 50 tópicos paginados: cursor usa response.messages[top_message].date."""
    source = channel(-10099, "Fórum 75 Tópicos", forum=True, broadcast=False)
    topics_meta = []
    for idx in range(75, 0, -1):
        tid = 100 + idx
        top_id = 10_000 + idx
        topics_meta.append(
            {
                "id": tid,
                "title": f"Tópico {idx:02d}",
                "top_message": top_id,
                # Data de criação propositalmente igual/invertida para pegar uso indevido de topic.date
                "created_date": BASE_TIME + timedelta(minutes=100 - idx),
                "activity_date": BASE_TIME + timedelta(hours=idx),
            }
        )
    topics_meta.append(
        {
            "id": 1,
            "title": "General",
            "top_message": 1,
            "created_date": BASE_TIME,
            "activity_date": BASE_TIME,
        }
    )

    client = RealisticMTProtoForumClient(
        [],
        source_peer=source,
        topics_meta=topics_meta,
        thread_membership={},
    )
    cloner = ChannelCloner(client, repo=env.repo)
    listed = asyncio.run(cloner.list_topics(source))
    assert [tp.title for tp in listed[:-1]] == [f"Tópico {idx:02d}" for idx in range(75, 0, -1)]
    creation = ChannelCloner._creation_order(listed)
    assert creation[0].id == 1
    assert [tp.title for tp in creation[1:]] == [f"Tópico {idx:02d}" for idx in range(1, 76)]


def test_retomada_apos_skipped_com_dedup_bloqueia_inversao_29_depois_28(env) -> None:
    """Impede que uma aula 'skipped' (28) seja enviada DEPOIS de uma já entregue (29)."""
    source = channel(-10111, "Origem")
    dest = channel(-20111, "Destino")
    messages = [
        text_message(27, "Aula 27"),
        text_message(28, "Exercícios gerais"),
        text_message(29, "Aula 29"),
    ]
    client = FakeClient(messages)
    client.register_entity(source)
    client.register_entity(dest)

    # Execução anterior havia marcado 27=done, 28=skipped (dedup antigo), 29=done
    env.repo.upsert_checkpoint(source.id, dest.id, 27, Status.DONE, dest_msg_id=501)
    env.repo.upsert_checkpoint(source.id, dest.id, 28, Status.SKIPPED)
    env.repo.upsert_checkpoint(source.id, dest.id, 29, Status.DONE, dest_msg_id=502)

    copier = Copier(
        client,
        repo=env.repo,
        config=RunConfig(workers=1, delay=0.0, use_dedup=False),
        rich_ok=False,
    )
    result = asyncio.run(copier.run(source, dest))
    assert result["errors"] == 1
    assert "Violação de ordem" in (result.get("first_error") or "")
    assert client.sent == [], "Nunca pode enviar a aula 28 depois da 29!"


def test_retomada_parcial_de_album_nao_reenvia_partes_ja_concluidas(env) -> None:
    """Se partes 1..2 de um álbum já estão DONE, a retomada envia somente 3..4."""
    source = channel(-10112, "Origem")
    dest = channel(-20112, "Destino")
    messages = [
        document_message(i, name=f"parte_{i}.pdf", caption=f"P{i}", grouped_id=888)
        for i in (1, 2, 3, 4)
    ]
    client = FakeClient(messages)
    client.register_entity(source)
    client.register_entity(dest)

    env.repo.upsert_checkpoint(source.id, dest.id, 1, Status.DONE, dest_msg_id=701)
    env.repo.upsert_checkpoint(source.id, dest.id, 2, Status.DONE, dest_msg_id=702)

    copier = Copier(
        client,
        repo=env.repo,
        config=RunConfig(workers=1, delay=0.0, use_dedup=False),
        rich_ok=False,
    )
    result = asyncio.run(copier.run(source, dest))
    assert result["complete"] is True
    assert result["done"] == 2
    assert [s.get("caption") or s.get("text") for s in client.sent] == ["P3", "P4"]


def test_cli_clone_e_verify_preservam_topicos_do_forum(env, tmp_path) -> None:
    """O modo CLI ``telecopy clone`` também cria/mapeia tópicos e valida por tópico."""
    import telecopy_cli

    source = channel(-10200, "Fórum CLI Origem", forum=True, broadcast=False)
    dest = channel(-20200, "Fórum CLI Destino", forum=True, broadcast=False)
    messages = [
        FakeMessage(id=10, message="", date=BASE_TIME, action=_topic_create_action("MÓDULO A")),
        FakeMessage(id=20, message="", date=BASE_TIME, action=_topic_create_action("MÓDULO B")),
        text_message(
            11,
            "Aula A1",
            reply_to=SimpleNamespace(reply_to_msg_id=10, reply_to_top_id=10, forum_topic=True),
        ),
        text_message(
            12,
            "Aula A2",
            reply_to=SimpleNamespace(reply_to_msg_id=10, reply_to_top_id=10, forum_topic=True),
        ),
        text_message(
            21,
            "Aula B1",
            reply_to=SimpleNamespace(reply_to_msg_id=20, reply_to_top_id=20, forum_topic=True),
        ),
    ]
    for m in messages:
        m.date = BASE_TIME + timedelta(seconds=int(m.id))

    client = RealisticMTProtoForumClient(
        messages,
        source_peer=source,
        topics_meta=[
            {"id": 20, "title": "MÓDULO B", "top_message": 21, "activity_date": BASE_TIME + timedelta(seconds=21)},
            {"id": 10, "title": "MÓDULO A", "top_message": 12, "activity_date": BASE_TIME + timedelta(seconds=12)},
            {"id": 1, "title": "General", "top_message": 1, "activity_date": BASE_TIME},
        ],
        thread_membership={10: [11, 12], 20: [21]},
    )
    client.register_entity(dest)
    client.catalog[dest.id] = [
        SimpleNamespace(
            id=1, title="General", icon_color=0x6FB9F0, icon_emoji_id=None,
            title_missing=False, closed=False, pinned=False, hidden=False,
            date=BASE_TIME, top_message=1,
        )
    ]

    args = telecopy_cli.build_parser().parse_args([
        "--lang", "pt",
        "clone",
        "--src", str(source.id),
        "--dst", str(dest.id),
        "--perfil", "recomendado",
        "--json",
        "--report-dir", str(tmp_path / "reports"),
    ])
    code = asyncio.run(telecopy_cli.executar(args, client=client, repo=env.repo))
    assert code == 0
    ver = asyncio.run(
        verificar_par(
            client,
            env.repo,
            source,
            dest,
            topic_map={tp_src: tp_dst for tp_src, tp_dst in ChannelCloner(client, repo=env.repo)._load_map(source, dest).items()},
        )
    )
    assert ver.como_dict()["complete"] is True
    assert ver.ordem_ok is True


def test_mtproto_copia_topico_unico_general_captura_mensagens_sem_reply_to(env) -> None:
    """No Telegram real, GetReplies(1) omite mensagens sem reply_to do General; iter_peer_messages cobre tudo."""
    source = channel(-10300, "Fórum General Origem", forum=True, broadcast=False)
    dest = channel(-20300, "Canal Destino", forum=False, broadcast=False)

    g1 = text_message(2, "Aviso 1 no General")
    g1.date = BASE_TIME + timedelta(seconds=2)
    m_other_root = FakeMessage(id=10, message="", date=BASE_TIME, action=_topic_create_action("Módulo X"))
    m_other = text_message(
        11,
        "Aula do Módulo X",
        reply_to=SimpleNamespace(reply_to_msg_id=10, reply_to_top_id=10, forum_topic=True),
    )
    m_other.date = BASE_TIME + timedelta(seconds=11)
    g2 = text_message(
        15,
        "Resposta no General",
        reply_to=SimpleNamespace(reply_to_msg_id=2, reply_to_top_id=None, forum_topic=False),
    )
    g2.date = BASE_TIME + timedelta(seconds=15)

    client = RealisticMTProtoForumClient(
        [g1, m_other_root, m_other, g2],
        source_peer=source,
        topics_meta=[
            {"id": 10, "title": "Módulo X", "top_message": 11},
            {"id": 1, "title": "General", "top_message": 15},
        ],
        thread_membership={10: [11]},
    )
    client.register_entity(dest)

    copier = Copier(
        client,
        repo=env.repo,
        config=RunConfig(workers=1, delay=0.0, use_dedup=False),
        rich_ok=False,
    )
    result = asyncio.run(copier.run(source, dest, source_topic=1))
    assert result["complete"] is True
    assert result["done"] == 2
    assert [s["text"] for s in client.sent] == ["Aviso 1 no General", "Resposta no General"]

