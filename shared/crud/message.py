from typing import Optional, List, Any
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.models import Message, MessageFile, MessageDirection, MessageType, MessageStatus, FileType


async def list_messages(
    session: AsyncSession,
    *,
    instance_id: Optional[int] = None,
    chat_id: Optional[str] = None,
    offset: int = 0,
    limit: int = 100
) -> List[Message]:
    """
    Get [Message] for instance_id & chat_id
    Loads instance & files. Pagination support (offset, limit)
    """
    if instance_id is None or chat_id is None:
        return []

    q = select(Message).options(
        selectinload(Message.instance),
        selectinload(Message.files)
    ).order_by(Message.created_at.desc())
    if instance_id is not None:
        q = q.where(Message.instance_id == instance_id)
    if chat_id is not None:
        q = q.where(Message.chat_id == chat_id)
    q = q.offset(offset).limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_message_by_id(
    session: AsyncSession,
    *,
    message_id: int
) -> Optional[Message]:
    """
    Get Message by its internal ID
    """
    q = select(Message).options(
        selectinload(Message.instance),
        selectinload(Message.files)
    ).where(Message.id == message_id)
    result = await session.execute(q)
    return result.scalars().first()


async def get_message_by_wa_id(
    session: AsyncSession,
    *,
    instance_id: int,
    wa_message_id: str
) -> Optional[Message]:
    """
    Get Message by Instance ID & we_message_id
    Example:
    msg = await get_message_by_wa_id(
        session,
        instance_id=2,
        wa_message_id="88005553535",
    )
    """
    q = select(Message).options(
        selectinload(Message.instance),
        selectinload(Message.files)
    ).where(
        Message.instance_id == instance_id,
        Message.wa_message_id == wa_message_id
    )
    result = await session.execute(q)
    return result.scalars().first()


async def create_message(
    session: AsyncSession,
    *,
    instance_id: int,
    chat_id: str,
    chat_name: str,
    from_app: bool,
    direction: MessageDirection,
    message_type: MessageType,
    wa_message_id: Optional[str] = None,
    status: Optional[MessageStatus] = MessageStatus.pending,
    text: Optional[str] = None,
    quote_id: Optional[int] = None,
    commit: bool = True
) -> Message:
    """
    Creates new Message object
    Example:
    msg = await create_message(
            session,
            instance_id=1,
            wa_message_id="88005553535",
            chat_id="79957889000@c.us",
            chat_name="79957889000",
            from_app=True,
            direction=MessageDirection.out,
            message_type=MessageType.text,
            text="Yo!"
        )
    """
    # TODO: CHANGE THIS TO NEW INTERFACE
    msg = Message(
        instance_id=instance_id,
        wa_message_id=wa_message_id,
        chat_id=chat_id,
        chat_name=chat_name,
        from_app=from_app,
        direction=direction,
        message_type=message_type,
        status=status,
        text=text,
        quote_id=quote_id,
    )
    session.add(msg)
    if commit:
        await session.commit()
        await session.refresh(msg)
    else:
        await session.flush()
    return msg


async def create_message_file(
    session: AsyncSession,
    *,
    message: Message,
    file_type: FileType,
    name: str,
    mime: str,
    file_path: str,
    file_url: str,
    size: Optional[int] = None
) -> MessageFile:
    """
    Create and attach a MessageFile to an existing Message
    Example:
    file = await create_message_file(
            session,
            message=msg,
            file_type=FileType.image,
            name="test.png",
            mime="image/png",
            file_path="/media/test.png",
            file_url="/media/test.png",
            size=130000
        )
    """
    if not message.is_file:
        raise ValueError("Message is not of a file type!")
    file = MessageFile(
        message=message,
        file_type=file_type,
        name=name,
        mime=mime,
        file_path=file_path,
        file_url=file_url,
        size=size
    )
    session.add(file)
    await session.commit()
    await session.refresh(file)
    return file


async def update_message(
    session: AsyncSession,
    message: Message,
    **kwargs: Any
) -> Message:
    """
    Updates Message
    Example:
    msg = await get_message_by_wa_id(
            session,
            instance_id=2,
            wa_message_id="88005553535",
        )
    if msg is None:
        logger.error("no msg found!")
        return
    msg = await update_message(
            session,
            message=msg,
            status=MessageStatus.sent
        )
    """
    for field, value in kwargs.items():
        if hasattr(message, field):
            setattr(message, field, value)
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


async def delete_message(
    session: AsyncSession,
    *,
    message: Message
) -> None:
    """
    Deletes Message
    """
    await session.delete(message)
    await session.commit()
