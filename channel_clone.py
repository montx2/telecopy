"""Estrutura de fóruns Telegram: leitura completa, mapeamento e criação.

A API não permite clonar identidade, membros, datas ou autores originais.
Este módulo replica o que é representável: tipo do chat, descrição (em destino
novo) e tópicos com título/ícone/estado. O mapa origem→destino é persistido
quando um Repository é fornecido, para retomar sem recriar tópicos.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    # Data da última atividade (top_message em response.messages, com fallback
    # para topic.date); usada junto com top_message para ordenar tópicos como na origem.
    date: Optional[datetime] = None
    top_message: int = 0


@dataclass(frozen=True)
class ClonePlan:
    """Estrutura que será criada/atualizada depois da confirmação do usuário."""

    source: Any
    title: str
    about: str
    broadcast: bool
    forum: bool
    topics: Tuple[ForumTopicSpec, ...]
    warnings: Tuple[str, ...] = ()
    existing: bool = False


class ChannelCloneError(RuntimeError):
    """Falha ao preparar/criar destino; destination informa se ficou parcial."""

    def __init__(self, message: str, destination: Any = None) -> None:
        super().__init__(message)
        self.destination = destination


class ChannelCloner:
    """Prepara e cria/atualiza um destino privado baseado em uma origem."""

    def __init__(self, client, repo=None) -> None:
        self.client = client
        self.repo = repo
        # Avisos não fatais (ex.: ícone recusado pelo Telegram) que a UI mostra
        # depois de preparar a estrutura, sem abortar a clonagem.
        self.warnings: List[str] = []

    def _warn(self, message: str) -> None:
        logger.warning(message)
        self.warnings.append(message)

    async def _call(self, request, retries: int = 5):
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
    def default_title(source, suffix: str = "") -> str:
        """Título sugerido; ``suffix`` é anexado respeitando o limite UTF-16.

        O espaço antes do sufixo é adicionado aqui: o prompt de configuração
        apara espaços nas pontas, então o usuário digita só o texto.
        """
        base = (str(getattr(source, "title", "") or "TELECOPY").strip()
                or "TELECOPY")
        text = str(suffix or "").strip()
        suffix = f" {text}" if text else ""
        while base and len((base + suffix).encode("utf-16-le")) // 2 > MAX_TITLE_LENGTH:
            base = base[:-1]
        return f"{base}{suffix}"

    @staticmethod
    def _map_key(source, destination) -> str:
        return f"forum_topic_map:{int(source.id)}:{int(destination.id)}"

    def _load_map(self, source, destination) -> Dict[int, int]:
        if self.repo is None:
            return {}
        try:
            raw = self.repo.get_kv(self._map_key(source, destination))
            data = json.loads(raw) if raw else {}
            return {int(k): int(v) for k, v in data.items()}
        except Exception as exc:
            logger.warning(f"Mapa de tópicos persistido inválido: {exc}")
            return {}

    def _save_map(self, source, destination, topic_map: Dict[int, int]) -> None:
        if self.repo is None:
            return
        try:
            self.repo.set_kv(
                self._map_key(source, destination),
                json.dumps({str(k): int(v) for k, v in sorted(topic_map.items())}),
            )
        except Exception as exc:
            # A cópia ainda pode continuar, mas a retomada pode exigir mapear
            # pelo título. Não esconda isso do usuário.
            logger.warning(f"Não foi possível persistir o mapa de tópicos: {exc}")

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
            source=source, title=title, about=about, broadcast=is_broadcast,
            forum=is_forum, topics=tuple(topics), warnings=tuple(warnings),
        )

    async def plan_existing(
        self, source, destination
    ) -> Tuple[ClonePlan, Dict[int, int], Tuple[str, ...]]:
        """Planeja um fórum existente sem escrever nada antes da confirmação.

        Tópicos já associados pelo mapa persistido são reutilizados mesmo se
        tiverem sido renomeados. Títulos duplicados sem ícone distintivo são
        recusados; tópicos ausentes serão criados somente em ``ensure_topics``.
        """
        if not getattr(source, "forum", False) or not getattr(destination, "forum", False):
            raise ChannelCloneError(t("copy.clone.forums_required"))
        if int(source.id) == int(destination.id):
            raise ChannelCloneError(t("entity.same"))

        source_topics = await self._list_forum_topics(source)
        dest_topics = await self._list_forum_topics(destination)
        dest_by_id = {topic.id: topic for topic in dest_topics}
        general = self._general_topic(dest_topics)
        general_id = general.id if general else 1

        saved = self._load_map(source, destination)
        current_source_ids = {topic.id for topic in source_topics}
        mapping: Dict[int, int] = {1: general_id}
        used = {general_id}
        # Validar o mapa salvo antes de usar qualquer parte dele. Tópicos de
        # origem que não existem mais não podem consumir tópicos do destino.
        for source_id, dest_id in saved.items():
            if source_id == 1 or source_id not in current_source_ids:
                continue
            if dest_id in dest_by_id and dest_id not in used and dest_id != general_id:
                mapping[source_id] = dest_id
                used.add(dest_id)

        missing: List[str] = []
        for topic in source_topics:
            if topic.id == 1:
                mapping[1] = general_id
                continue
            if topic.id in mapping:
                continue
            candidates = [
                item for item in dest_topics
                if item.id not in used and item.title == topic.title
            ]
            if not candidates:
                missing.append(topic.title)
                continue
            if len(candidates) > 1:
                exact_icon = [
                    item for item in candidates
                    if (item.icon_color or 0) == (topic.icon_color or 0)
                    and (item.icon_emoji_id or 0) == (topic.icon_emoji_id or 0)
                ]
                if len(exact_icon) == 1:
                    candidates = exact_icon
                else:
                    raise ChannelCloneError(t(
                        "copy.clone.topic_ambiguous", topic=topic.title
                    ))
            mapping[topic.id] = candidates[0].id
            used.add(candidates[0].id)

        plan = ClonePlan(
            source=source,
            title=str(getattr(destination, "title", "") or ""),
            about="",  # não sobrescreve a descrição de um destino existente
            broadcast=False, forum=True, topics=tuple(source_topics), existing=True,
        )
        return plan, mapping, tuple(missing)

    async def ensure_topics(
        self, plan: ClonePlan, destination, topic_map: Dict[int, int]
    ) -> Dict[int, int]:
        """Cria os tópicos ausentes e atualiza título/ícone dos associados."""
        if not plan.forum:
            return topic_map
        existing = await self._list_forum_topics(destination)
        by_id = {topic.id: topic for topic in existing}
        general = self._general_topic(existing)
        general_id = general.id if general else 1
        valid_source_ids = {1} | {topic.id for topic in plan.topics}
        topic_map = {k: v for k, v in dict(topic_map).items() if k in valid_source_ids}
        topic_map[1] = general_id

        for topic in self._creation_order(plan.topics):
            if topic.id == 1:
                # General é um tópico nativo do Telegram; nunca criar duplicata.
                topic_map[1] = general_id
                if general is not None:
                    await self._sync_topic_metadata(destination, topic, general)
                continue
            target_id = topic_map.get(topic.id)

            if target_id and target_id in by_id:
                await self._sync_topic_metadata(destination, topic, by_id[target_id])
                continue

            created_id = await self._create_topic(destination, topic, existing)
            topic_map[topic.id] = created_id
            self._save_map(plan.source, destination, topic_map)
            if created_id not in by_id:
                spec = ForumTopicSpec(
                    id=created_id, title=topic.title, icon_color=topic.icon_color,
                    icon_emoji_id=topic.icon_emoji_id,
                )
                existing.append(spec)
                by_id[created_id] = spec

        self._save_map(plan.source, destination, topic_map)
        return topic_map

    async def create(self, plan: ClonePlan, on_created=None
                     ) -> Tuple[Any, Dict[int, int]]:
        """Cria o chat e todos os tópicos; retorna a entidade e o mapa de IDs.

        ``on_created(destination)`` roda imediatamente depois de
        ``CreateChannelRequest`` e antes de qualquer tópico, para que o par
        origem→destino seja registrado mesmo se a criação dos tópicos falhar.
        """
        destination = None
        try:
            response = await self._call(functions.channels.CreateChannelRequest(
                title=plan.title, about=plan.about, broadcast=plan.broadcast,
                megagroup=not plan.broadcast, forum=plan.forum,
            ))
            destination = self._created_chat(response, plan.source)
            if destination is None:
                raise ChannelCloneError(t("copy.clone.created_data_missing"))

            if on_created is not None:
                try:
                    result = on_created(destination)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:  # registro não pode derrubar a clonagem
                    logger.warning(f"Registro do clone automático falhou: {exc}")

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
        """Mapeamento estrito (compatibilidade): exige todos os tópicos prontos."""
        plan, mapping, missing = await self.plan_existing(source, destination)
        if missing:
            raise ChannelCloneError(t(
                "copy.clone.topic_missing_destination", topic=missing[0]
            ))
        return mapping

    async def apply_topic_settings(
        self, plan: ClonePlan, destination, topic_map: Dict[int, int]
    ) -> List[Tuple[str, str]]:
        """Aplica closed/hidden/pinned depois que os envios terminaram."""
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
            if changes:
                try:
                    await self._call(functions.messages.EditForumTopicRequest(
                        peer=destination, topic_id=target_id, **changes,
                    ))
                except Exception as exc:
                    logger.warning(f"Não foi possível restaurar o tópico {topic.title!r}: {exc}")
                    warnings.append((topic.title, str(exc)))

        pinned_ids = []
        for topic in plan.topics:
            target_id = topic_map.get(topic.id)
            if not target_id:
                continue
            try:
                await self._call(functions.messages.UpdatePinnedForumTopicRequest(
                    peer=destination, topic_id=target_id, pinned=bool(topic.pinned),
                ))
                if topic.pinned:
                    pinned_ids.append(target_id)
            except Exception as exc:
                logger.warning(f"Não foi possível fixar/desfixar o tópico {topic.title!r}: {exc}")
                warnings.append((topic.title, str(exc)))

        if len(pinned_ids) > 1:
            try:
                await self._call(functions.messages.ReorderPinnedForumTopicsRequest(
                    peer=destination, order=pinned_ids, force=True,
                ))
            except Exception as exc:
                logger.warning(f"Não foi possível ordenar tópicos fixados: {exc}")
                warnings.append(("fixados", str(exc)))
        return warnings

    async def _sync_topic_metadata(self, destination, source: ForumTopicSpec,
                                   target: ForumTopicSpec) -> None:
        """Atualiza o que a API permite editar; cor de tópico existente não é editável."""
        changes: Dict[str, Any] = {}
        if not source.title_missing and source.title != target.title:
            changes["title"] = source.title
        if source.icon_emoji_id != target.icon_emoji_id:
            changes["icon_emoji_id"] = int(source.icon_emoji_id or 0)
        if changes:
            await self._call(functions.messages.EditForumTopicRequest(
                peer=destination, topic_id=target.id, **changes,
            ))
        if (source.icon_color or 0) != (target.icon_color or 0):
            logger.warning(
                f"A cor do tópico existente {target.title!r} não pode ser alterada pela API; "
                "crie um destino novo para reproduzir a cor original."
            )

    async def _read_about(self, source) -> Tuple[str, Optional[str]]:
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

    @staticmethod
    def _spec(topic, activity_date: Optional[datetime] = None) -> ForumTopicSpec:
        stamp = activity_date if activity_date is not None else getattr(topic, "date", None)
        return ForumTopicSpec(
            id=int(topic.id),
            title=str(getattr(topic, "title", "") or ""),
            icon_color=getattr(topic, "icon_color", None),
            icon_emoji_id=getattr(topic, "icon_emoji_id", None),
            title_missing=bool(getattr(topic, "title_missing", False)),
            closed=bool(getattr(topic, "closed", False)),
            pinned=bool(getattr(topic, "pinned", False)),
            hidden=bool(getattr(topic, "hidden", False)),
            date=stamp,
            top_message=int(getattr(topic, "top_message", 0) or 0),
        )

    @staticmethod
    def _general_topic(topics: List[ForumTopicSpec]) -> Optional[ForumTopicSpec]:
        # O General do Telegram tem ID 1. Um tópico comum chamado "General"
        # não pode ser sequestrado como se fosse o tópico nativo.
        return next((topic for topic in topics if topic.id == 1), None)

    async def _list_forum_topics(self, peer) -> List[ForumTopicSpec]:
        """Busca todos os tópicos, inclusive General, sem parar em página curta."""
        collected: Dict[int, ForumTopicSpec] = {}
        offset_date = None
        offset_id = 0
        offset_topic = 0
        total: Optional[int] = None

        for _page in range(MAX_TOPIC_PAGES):
            response = await self._call(functions.messages.GetForumTopicsRequest(
                peer=peer, offset_date=offset_date, offset_id=offset_id,
                offset_topic=offset_topic, limit=FORUM_PAGE_SIZE,
            ))
            page_topics = list(getattr(response, "topics", None) or [])
            page_messages = list(getattr(response, "messages", None) or [])
            msg_dates = {
                int(getattr(m, "id", 0) or 0): getattr(m, "date", None)
                for m in page_messages
                if int(getattr(m, "id", 0) or 0) > 0
            }
            by_create = bool(getattr(response, "order_by_create_date", False))
            total = getattr(response, "count", None)

            for topic in page_topics:
                if type(topic).__name__ == "ForumTopicDeleted":
                    continue
                topic_id = int(getattr(topic, "id", 0) or 0)
                if topic_id > 0:
                    top_msg_id = int(getattr(topic, "top_message", 0) or 0)
                    activity = (
                        getattr(topic, "date", None)
                        if by_create
                        else (msg_dates.get(top_msg_id) or getattr(topic, "date", None))
                    )
                    collected[topic_id] = self._spec(topic, activity_date=activity)

            if not page_topics:
                break
            if total is not None and len(collected) >= int(total):
                break

            last = page_topics[-1]
            last_top = int(getattr(last, "top_message", 0) or 0)
            last_offset_date = (
                getattr(last, "date", None)
                if by_create
                else (msg_dates.get(last_top) or getattr(last, "date", None))
            )
            next_cursor = (
                last_offset_date,
                last_top,
                int(getattr(last, "id", 0) or 0),
            )
            if next_cursor == (offset_date, offset_id, offset_topic):
                raise RuntimeError(t("copy.clone.topic_pagination_stuck"))
            offset_date, offset_id, offset_topic = next_cursor
        else:
            raise RuntimeError(t("copy.clone.topic_page_limit"))

        if 1 not in collected:
            general = await self._read_general_topic(peer)
            if general is not None:
                collected[1] = general

        if total is not None and len(collected) < int(total) and page_topics:
            # Uma página final vazia pode significar apenas tópicos apagados no
            # count; nesse caso não há como recriar conteúdo que já foi apagado.
            raise RuntimeError(t(
                "copy.clone.topic_count_mismatch",
                count=int(total), found=len(collected),
            ))
        return list(collected.values())

    async def _read_general_topic(self, peer) -> Optional[ForumTopicSpec]:
        try:
            response = await self._call(functions.messages.GetForumTopicsByIDRequest(
                peer=peer, topics=[1],
            ))
            for topic in getattr(response, "topics", None) or []:
                if int(getattr(topic, "id", 0) or 0) == 1:
                    return self._spec(topic)
        except Exception as exc:
            logger.warning(f"Tópico General não retornado pela API: {exc}")
        return None

    async def _create_forum_topics(self, plan: ClonePlan, destination) -> Dict[int, int]:
        existing = await self._list_forum_topics(destination)
        general = self._general_topic(existing)
        general_id = general.id if general else 1
        topic_map: Dict[int, int] = {1: general_id}

        for topic in self._creation_order(plan.topics):
            if topic.id == 1:
                topic_map[1] = general_id
                if general is not None:
                    await self._sync_topic_metadata(destination, topic, general)
                continue
            created_id = await self._create_topic(destination, topic, existing)
            topic_map[topic.id] = created_id
            self._save_map(plan.source, destination, topic_map)
            if created_id not in {item.id for item in existing}:
                existing.append(ForumTopicSpec(
                    id=created_id, title=topic.title, icon_color=topic.icon_color,
                    icon_emoji_id=topic.icon_emoji_id,
                ))
        self._save_map(plan.source, destination, topic_map)
        return topic_map

    @staticmethod
    def _creation_order(topics: Sequence[ForumTopicSpec]) -> List[ForumTopicSpec]:
        """Do tópico com menos atividade recente para o com mais atividade.

        O Telegram ordena os tópicos pela última atividade (``top_message``) e
        ``GetForumTopics`` devolve os mais recentes primeiro. Criar e popular
        do menos recente ao mais recente faz o último tópico ficar no topo do
        destino, espelhando exatamente a ordem visual da origem.
        """
        def key(item: Tuple[int, ForumTopicSpec]):
            index, topic = item
            stamp = topic.date
            if isinstance(stamp, datetime):
                seconds = stamp.timestamp()
                top_msg = int(getattr(topic, "top_message", 0) or 0)
            else:
                seconds = float("-inf")
                top_msg = 0
            return (0 if topic.id == 1 else 1, seconds, top_msg, -index)

        return [topic for _index, topic in sorted(enumerate(topics), key=key)]

    async def _create_topic(self, destination, topic: ForumTopicSpec,
                            existing: List[ForumTopicSpec]) -> int:
        before = {item.id for item in existing}
        request_kwargs: Dict[str, Any] = {
            "peer": destination, "title": topic.title,
            "random_id": generate_random_long(),
        }
        if topic.title_missing:
            request_kwargs["title_missing"] = True
        if topic.icon_color is not None:
            request_kwargs["icon_color"] = int(topic.icon_color)
        if topic.icon_emoji_id:
            request_kwargs["icon_emoji_id"] = int(topic.icon_emoji_id)

        # Um emoji de tópico pode ser recusado (o ID pertence ao dono original
        # ou o emoji virou inacessível). O tópico em si ainda é representável:
        # tentamos sem o emoji e, por último, sem ícone nenhum.
        attempts: List[Dict[str, Any]] = [dict(request_kwargs)]
        if topic.icon_emoji_id:
            without_emoji = {k: v for k, v in request_kwargs.items()
                             if k != "icon_emoji_id"}
            attempts.append(without_emoji)
            attempts.append({k: v for k, v in without_emoji.items()
                             if k != "icon_color"})

        response = None
        for index, kwargs in enumerate(attempts):
            try:
                response = await self._call(
                    functions.messages.CreateForumTopicRequest(**kwargs)
                )
            except FloodWaitError:
                raise
            except Exception as exc:
                if index + 1 >= len(attempts) or self._fatal_topic_error(exc):
                    raise
                logger.warning(
                    f"CreateForumTopic recusou o ícone do tópico {topic.title!r} "
                    f"({type(exc).__name__}); tentando novamente sem ele"
                )
                continue
            if index == 1:
                self._warn(t("copy.clone.topic_icon_fallback", topic=topic.title))
            elif index == 2:
                self._warn(t("copy.clone.topic_icon_dropped", topic=topic.title))
            break

        created_id = self._created_topic_id(response)
        if created_id is None:
            refreshed = await self._list_forum_topics(destination)
            created = [item for item in refreshed if item.id not in before]
            matching = [item for item in created if item.title == topic.title]
            candidate = matching or created
            if len(candidate) == 1:
                created_id = candidate[0].id
        if not created_id:
            raise RuntimeError(t("copy.clone.topic_id_missing", topic=topic.title))
        return int(created_id)

    @staticmethod
    def _fatal_topic_error(exc: Exception) -> bool:
        """Erros que não têm relação com o ícone e não devem ser repetidos."""
        return type(exc).__name__ in {
            "ChatAdminRequiredError", "ChannelPrivateError", "ChannelInvalidError",
            "ChatForbiddenError", "TopicDeletedError", "ParticipantsTooFewError",
        }

    async def list_topics(self, peer) -> List[ForumTopicSpec]:
        """Leitura pública da estrutura, na ordem da API (mais ativo primeiro)."""
        return await self._list_forum_topics(peer)

    async def ensure_topic(self, plan: ClonePlan, destination,
                           topic: ForumTopicSpec,
                           topic_map: Dict[int, int]) -> int:
        """Cria um tópico que apareceu na origem depois do início do espelho."""
        existing = await self._list_forum_topics(destination)
        by_id = {item.id: item for item in existing}
        target_id = topic_map.get(topic.id)
        if target_id and target_id in by_id:
            return int(target_id)
        created_id = await self._create_topic(destination, topic, existing)
        topic_map[topic.id] = created_id
        self._save_map(plan.source, destination, topic_map)
        return int(created_id)

    async def enable_forum(self, destination):
        """Ativa o modo fórum em um supergrupo; exige que a conta seja admin."""
        try:
            request = functions.channels.ToggleForumRequest(
                channel=destination, enabled=True, tabs=False,
            )
        except TypeError:
            # Camadas antigas do schema não têm o campo ``tabs``.
            request = functions.channels.ToggleForumRequest(
                channel=destination, enabled=True,
            )
        await self._call(request)
        try:
            fresh = await self.client.get_entity(int(destination.id))
        except Exception as exc:  # pragma: no cover - depende da rede
            logger.warning(f"Destino não pôde ser relido após ativar tópicos: {exc}")
            fresh = None
        if fresh is not None:
            try:
                fresh.forum = True
            except Exception:
                pass
            return fresh
        try:
            destination.forum = True
        except Exception:
            pass
        return destination

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
