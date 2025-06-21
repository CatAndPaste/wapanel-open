from enum import unique

from sqlalchemy import String, Integer, ForeignKey, Boolean, DateTime, Float, Text, Enum, BigInteger, UniqueConstraint, \
    Index, Table, Column, Computed
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, timedelta
import enum
from typing import Optional, List
from passlib.hash import bcrypt

from shared.database import Base


# ========
# Enums
# ========
class InstanceState(enum.Enum):
    unknown = "unknown"  # custom state (when couldn't get response from green api)
    authorized = "authorized"  #
    not_authorized = "notAuthorized"  # the rest is based on
    blocked = "blocked"  # https://green-api.com/en/docs/api/recommendations/instance-status-tracking/
    starting = "starting"
    yellow_card = "yellowCard"


class MessageDirection(enum.Enum):
    inc = "inc"  # из Green API (WhatsApp)
    out = "out"  # в WhatsApp
    sys = "sys"  # system


class MessageType(enum.Enum):  # mapping to Green API:
    text = "text"  # textMessage / extendedTextMessage / quotedMessage + reactionMessage (emoji)
    file_image = "file_image"  # imageMessage + stickerMessage (parse as image)
    file_video = "file_video"  # videoMessage
    file_audio = "file_audio"  # audioMessage
    file_doc = "file_doc"  # documentMessage
    notification = "notification"  # custom (our system messages)
    call = "call"
    # unused:
    location = "location"  # locationMessage (NYI)
    contact = "contact"  # contactMessage / contactsArray (NYI)


class MessageStatus(enum.Enum):
    # out:
    pending = "pending"  # just been created (not yet sent)
    sent = "sent"  # sent to Green API
    error_api = "api_error"  # Green API error
    error_int = "internal_error"  # internal error (e.g. container's down, network problems, etc.)
    # green api (detailed status):
    # https://green-api.com/en/docs/faq/whatsapp-messages-statuses/
    delivered = "delivered"
    read = "read"
    # in:
    incoming = "inc"  # incoming message has no explicit status


class FileType(enum.Enum):
    image = "image"  # stickers / images
    video = "video"
    audio = "audio"
    other = "other"  # document or other


