from sqlalchemy import select, or_, update, func, distinct, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from shared.models import Conversation, conversation_tags, Message, MessageDirection, Instance


async def get_or_create_conversation(session, *, instance_id: int, chat_id: str,
                                     phone: str | None = None, chat_name: str | None = None) -> Conversation:
    stmt = select(Conversation).where(
        Conversation.instance_id == instance_id,
        Conversation.chat_id == chat_id,
    )
    conv = await session.scalar(stmt)
    if conv:
        return conv

    conv = Conversation(
        instance_id=instance_id,
        chat_id=chat_id,
        phone=phone,
        title=chat_name or phone,
        is_group=chat_id.endswith("@g.us"),
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


async def fetch_dialogs(
    session: AsyncSession,
    *,
    instance_id: int,
    tag_ids: list[int] | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    Возвращает список словарей с полями:
    id, chat_id, title, phone, last_message_at, last_message, unread
    """
    m, c, i = Message.__table__, Conversation.__table__, Instance.__table__

    last_msg = (
        select(
            Message.conversation_id,
            Message.text,
            Message.direction,
            Message.created_at.label("msg_at")
        )
        .order_by(Message.conversation_id, Message.created_at.desc())
        .distinct(Message.conversation_id)
        .cte("last_msg")
    )

    stmt = (
        select(
            c.c.id,
            c.c.chat_id,
            c.c.title,
            c.c.phone,

            last_msg.c.msg_at.label("last_message_at"),
            case(
                (last_msg.c.direction == MessageDirection.out, "Вы: "),
                (last_msg.c.direction == MessageDirection.sys, "INFO: "),
                else_=""
            ).concat(last_msg.c.text).label("last_message"),

            func.count(distinct(m.c.id)).filter(
                (m.c.direction == MessageDirection.inc) &
                (m.c.is_seen.is_(False))
            ).label("unread"),
        )
        .join(last_msg, last_msg.c.conversation_id == c.c.id, isouter=True)
        .join(m, m.c.conversation_id == c.c.id)
        .where(c.c.instance_id == instance_id)
        .group_by(
            c.c.id, c.c.chat_id, c.c.title, c.c.phone,
            last_msg.c.msg_at, last_msg.c.text, last_msg.c.direction,
        )
    )

    if tag_ids:
        stmt = stmt.join(conversation_tags).where(conversation_tags.c.tag_id.in_(tag_ids))
    if q:
        like = f"%{q}%"
        stmt = stmt.where((c.c.title.ilike(like)) | (c.c.phone.ilike(like)))

    stmt = (
        stmt.order_by(c.c.pinned.desc(), last_msg.c.msg_at.desc().nullslast())
            .limit(limit).offset(offset)
    )

    rows = (await session.execute(stmt)).mappings().all()
    return rows            # отдаём list[Mapping] / list[dict]


async def list_conversations(
    session: AsyncSession,
    *,
    instance_id: int,
    tag_ids: list[int] | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0
):
    """
    Список чатов с последним сообщением и счётчиком непрочитанных.
    """
    m = Message.__table__
    c = Conversation.__table__

    # CTE с последним сообщением
    last_msg = (
        select(
            Message.conversation_id,
            Message.id.label("msg_id"),
            Message.text,
            Message.direction,
            Message.created_at.label("msg_at")
        )
        .order_by(Message.conversation_id, Message.created_at.desc())
        .distinct(Message.conversation_id)
        .cte("last_msg")
    )

    stmt = (
        select(Conversation)
        .join(last_msg, last_msg.c.conversation_id == Conversation.id, isouter=True)
        .where(Conversation.instance_id == instance_id)
    )

    # фильтры по тегам / поиску
    if tag_ids:
        stmt = (
            stmt.join(conversation_tags)
                .where(conversation_tags.c.tag_id.in_(tag_ids))
        )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Conversation.title.ilike(like)) | (Conversation.phone.ilike(like))
        )

    stmt = (
        stmt.order_by(Conversation.pinned.desc(),
                      last_msg.c.msg_at.desc().nullslast())
        .limit(limit)
        .offset(offset)
    )

    res = await session.execute(stmt)
    return res.scalars().unique().all()


async def mark_all_messages_seen(
    session: AsyncSession,
    *,
    conversation_id: int | None = None,
    instance_id: int | None = None,
    chat_id: str | None = None,
) -> int:
    """
    Помечает все входящие сообщения диалога как прочитанные
    (is_seen = True) и обнуляет unread_inc_count.

    Возвращает количество обновлённых сообщений.

    Используйте либо `conversation_id`, либо пару
    `(instance_id, chat_id)` – одно из двух.
    """
    # ── определить conversation_id, если не пришёл ────────────────
    if conversation_id is None:
        if instance_id is None or chat_id is None:
            raise ValueError("Нужно conversation_id ИЛИ (instance_id + chat_id)")
        conversation_id = await session.scalar(
            select(Conversation.id).where(
                Conversation.instance_id == instance_id,
                Conversation.chat_id == chat_id,
            )
        )
        if conversation_id is None:
            return 0

    # ── 1. помечаем сообщения ─────────────────────────────────────
    result = await session.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.direction == MessageDirection.inc,
            Message.is_seen.is_(False),
        )
        .values(is_seen=True)
        .execution_options(synchronize_session=False)  # быстрее, т.к. bulk
    )
    updated_rows = result.rowcount or 0

    # ── 2. обнуляем счётчик в Conversation ────────────────────────
    if updated_rows:
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(unread_inc_count=0)
        )

    await session.commit()
    return updated_rows


async def mark_conversation_read(session, *, conversation_id: int):
    # 1) обнуляем счётчик
    conv = await session.get(Conversation, conversation_id)
    if not conv:
        return None

    # 2) апдейтим сообщения
    await session.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.direction == MessageDirection.inc,
            Message.is_seen.is_(False),
        )
        .values(is_seen=True)
    )

    conv.unread_inc_count = 0
    await session.commit()
    await session.refresh(conv)
    return conv


async def search_messages(session: AsyncSession, *, instance_id: int, q: str,
                          conversation_id: int | None = None, limit: int = 50, offset: int = 0):
    query = func.websearch_to_tsquery('russian', q)
    stmt = select(Message).join(Conversation).where(
        Conversation.instance_id == instance_id,
        Message.text_search.op('@@')(query)
    )
    if conversation_id:
        stmt = stmt.where(Message.conversation_id == conversation_id)

    stmt = stmt.order_by(func.ts_rank_cd(Message.text_search, query).desc(),
                         Message.created_at.desc()).limit(limit).offset(offset)
    res = await session.execute(stmt)
    return list(res.scalars())
