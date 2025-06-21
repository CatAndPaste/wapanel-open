import asyncio
import json

from fastapi import APIRouter, Depends, Request, Path
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from admin.templating import templates
from admin.utils.bot import update_channel, logout_instance, get_qr, start_history, refresh_instance
from admin.utils.db import get_session
from admin.utils.security import require_admin, can_manage_instances, has_instance_access
from admin.utils.logger import logger
from shared.crud.instance import list_instances, get_instance_by_id, delete_instance, update_instance
from shared.models import Instance, BotMeta, User, InstanceState

from pydantic import BaseModel, Field, validator
from fastapi import HTTPException, status, Form
from shared.crud.instance import create_instance, get_instance_by_api_id

router = APIRouter()


def _hx_alert(text: str, status: int = 200) -> HTMLResponse:
    """
    Возвращает "пустой" ответ, но с HX-Trigger,
    который фронт покажет как alert().
    """
    resp = HTMLResponse("", status_code=status)
    resp.headers["HX-Trigger"] = json.dumps({"hx-alert": {"msg": text}})
    return resp


class InstanceCreateForm(BaseModel):
    api_id: int = Field(alias="api_id")
    api_url: str = Field(min_length=1, alias="api_url")
    media_url: str = Field(min_length=1, alias="media_url")
    api_token: str = Field(min_length=1, alias="api_token")
    tg_id: int = Field(alias="tg_id")
    download_history: bool = Field(default=False, alias="download_history")
    auto_reply: bool = Field(default=False, alias="auto_reply")
    auto_reply_text: str | None = Field(alias="auto_reply_text")
    inst_name: str | None = Field(alias="inst_name")

    @classmethod
    def as_form(cls,
                api_id: str = Form(...),
                api_url: str = Form(...),
                media_url: str = Form(...),
                api_token: str = Form(...),
                tg_id: str = Form(...),
                download_history: bool = Form(False),
                auto_reply: bool = Form(False),
                auto_reply_text: str = Form(""),
                inst_name: str = Form(""),
                ):
        return cls(
            api_id=api_id,
            api_url=api_url.strip(),
            media_url=media_url.strip(),
            api_token=api_token.strip(),
            tg_id=tg_id,
            download_history=download_history,
            auto_reply=auto_reply,
            auto_reply_text=auto_reply_text.strip(),
            inst_name=inst_name.strip() or None
        )


@router.post("/instances/create", response_class=HTMLResponse)
async def create_instance_endpoint(
        request: Request,
        form: InstanceCreateForm = Depends(InstanceCreateForm.as_form),
        db: AsyncSession = Depends(get_session),
        user=Depends(require_admin),
):
    try:
        if not can_manage_instances(user):
            raise HTTPException(status_code=403, detail="У вас нет прав на создание новых инстансов")

        if form.auto_reply and not form.auto_reply_text.strip():
            raise HTTPException(status_code=400, detail="Текст авто-ответа обязателен, если включён авто-ответ")

        # check if api_id appears
        if await get_instance_by_api_id(db, api_id=form.api_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Инстанс с таким API ID уже существует",
            )

        # create
        try:
            tg_id = int(form.tg_id)
            await create_instance(
                db,
                api_id=form.api_id,
                api_url=form.api_url,
                media_url=form.media_url,
                api_token=form.api_token,
                telegram_channel_tg_id=tg_id,
                auto_reply=form.auto_reply,
                auto_reply_text=form.auto_reply_text,
                inst_name=form.inst_name
            )
            if not user.full_access and not user.is_owner:
                user.instances.append(await get_instance_by_api_id(db, api_id=form.api_id))
                await db.commit()

            task = asyncio.create_task(update_channel(tg_id))
            task.add_done_callback(
                lambda t: logger.error("update_channel failed: %s", t.exception())
                if t.exception() else None
            )

            if form.download_history:
                hist = asyncio.create_task(
                    start_history(form.api_id, wait_authorized=True)
                )
                hist.add_done_callback(
                    lambda t: logger.error("start_history failed: %s", t.exception())
                    if t.exception() else None
                )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


        # HX-Redirect to `/`
        resp = HTMLResponse("OK", status_code=201)
        resp.headers["HX-Redirect"] = "/"
        return resp
    except HTTPException as exc:
        # если запрос HTMX
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f"<span class='form-error'>{exc.detail}</span>",
                status_code=200
            )
        raise


@router.get("/", response_class=HTMLResponse)
async def instances_page(
        request: Request,
        db: AsyncSession = Depends(get_session),
        user=Depends(require_admin),
):
    instances = await list_instances(db, user=user)

    # 2. might be None
    bot_meta = await db.scalar(select(BotMeta).limit(1))

    return templates.TemplateResponse(
        "instances.html",
        {
            "request": request,
            "instances": instances,
            "bot": bot_meta,
            "active_page": "instances",
            "is_manager": can_manage_instances(user),
        },
    )


@router.get("/instances/{inst_id}/card", response_class=HTMLResponse)
async def instance_card(
        inst_id: int,
        request: Request,
        db: AsyncSession = Depends(get_session)
):
    inst = await get_instance_by_id(db, instance_id=inst_id)
    if not inst or not has_instance_access(request.state.user, inst):
        raise HTTPException(404)
    bot_meta = await db.scalar(select(BotMeta).limit(1))
    return templates.TemplateResponse(
        "instances/partials/instance_article.html",
        {
            "request": request,
            "inst": inst,
            "bot": bot_meta,
            "is_manager": can_manage_instances(request.state.user),
        },
    )


