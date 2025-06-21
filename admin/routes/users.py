from __future__ import annotations

import asyncio
import secrets
import string
import json
from typing import Optional, Sequence, List, Annotated

from fastapi import (
    APIRouter,
    Depends,
    Request,
    Form,
    HTTPException,
    status,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, validator
from sqlalchemy import update, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.templating import templates
from admin.utils.db import get_session
from admin.utils.security import (
    require_admin,
    can_manage_users,
    can_manage_instances,
)
from admin.utils.logger import logger
from shared.crud.user import (
    list_users,
    create_user,
    update_user,
    delete_user as crud_delete_user,
)
from shared.crud.instance import list_instances
from shared.models import User, DBSession, Instance


router = APIRouter(prefix="")


# helpers/ui utilities
def _hx_alert(msg: str, *, code: int = 200) -> HTMLResponse:
    """
    Empty HX-Trigger response, frontend will show it as alert() (simple notification)
    """
    resp = HTMLResponse("", status_code=code)
    resp.headers["HX-Trigger"] = json.dumps({"hx-alert": {"msg": msg}})
    return resp


def _hx_err(msg: str) -> HTMLResponse:
    """
    #user-errors HTML-fragment
    (hx-target="user-errors" hx-swap="innerHTML")
    """
    return HTMLResponse(f"<span class='form-error'>{msg}</span>", status_code=200)


def _pwd_gen(n: int = 10) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))

# deps
def require_manage_users(user: Annotated[User, Depends(require_admin)]) -> User:
    if can_manage_users(user):
        return user
    raise HTTPException(status_code=403, detail="Нет прав на управление пользователями")


# pydantic
class UserCreateForm(BaseModel):
    # general
    username: str
    password1: str
    password2: str
    telegram_id: Optional[str] = None
    is_2fa_enabled: bool = True
    # perms
    can_manage_users: bool = False
    can_manage_instances: bool = False
    # access
    full_access: bool = False
    instance_ids: List[int] = Field(default_factory=list)

    # fastapi factory
    @classmethod
    def as_form(
        cls,
        username: str = Form(...),
        password1: str = Form(...),
        password2: str = Form(...),
        telegram_id: str = Form(""),
        is_2fa_enabled: bool = Form(False),
        can_manage_users: bool = Form(False),
        can_manage_instances: bool = Form(False),
        full_access: bool = Form(False),
        instance_ids: List[int] = Form([]),
    ):
        return cls(
            username=username,
            password1=password1,
            password2=password2,
            telegram_id=telegram_id,
            is_2fa_enabled=is_2fa_enabled,
            can_manage_users=can_manage_users,
            can_manage_instances=can_manage_instances,
            full_access=full_access,
            instance_ids=instance_ids,
        )


class UserUpdateForm(BaseModel):
    # general
    username: Optional[str] = None
    password1: Optional[str] = None
    password2: Optional[str] = None
    telegram_id: Optional[str] = None
    is_2fa_enabled: Optional[bool] = None
    # perms
    can_manage_users: Optional[bool] = None
    can_manage_instances: Optional[bool] = None
    # access
    full_access: Optional[bool] = None
    instance_ids: Optional[List[int]] = None

    # fastapi factory
    @classmethod
    def as_form(
        cls,
        username: str = Form(""),
        password1: str = Form(""),
        password2: str = Form(""),
        telegram_id: str = Form(""),
        is_2fa_enabled:  Optional[bool] = Form(None),
        can_manage_users:  Optional[bool] = Form(None),
        can_manage_instances:  Optional[bool] = Form(None),
        full_access:  Optional[bool] = Form(None),
        instance_ids: Optional[List[int]] = Form(None),
    ):
        return cls(
            username=username,
            password1=password1,
            password2=password2,
            telegram_id=telegram_id,
            is_2fa_enabled=is_2fa_enabled,
            can_manage_users=can_manage_users,
            can_manage_instances=can_manage_instances,
            full_access=full_access,
            instance_ids=instance_ids,
        )


# ROUTES:
@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    db: AsyncSession = Depends(get_session),
    cur: User = Depends(require_admin),
):
    users = await list_users(db, requested_by=cur)
    users.sort(key=lambda u: (u.id != cur.id, u.username.lower()))
    insts = await list_instances(db, user=cur)

    return templates.TemplateResponse(
        "users/users.html",
        {
            "request": request,
            "users": users,
            "instances": insts,
            "cur": cur,
            "can_manage_users": can_manage_users(cur),
            "active_page": "users",
        },
    )