# ========
# Models
# ========
class TelegramChannel(Base):
    __tablename__ = "tg_channels"

    # auto
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, default=None, nullable=True)
    url: Mapped[str] = mapped_column(String, default=None, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    # relations
    instances: Mapped[List["Instance"]] = relationship(
        "Instance",
        back_populates="telegram_channel",
        cascade="all, delete-orphan",
    )


user_instance_access = Table(
    """
    M2M users - instances
    """
    "user_instance_access",
    Base.metadata,
    Column("user_id", Integer,
           ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("instance_id", Integer,
           ForeignKey("instances.id", ondelete="CASCADE"), primary_key=True),
)


class Instance(Base):
    __tablename__ = "instances"

    # required
    api_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(String, unique=False, nullable=False)
    media_url: Mapped[str] = mapped_column(String, unique=False, nullable=False)
    api_token: Mapped[str] = mapped_column(String, unique=False, nullable=False)
    telegram_channel_id: Mapped[int] = mapped_column(ForeignKey("tg_channels.id", ondelete="CASCADE"), nullable=False)
    telegram_channel: Mapped[TelegramChannel] = relationship("TelegramChannel", back_populates="instances")
    # admin
    auto_reply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_reply_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # info
    # from Green API:
    name: Mapped[Optional[str]] = mapped_column(String, unique=False, nullable=True)
    state: Mapped[InstanceState] = mapped_column(Enum(InstanceState), default=InstanceState.unknown, nullable=False)
    phone: Mapped[str] = mapped_column(String, unique=False, nullable=True)
    photo_url: Mapped[str] = mapped_column(String, unique=False, nullable=True)
    # misc
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # relations
    messages: Mapped[List["Message"]] = relationship(
        "Message",
        back_populates="instance",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation",
        back_populates="instance",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    users: Mapped[List["User"]] = relationship(
        "User",
        secondary=user_instance_access,
        back_populates="instances",
        lazy="selectin",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("instance_id", "wa_message_id", name="uq_msg_wa"),
        Index(
            "ix_msg_conv",
            "instance_id", "chat_id", "created_at",
            postgresql_using="btree",
            postgresql_ops={"created_at": "DESC"},
        ),
        Index("ix_msg_conv_created_desc", "conversation_id", "created_at",
              postgresql_using="btree", postgresql_ops={"created_at": "DESC"}),
        Index("ix_msg_text_search", "text_search", postgresql_using="gin"),
        Index("ix_msg_inc_unseen", "conversation_id", "is_seen", "direction"),
    )

    # auto
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id", ondelete="CASCADE"), nullable=False)
    instance: Mapped[Instance] = relationship("Instance", back_populates="messages")
    wa_message_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    # chat info
    chat_name: Mapped[str] = mapped_column(String, nullable=False)  # 79957889000 or Contact Name
    chat_id: Mapped[str] = mapped_column(String, index=True,
                                         nullable=False)  # 79957889000@c.us (private) or 79957889046@g.us (group)
    from_app: Mapped[bool] = mapped_column(Boolean, default=True)  # if sent from this app (not phone or other way)
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection),
                                                        nullable=False, default=MessageDirection.inc)
    # content
    message_type: Mapped[MessageType] = mapped_column(Enum(MessageType), nullable=False)
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    # quotes (NYI)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    # misc
    # for incoming only: if message was seen through web chat
    is_seen: Mapped[bool] = mapped_column(Boolean, default=False)
    # if automatic response (to track auto reply cooldown)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    # relations
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    files: Mapped[list["MessageFile"]] = relationship(
        "MessageFile",
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # search
    text_search = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('russian', coalesce(text, ''))", persisted=True)
    )

    @property
    def is_file(self) -> bool:
        """ If message has files """
        return self.message_type.name.startswith("file_")

    @property
    def shortify(self) -> str:
        """ Returns truncated (max. 100) text content"""
        return (self.text or "")[:100]


class MessageFile(Base):
    __tablename__ = "message_files"

    # auto
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    message: Mapped[Message] = relationship(back_populates="files")
    # file info
    file_type: Mapped[FileType] = mapped_column(Enum(FileType), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    mime: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[int | None] = mapped_column(Integer)
    # path and url
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_url: Mapped[str] = mapped_column(String, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("instance_id", "chat_id", name="uq_conv_instance_chat"),
        Index("ix_conv_instance_arch_updated", "instance_id", "is_archived", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id", ondelete="CASCADE"), nullable=False)
    instance: Mapped["Instance"] = relationship(back_populates="conversations")

    chat_id: Mapped[str] = mapped_column(String, nullable=False)
    phone:   Mapped[str | None] = mapped_column(String)
    title:   Mapped[str | None] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(String)

    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    unread_inc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    tags: Mapped[list["ChatTag"]] = relationship(
        "ChatTag",
        secondary="conversation_tags",
        back_populates="conversations",
        lazy="selectin",
    )


class User(Base):
    __tablename__ = "users"

    # automated
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=True, default=None)
    # misc
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # staffer info
    is_2fa_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    # permissions
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)  # overwrites full access
    can_manage_users: Mapped[bool] = mapped_column(Boolean, default=False)
    can_manage_instances: Mapped[bool] = mapped_column(Boolean, default=False)
    # instance access
    full_access: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    instances: Mapped[list["Instance"]] = relationship(
        "Instance",
        secondary=user_instance_access,
        lazy="selectin",
    )
    # relations
    sessions: Mapped[list["DBSession"]] = relationship("DBSession",
                                                       cascade="all, delete-orphan", lazy="selectin")

    @property
    def password(self):
        raise AttributeError("Password is not stored on server!")

    @password.setter
    def password(self, pwd):
        self.hashed_password = bcrypt.hash(pwd)

    def verify_password(self, pwd):
        return bcrypt.verify(pwd, self.hashed_password)


conversation_tags = Table(
    "conversation_tags",
    Base.metadata,
    Column("conversation_id", ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("chat_tags.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_conv_tags_conv", "conversation_id"),
    Index("ix_conv_tags_tag", "tag_id"),
)


class ChatTag(Base):
    __tablename__ = "chat_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#337799")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversations: Mapped[list[Conversation]] = relationship(
        "Conversation",
        secondary=conversation_tags,
        back_populates="tags",
        lazy="selectin",
    )


class DBSession(Base):
    """
    Client-side: in cookie <g-session = token>
    Server-side: SHA256-hash <token_hash>
    + csrf_token for secured forms
    """
    __tablename__ = "db_sessions"
    __table_args__ = (
        Index("ix_db_sessions_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user: Mapped["User"] = relationship(back_populates="sessions")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(32), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @hybrid_property
    def expires_at(self) -> datetime:
        return self.last_seen + timedelta(days=14)

    def is_expired(self) -> bool:
        return datetime.utcnow() >= self.expires_at


class BotMeta(Base):
    __tablename__ = "bot_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    bot_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, default=None, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String, default=None, nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String, default=None, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