@router.delete("/instances/{inst_id}", status_code=204)
async def delete_instance_endpoint(
        inst_id: int = Path(..., ge=1),
        db: AsyncSession = Depends(get_session),
        user: User = Depends(require_admin),
):
    inst = await get_instance_by_id(db, instance_id=inst_id)
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(status_code=404, detail="Инстанс не найден")

    await delete_instance(db, instance=inst)


# EDIT

class InstanceUpdateForm(BaseModel):
    api_url: str
    media_url: str
    api_token: str
    tg_id: int
    auto_reply: bool = False
    auto_reply_text: str | None = None
    inst_name: str | None = None

    @classmethod
    def as_form(
        cls,
        api_url: str = Form(...),
        media_url: str = Form(...),
        api_token: str = Form(...),
        tg_id: int = Form(...),
        auto_reply: bool = Form(False),
        auto_reply_text: str = Form(""),
        inst_name: str = Form(""),
    ):
        return cls(
            api_url=api_url.strip(),
            media_url=media_url.strip(),
            api_token=api_token.strip(),
            tg_id=tg_id,
            auto_reply=auto_reply,
            auto_reply_text=auto_reply_text.strip() or None,
            inst_name=inst_name.strip() or None,
        )


@router.get("/instances/{inst_id}/edit", response_class=HTMLResponse)
async def instance_edit_form(
    inst_id: int,
    request: Request,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
):
    inst = await get_instance_by_id(db, instance_id=inst_id)
    if not inst or not can_manage_instances(user) or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден")
    bot_meta = await db.scalar(select(BotMeta).limit(1))
    return templates.TemplateResponse(
        "instances/partials/instance_edit.html",
        {
            "request": request,
            "inst": inst,
            "bot": bot_meta,
        },
    )


@router.put("/instances/{inst_id}", response_class=HTMLResponse)
async def instance_update(
    request: Request,
    inst_id: int,
    form: InstanceUpdateForm = Depends(InstanceUpdateForm.as_form),
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
):
    inst = await get_instance_by_id(db, instance_id=inst_id)
    if not inst or not can_manage_instances(user) or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден")

    if form.auto_reply and not form.auto_reply_text:
        return HTMLResponse(
            "<span class='form-error'>Текст авто-ответа обязателен</span>",
            status_code=200,
        )

    tg_id = int(form.tg_id)
    await update_instance(
        db,
        inst,
        api_url=form.api_url,
        media_url=form.media_url,
        api_token=form.api_token,
        telegram_channel_tg_id=tg_id,
        auto_reply=form.auto_reply,
        auto_reply_text=form.auto_reply_text,
        name=form.inst_name
    )

    bot_meta = await db.scalar(select(BotMeta).limit(1))
    return templates.TemplateResponse(
        "instances/partials/instance_article.html",
        {
            "request": request,
            "inst": inst,
            "bot": bot_meta,
            "is_manager": can_manage_instances(user),
        },
    )


# LOGOUT

@router.post("/instances/{inst_id}/logout", status_code=204)
async def logout_instance_endpoint(
    request: Request,
    inst_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
):
    inst = await get_instance_by_id(db, instance_id=inst_id)
    if not inst or not can_manage_instances(user) or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден")

    if inst.state is not InstanceState.authorized:
        raise HTTPException(409, "Инстанс не авторизован")

    ok = await logout_instance(inst.api_id)
    if not ok:
        raise HTTPException(502, "Не удалось разлогинить инстанс")

    return HTMLResponse(status_code=204)

# QR

@router.get("/instances/{inst_id}/qr")
async def qr_proxy(
    inst_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
):
    inst = await get_instance_by_id(db, instance_id=inst_id)
    if not inst or not can_manage_instances(user) or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден")

    payload = await get_qr(inst.api_id) or {"status": "error", "message": "no_response"}
    return payload


# history loader


@router.post("/instances/{inst_id}/history", response_class=HTMLResponse)
async def instance_history(
        request: Request,
        inst_id: int,
        db: AsyncSession = Depends(get_session),
        user: User = Depends(require_admin),
):
    inst = await get_instance_by_id(db, instance_id=inst_id)
    if not inst or not can_manage_instances(user) or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден")

    try:
        ok = await start_history(inst.api_id, wait_authorized=False)
    except Exception as e:
        return _hx_alert(f"Ошибка: {e}")

    if ok:
        return _hx_alert("Запущена загрузка истории сообщений", status=202)
    else:
        return _hx_alert("Получение истории в процессе")


@router.post("/instances/{inst_id}/refresh", response_class=HTMLResponse)
async def instance_refresh(
    request: Request,
    inst_id: int,
    db: AsyncSession = Depends(get_session),
    user:   User = Depends(require_admin),
):
    inst = await get_instance_by_id(db, instance_id=inst_id)
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден")

    ok = await refresh_instance(inst.api_id)

    if ok == "cooldown":
        return _hx_alert("Пожалуйста подождите, данные обновляются или недавно были обновлены", 429)
    if ok:
        return _hx_alert("Обновление запущено", 202)
    return _hx_alert("Не удалось отправить запрос", 502)