@router.post("/users", response_class=HTMLResponse)
async def users_create(
    request: Request,
    form: UserCreateForm = Depends(UserCreateForm.as_form),
    db: AsyncSession = Depends(get_session),
    cur: User = Depends(require_manage_users),
):
    # server-side validation
    if form.password1 != form.password2:
        return _hx_err("Пароли не совпадают")
    if not form.password1.strip():
        return _hx_err("Пароль не может быть пустым")

    try:
        if form.telegram_id:
            tg_id = int(form.telegram_id)
        else:
            tg_id = None
    except Exception as e:
        return _hx_err("Telegram ID может содержать только цифры и -")

    if tg_id is None and form.is_2fa_enabled:
        return _hx_err("Telegram ID не может быть пустым, если вы включили для пользователя 2FA")

    # creator cannot give perms higher that they have
    if form.can_manage_instances and not can_manage_instances(cur):
        return _hx_err("У вас нет права выдавать управление инстансами")
    if form.full_access and not (cur.full_access or cur.is_owner):
        return _hx_err("Вы сами не имеете полного доступа к инстансам")

    # instance filter based on creator
    allowed = {i.id for i in await list_instances(db, user=cur)}
    inst_ids = [iid for iid in form.instance_ids if iid in allowed]

    try:
        await create_user(
            db,
            username=form.username,
            password=form.password1,
            telegram_id=tg_id,
            is_2fa_enabled=form.is_2fa_enabled,
            can_manage_users=form.can_manage_users,
            can_manage_instances=form.can_manage_instances,
            full_access=form.full_access,
            instance_ids=inst_ids,
        )
    except ValueError as exc:
        return _hx_err(str(exc))

    resp = HTMLResponse("", status_code=201)
    resp.headers["HX-Redirect"] = "/users"
    return resp



@router.get("/users/{uid}/edit", response_class=HTMLResponse)
async def user_edit_form(
    uid: int,
    request: Request,
    db: AsyncSession = Depends(get_session),
    cur: User = Depends(require_admin),
):
    u = await db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")

    # no manage-perms -> edit only your entry
    if not can_manage_users(cur) and u.id != cur.id:
        raise HTTPException(403)

    has_foreign_access = not {i.id for i in cur.instances}.issuperset({i.id for i in u.instances})

    insts = await list_instances(db, user=cur)
    return templates.TemplateResponse(
        "users/partials/user_edit.html",
        {
            "request": request,
            "u": u,
            "instances": insts,
            "has_foreign_access": has_foreign_access,
        },
    )


@router.put("/users/{uid}", response_class=HTMLResponse)
async def user_update(
    uid: int,
    request: Request,
    form: UserUpdateForm = Depends(UserUpdateForm.as_form),
    db: AsyncSession = Depends(get_session),
    cur: User = Depends(require_admin),
):
    u = await db.get(User, uid)
    if not u:
        raise HTTPException(404)

    if not can_manage_users(cur) and u.id != cur.id:
        raise HTTPException(403)
    if u.is_owner and not cur.is_owner:
        raise HTTPException(403, "Нельзя редактировать владельца")

    # !!!
    # you cannot forbid yourself user management
    if form.can_manage_users is not None and not can_manage_users(cur):
        return _hx_err("У вас нет права изменять этот флаг")

    try:
        if form.telegram_id:
            tg_id = int(form.telegram_id)
        else:
            tg_id = None
    except Exception as e:
        return _hx_err("Telegram ID может содержать только цифры и -")

    if tg_id is None and form.is_2fa_enabled:
        return _hx_err("Telegram ID не может быть пустым, если вы включили для пользователя 2FA")

    # inst filter
    if form.instance_ids is not None:
        allowed = {i.id for i in await list_instances(db, user=cur)}
        form.instance_ids = [iid for iid in form.instance_ids if iid in allowed]

    try:
        await update_user(
            db,
            u,
            username=form.username,
            telegram_id=tg_id,
            new_password=form.password1 if form.password1 else None,
            is_2fa_enabled=form.is_2fa_enabled,
            can_manage_users=form.can_manage_users,
            can_manage_instances=form.can_manage_instances,
            full_access=form.full_access,
            instance_ids=form.instance_ids,
        )
    except ValueError as exc:
        return _hx_err(str(exc))

    # user changed -> invalidate user sessions
    if form.username or form.password1 or form.is_2fa_enabled is not None:
        await db.execute(
            update(DBSession)
            .where(DBSession.user_id == u.id)
            .values(is_active=False)
        )
        await db.commit()

    resp = HTMLResponse("", status_code=201)
    resp.headers["HX-Redirect"] = "/users"
    return resp


@router.delete("/users/{uid}", status_code=204)
async def user_delete(
    uid: int,
    db: AsyncSession = Depends(get_session),
    cur: User = Depends(require_manage_users),
):
    if uid == cur.id:
        raise HTTPException(400, "Нельзя удалить самого себя")

    u = await db.get(User, uid)
    if not u:
        raise HTTPException(404)

    if u.is_owner:
        raise HTTPException(403, "Владельца удалить нельзя")

    await crud_delete_user(db, user=u)
    return HTMLResponse(status_code=204)


@router.post("/users/{uid}/logout_sessions", status_code=204)
async def user_logout_sessions(
    uid: int,
    db: AsyncSession = Depends(get_session),
    cur: User = Depends(require_admin),
):
    if uid != cur.id and not can_manage_users(cur):
        raise HTTPException(403)

    await db.execute(
        update(DBSession).where(DBSession.user_id == uid).values(is_active=False)
    )
    await db.commit()
    return HTMLResponse(status_code=204)


# Me card for users without manage users perm
@router.get("/users/{uid}/card", response_class=HTMLResponse)
async def user_card(
    uid: int,
    request: Request,
    db: AsyncSession = Depends(get_session),
    cur: User = Depends(require_admin),
):
    u: User | None = await db.get(User, uid)
    if not u:
        raise HTTPException(404, "Пользователь не найден")

    if not can_manage_users(cur) and u.id != cur.id:
        raise HTTPException(404)

    return templates.TemplateResponse(
        "users/partials/user_article.html",
        {
            "request": request,
            "u": u,
            "cur": cur,
            "can_manage_users": can_manage_users(cur),
        },
    )
