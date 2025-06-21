from shared.models import Message, MessageDirection, MessageStatus, MessageType
from shared import locale as L


async def notify_send_error(db, orig: Message, reason: str) -> None:
    """
    Creates system notification Message in DB with error-text.
    """
    # TODO: Another place for chat id in messages
    sys_msg = Message(
        instance_id=orig.instance_id,
        conversation_id=orig.conversation_id,
        chat_id=orig.chat_id,
        chat_name=orig.chat_name,
        wa_message_id=None,
        direction=MessageDirection.sys,
        from_app=True,
        status=MessageStatus.incoming,
        message_type=MessageType.notification,
        text=f"{L.ERR_PREFIX}\n```{reason}```",
    )
    db.add(sys_msg)
    await db.commit()
