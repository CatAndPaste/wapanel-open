# shared/crud/user.py
from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import User, Instance


async def _uniq_username(session: AsyncSession, username: str, exclude_id: int | None = None) -> None:
    q = select(User.id).where(User.username == username)
    if exclude_id is not None:
        q = q.where(User.id != exclude_id)
    exists = await session.scalar(q)
    if exists:
        raise ValueError(f"Пользователь с таким именем уже существует")


async def list_users(session: AsyncSession, *, requested_by: User) -> list[User]:
    """
    • если у запрашивающего есть can_manage_users / is_owner → возвращаем всех
    • иначе — только самого запрашивающего
    """
    has_rights = bool(requested_by.is_owner or requested_by.can_manage_users)
    q = select(User) if has_rights else select(User).where(User.id == requested_by.id)
    return list((await session.execute(q)).scalars().all())


async def get_user_by_username(session: AsyncSession, *, username: str) -> Optional[User]:
    return await session.scalar(select(User).where(User.username == username))


async def get_users_by_tg_id(session: AsyncSession, *, telegram_id: int) -> list[User]:
    q = select(User).where(User.telegram_id == telegram_id)
    return list((await session.execute(q)).scalars().all())


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    telegram_id: int | None = None,
    # permissions
    is_owner: bool = False,
    is_2fa_enabled: bool = True,
    can_manage_users: bool = False,
    can_manage_instances: bool = False,
    # instance access
    full_access: bool = False,
    instance_ids: Sequence[int] | None = None,
) -> User:
    """
    Создаём пользователя и возвращаем его ORM-объект.

    ⚠️ Если *full_access* = True, *instance_ids* игнорируется.
    """
    if not username.strip():
        raise ValueError("Имя пользователя не может быть пустым")
    await _uniq_username(session, username)

    user = User(
        username=username.strip(),
        telegram_id=telegram_id,
        is_owner=is_owner,
        is_2fa_enabled=is_2fa_enabled,
        can_manage_users=can_manage_users,
        can_manage_instances=can_manage_instances,
        full_access=full_access,
    )
    user.password = password

    if instance_ids:
        valid = []
        for iid in instance_ids:
            inst = await session.get(Instance, iid)
            if inst is None:
                continue
            valid.append(inst)
        user.instances = valid

    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def update_user(               # noqa: C901 — большая, но читаемая
    session: AsyncSession,
    user: User,
    *,
    # базовые поля
    username: str | None = None,
    telegram_id: int | None = None,
    is_active: bool | None = None,
    new_password: str | None = None,
    is_2fa_enabled: bool | None = None,
    # права
    can_manage_users: bool | None = None,
    can_manage_instances: bool | None = None,
    full_access: bool | None = None,
    # доступ к инстансам
    instance_ids: Sequence[int] | None = None,
) -> User:
    """
    Обновляем поля пользователя; возвращаем актуальный объект.

    • Если *full_access* становится True → обнуляем «частный» список инстансов
    • Если *full_access* False и instance_ids не None → задаём новый список
    """
    # ---------- базовые поля ----------
    if username is not None and username != user.username:
        if not username.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        await _uniq_username(session, username, exclude_id=user.id)
        user.username = username.strip()

    if telegram_id is not None:
        user.telegram_id = telegram_id

    if is_active is not None:
        user.is_active = is_active

    if new_password is not None:
        user.password = new_password

    if is_2fa_enabled is not None and not user.is_owner:   # владельцу нельзя отключить 2FA
        user.is_2fa_enabled = is_2fa_enabled

    # ---------- права ----------
    if can_manage_users is not None:
        user.can_manage_users = can_manage_users

    if can_manage_instances is not None:
        user.can_manage_instances = can_manage_instances

    # ---------- доступ к инстансам ----------
    if full_access is not None:
        user.full_access = full_access
        if full_access:
            user.instances.clear()

    if instance_ids is not None:
        valid = []
        for iid in instance_ids:
            inst = await session.get(Instance, iid)
            if inst is None:
                continue
            valid.append(inst)
        user.instances = valid

    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def delete_user(session: AsyncSession, *, user: User) -> None:
    await session.delete(user)
    await session.commit()
