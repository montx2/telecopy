"""Criação de um destino privado com o tipo e a estrutura de fórum da origem.

A API do Telegram não permite clonar uma identidade de canal (username,
membros, permissões e datas originais). Este módulo replica o tipo do chat,
título/descrição e, para fóruns, seus tópicos e respectivos metadados básicos.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from telethon import functions
from telethon.errors import FloodWaitError
from telethon.helpers import generate_random_long
from telethon.tl import types

from i18n import t
from utils import logger

FORUM_PAGE_SIZE = 100
MAX_TOPIC_PAGES = 1000
MAX_TITLE_LENGTH = 128


@dataclass(frozen=True)
class ForumTopicSpec:
    """Metadados de um tópico que podem ser recriados pela API do Telegram."""

    id: int
    title: str
    icon_color: Optional[int] = None
    icon_emoji_id: Optional[int] = None
    title_missing: bool = False
    closed: bool = False
    pinned: bool = False
    hidden: bool = False


@dataclass(frozen=True)
class ClonePlan:
    """Estrutura que será criada depois da confirmação do usuário."""

    source: Any
    title: str
    about: str
    broadcast: bool
    forum: bool
    topics: Tuple[ForumTopicSpec, ...]
    warnings: Tuple[str, ...] = ()


class ChannelCloneError(RuntimeError):
    """Falha ao preparar/criar destino; destination informa se ficou parcial."""

    def __init__(self, message: str, destination: Any = None) -> None:
        super().__init__(message)
        self.destination = destination


class ChannelCloner:
    """Prepara e cria um canal/grupo privado baseado em uma origem."""

    def __init__(self, client) -> None:
        self.client = client

    async def _call(self, request, retries: int = 3):
        """Repete requests rejeitados explicitamente por FloodWait do Telegram."""
        for attempt in range(retries):
            try:
                return await self.client(request)
            except FloodWaitError as exc:
                if attempt + 1 >= retries:
                    raise
                wait = max(1, int(exc.seconds) + 1)
                logger.warning(f"FloodWait ao criar/ler estrutura; aguardando {wait}s")
                print(f"  ·  {t('copy.clone.flood_wait', seconds=wait)}", flush=True)
                await asyncio.sleep(wait)

    @staticmethod
    def default_title(source) -> str:
        """Usa o título original por padrão; o usuário ainda pode alterá-lo."""
        return (str(getattr(source, "title", "") or "TELECOPY").strip()
                or "TELECOPY")[:MAX_TITLE_LENGTH]

    async def prepare(self, source, title: Optional[str] = None) -> ClonePlan:
        title = self.default_title(source) if title is None else str(title).strip()
        if not title:
            raise ChannelCloneError(t("copy.clone.title_empty"))
        title_units = len(title.encode("utf-16-le")) // 2
        if title_units > MAX_TITLE_LENGTH:
            raise ChannelCloneError(t(
                "copy.clone.title_length", count=title_units, max=MAX_TITLE_LENGTH
            ))

        is_forum = bool(getattr(source, "forum", False))
        is_broadcast = bool(getattr(source, "broadcast", False)) and not is_forum
        warnings: List[str] = []
        about, about_error = await self._read_about(source)
        if about_error:
            warnings.append(about_error)

        topics: List[ForumTopicSpec] = []
        if is_forum:
            try:
                topics = await self._list_forum_topics(source)
            except Exception as exc:
                logger.exception(f"Não foi possível ler os tópicos da origem: {exc}")
                raise ChannelCloneError(t(
                    "copy.clone.topic_read_failed", reason=exc
                )) from exc

        return ClonePlan(
            source=source,
            title=title,
            about=about,
            broadcast=is_broadcast,
            forum=is_forum,
            topics=tuple(topics),
            warnings=tuple(warnings),
        )

    async def create(self, plan: ClonePlan) -> Tuple[Any, Dict[int, int]]:
        """Cria o chat e todos os tópicos; retorna a entidade e o mapa de IDs."""
        destination = None
        try:
            response = await self._call(functions.channels.CreateChannelRequest(
                title=plan.title,
                about=plan.about,
                broadcast=plan.broadcast,
                megagroup=not plan.broadcast,
                forum=plan.forum,
            ))
            destination = self._created_chat(response, plan.source)
            if destination is None:
                raise ChannelCloneError(t("copy.clone.created_data_missing"))

            topic_map: Dict[int, int] = {}
            if plan.forum:
                topic_map = await self._create_forum_topics(plan, destination)
            return destination, topic_map
        except ChannelCloneError as exc:
            if exc.destination is not None:
                raise
            raise ChannelCloneError(str(exc), destination=destination) from exc
        except Exception as exc:
            logger.exception(f"Falha ao criar destino Telegram: {exc}")
            raise ChannelCloneError(
                f"O Telegram não conseguiu criar o destino: {exc}",
                destination=destination,
            ) from exc

    async def map_existing_topics(self, source, destination) -> Dict[int, int]:
        """Mapeia fóruns existentes pelo título e pelos ícones dos tópicos.

        É usado ao retomar uma cópia interrompida para um destino criado antes.
        Títulos duplicados sem metadados suficientes são rejeitados em vez de
        enviar mensagens silenciosamente ao tópico errado.
        """
        if not getattr(source, "forum", False) or not getattr(destination, "forum", False):
            raise ChannelCloneError(t("copy.clone.forums_required"))

        source_topics = await self._list_forum_topics(source)
        dest_topics = await self._list_forum_topics(destination)
        general = next((topic for topic in dest_topics if topic.id == 1), None)
        if general is None:
            general = next(
                (topic for topic in dest_topics
                 if topic.title.strip().casefold() in {"general", "geral"}),
                None,
            )
        general_id = general.id if general else 1
        mapping: Dict[int, int] = {1: general_id}
        used = {general_id}

        for source_topic in source_topics:
            if source_topic.id == 1:
                mapping[source_topic.id] = general_id
                continue
            candidates = [
                topic for topic in dest_topics
                if topic.id not in used and topic.title == source_topic.title
            ]
            if not candidates:
                raise ChannelCloneError(t(
                    "copy.clone.topic_missing_destination", topic=source_topic.title
                ))
            if len(candidates) > 1:
                exact_icon = [
                    topic for topic in candidates
                    if topic.icon_color == source_topic.icon_color
                    and topic.icon_emoji_id == source_topic.icon_emoji_id
                ]
                if len(exact_icon) == 1:
                    candidates = exact_icon
                else:
                    raise ChannelCloneError(t(
                        "copy.clone.topic_ambiguous", topic=source_topic.title
                    ))
            target = candidates[0]
            mapping[source_topic.id] = target.id
            used.add(target.id)
        return mapping

    async def apply_topic_settings(
        self, plan: ClonePlan, destination, topic_map: Dict[int, int]
    ) -> List[Tuple[str, str]]:
        """Aplica estados que precisam esperar até os envios aos tópicos acabarem."""
        warnings: List[Tuple[str, str]] = []
        if not plan.forum:
            return warnings

        for topic in plan.topics:
            target_id = topic_map.get(topic.id)
            if not target_id:
                continue
            changes: Dict[str, bool] = {}
            if topic.closed:
                changes["closed"] = True
            if topic.hidden:
                changes["hidden"] = True
            if not changes:
                continue
            try:
                await self._call(functions.messages.EditForumTopicRequest(
                    peer=destination,
                    topic_id=target_id,
                    **changes,
                ))
            except Exception as exc:
                logger.warning(f"Não foi possível restaurar o tópico {topic.title!r}: {exc}")
                warnings.append((topic.title, str(exc)))

        pinned_ids = [
            topic_map[topic.id]
            for topic in plan.topics
            if topic.pinned and topic.id in topic_map
        ]
        for topic_id in pinned_ids:
            try:
                await self._call(functions.messages.UpdatePinnedForumTopicRequest(
                    peer=destination,
                    topic_id=topic_id,
                    pinned=True,
                ))
            except Exception as exc:
                logger.warning(f"Não foi possível fixar o tópico {topic_id}: {exc}")
                warnings.append((str(topic_id), str(exc)))

        if len(pinned_ids) > 1:
            try:
                await self._call(functions.messages.ReorderPinnedForumTopicsRequest(
                    peer=destination,
                    order=pinned_ids,
                    force=True,
                ))
            except Exception as exc:
                logger.warning(f"Não foi possível ordenar tópicos fixados: {exc}")
                warnings.append(("fixados", str(exc)))
        return warnings

    async def _read_about(self, source) -> Tuple[str, Optional[str]]:
        """Lê a descrição completa; se indisponível, continua sem descrição."""
        try:
            if isinstance(source, types.Chat):
                request = functions.messages.GetFullChatRequest(chat_id=int(source.id))
            else:
                request = functions.channels.GetFullChannelRequest(channel=source)
            response = await self._call(request)
            full_chat = getattr(response, "full_chat", None)
            return str(getattr(full_chat, "about", "") or ""), None
        except Exception as exc:
            logger.warning(f"Descrição da origem não pôde ser lida: {exc}")
            return "", str(exc)

    async def _list_forum_topics(self, peer) -> List[ForumTopicSpec]:
        """Busca todos os tópicos, com paginação e proteção contra cursor travado."""
        collected: Dict[int, ForumTopicSpec] = {}
        offset_date = None
        offset_id = 0
        offset_topic = 0

        for _page in range(MAX_TOPIC_PAGES):
            response = await self._call(functions.messages.GetForumTopicsRequest(
                peer=peer,
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=FORUM_PAGE_SIZE,
            ))
            page_topics = list(getattr(response, "topics", None) or [])
            total = getattr(response, "count", None)

            for topic in page_topics:
                if type(topic).__name__ == "ForumTopicDeleted":
                    continue
                topic_id = int(getattr(topic, "id", 0) or 0)
                if topic_id <= 0:
                    continue
                collected[topic_id] = ForumTopicSpec(
                    id=topic_id,
                    title=str(getattr(topic, "title", "") or ""),
                    icon_color=getattr(topic, "icon_color", None),
                    icon_emoji_id=getattr(topic, "icon_emoji_id", None),
                    title_missing=bool(getattr(topic, "title_missing", False)),
                    closed=bool(getattr(topic, "closed", False)),
                    pinned=bool(getattr(topic, "pinned", False)),
                    hidden=bool(getattr(topic, "hidden", False)),
                )

            if not page_topics or (total is not None and len(collected) >= int(total)):
                break
            if len(page_topics) < FORUM_PAGE_SIZE:
                break

            last = page_topics[-1]
            next_cursor = (
                getattr(last, "date", None),
                int(getattr(last, "top_message", 0) or 0),
                int(getattr(last, "id", 0) or 0),
            )
            if next_cursor == (offset_date, offset_id, offset_topic):
                raise RuntimeError(t("copy.clone.topic_pagination_stuck"))
            offset_date, offset_id, offset_topic = next_cursor
        else:
            raise RuntimeError(t("copy.clone.topic_page_limit"))

        if total is not None and len(collected) < int(total):
            raise RuntimeError(t(
                "copy.clone.topic_count_mismatch",
                count=int(total), found=len(collected),
            ))
        return list(collected.values())

    async def _create_forum_topics(
        self, plan: ClonePlan, destination
    ) -> Dict[int, int]:
        existing = await self._list_forum_topics(destination)
        general = next((topic for topic in existing if topic.id == 1), None)
        if general is None:
            general = next(
                (topic for topic in existing
                 if topic.title.strip().casefold() in {"general", "geral"}),
                None,
            )
        general_id = general.id if general else 1
        topic_map: Dict[int, int] = {1: general_id}

        for topic in plan.topics:
            if topic.id == 1:
                topic_map[topic.id] = general_id
                continue
            before = {item.id for item in existing}
            request_kwargs: Dict[str, Any] = {
                "peer": destination,
                "title": topic.title,
                "random_id": generate_random_long(),
            }
            if topic.title_missing:
                request_kwargs["title_missing"] = True
            if topic.icon_color:
                request_kwargs["icon_color"] = int(topic.icon_color)
            if topic.icon_emoji_id:
                request_kwargs["icon_emoji_id"] = int(topic.icon_emoji_id)

            response = await self._call(functions.messages.CreateForumTopicRequest(
                **request_kwargs
            ))
            created_id = self._created_topic_id(response)
            if created_id is None:
                refreshed = await self._list_forum_topics(destination)
                created = [item for item in refreshed if item.id not in before]
                matching = [item for item in created if item.title == topic.title]
                candidate = (matching or created)
                if len(candidate) == 1:
                    created_id = candidate[0].id
                existing = refreshed

            if not created_id:
                raise RuntimeError(t(
                    "copy.clone.topic_id_missing", topic=topic.title
                ))
            topic_map[topic.id] = int(created_id)
            if created_id not in {item.id for item in existing}:
                existing.append(ForumTopicSpec(
                    id=int(created_id),
                    title=topic.title,
                    icon_color=topic.icon_color,
                    icon_emoji_id=topic.icon_emoji_id,
                ))
        return topic_map

    @staticmethod
    def _created_chat(response, source):
        source_id = int(getattr(source, "id", 0) or 0)
        for chat in getattr(response, "chats", None) or []:
            if int(getattr(chat, "id", 0) or 0) != source_id:
                return chat
        for update in getattr(response, "updates", None) or []:
            chat = getattr(update, "chat", None)
            if chat is not None and int(getattr(chat, "id", 0) or 0) != source_id:
                return chat
        return None

    @staticmethod
    def _created_topic_id(response) -> Optional[int]:
        for update in getattr(response, "updates", None) or []:
            message = getattr(update, "message", None)
            action = getattr(message, "action", None)
            if message is not None and type(action).__name__ == "MessageActionTopicCreate":
                message_id = int(getattr(message, "id", 0) or 0)
                if message_id > 0:
                    return message_id
            topic_id = int(getattr(update, "topic_id", 0) or 0)
            if topic_id > 0:
                return topic_id
        direct_id = int(getattr(response, "topic_id", 0) or 0)
        return direct_id or None
