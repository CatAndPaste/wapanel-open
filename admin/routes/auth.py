import hashlib
import secrets
import bcrypt
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Dict

from fastapi import (
    APIRouter, Request, Depends, Form
)
from fastapi.exceptions import HTTPException
from starlette.datastructures import URL
from starlette.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, select

from admin.templating import templates
from admin.utils.bot import send_notification
from admin.utils.config import settings
from admin.utils.sessions import create_session_tokens
from admin.utils.db import get_session
from admin.utils.urls import sanitize_next
from shared.crud.user import get_user_by_username
from shared.models import DBSession, User, BotMeta, Instance

router = APIRouter()


# CHALLENGES

@dataclass
class Challenge:
    uid: int
    hash: str  # code bcrypt hash
    exp: datetime
    tries: int = 0


CHALLENGES: Dict[str, Challenge] = {}
CODE_TTL = timedelta(minutes=30)
MAX_TRIES = 5
CHALLENGE_COOKIE = "g-challenge"
SESSION_COOKIE = "g-session"


def _cleanup_expired() -> None:
    now = datetime.utcnow()
    to_drop = [cid for cid, ch in CHALLENGES.items() if ch.exp < now or ch.tries >= MAX_TRIES]
    for cid in to_drop:
        CHALLENGES.pop(cid, None)


def _create_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def redirect_login(reason: str, *, next: str | None = None):
    """
    -> /login?e=<reason>.
    HX-Redirect header for HTMX
    """
    url = f"/login?e={reason}"
    if next is not None and next != "/":
        url += f"&next={next}"
    resp = RedirectResponse(url, status_code=302)
    resp.headers["HX-Redirect"] = url
    return resp


# ROUTES
@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, e: str | None = None, next: str | None = None):
    _cleanup_expired()
    if request.state.user:
        return RedirectResponse("/", status_code=302)

    msg = None
    if e == "noauth":
        msg = "Пожалуйста войдите в систему"
    elif e == "maxtries":
        msg = "Вы исчерпали количество попыток. Попробуйте войти заново"
    elif e == "expired":
        msg = "Код больше недействителен. Войдите заново"

    # active challenge -> 2FA form
    if (cid := request.cookies.get(CHALLENGE_COOKIE)) and cid in CHALLENGES:
        return templates.TemplateResponse("auth/2fa_form.html",
                                          {"request": request, "error": msg, "next": next or "/"})

    return templates.TemplateResponse("auth/login_page.html",
                                      {"request": request, "error": msg, "next": next or "/"})


@router.post("/login", response_class=HTMLResponse)
async def login_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next: str = Form(default="/"),
        db: AsyncSession = Depends(get_session),
):
    _cleanup_expired()

    user = await get_user_by_username(db, username=username)
    if not user or not user.verify_password(password):
        templ = "auth/login_form.html" if request.headers.get("HX-Request") else "auth/login_page.html"
        return templates.TemplateResponse(templ, {"request": request, "error": "Неверные данные!", "next": next})

    if user.is_2fa_enabled:
        if user.telegram_id is None:
            templ = "auth/login_form.html" if request.headers.get("HX-Request") else "auth/login_page.html"
            return templates.TemplateResponse(templ, {"request": request, "error": "Для вашего аккаунта включена 2FA, "
                                                                                   "но не задан Telegram ID. Пожалуйста свяжитесь с пользователем, предоставившим доступ.",
                                                      "next": next})
        # code / challenge
        code = _create_code()
        cid = secrets.token_hex(8)
        ch_exp = datetime.utcnow() + CODE_TTL

        CHALLENGES[cid] = Challenge(
            uid=user.id,
            hash=bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode(),
            exp=ch_exp,
        )

        # tg notify
        sent = await send_notification(
            user.telegram_id,
            f"Ваш код подтверждения: `{code}`\nПопытка входа в админ-панель https://{settings.WEBHOOK_HOST}/.",
            use_markdown=True,
        )

        if not sent:
            bot_meta: BotMeta | None = await db.scalar(select(BotMeta).limit(1))

            if bot_meta and bot_meta.is_active and bot_meta.username:
                extra = (f'Чтобы получить 2FA-код, начните диалог с ботом '
                         f'<a target="_blank" href="https://t.me/{bot_meta.username}">'
                         f'@{bot_meta.username}</a>')
            else:
                extra = 'Чтобы получить 2FA-код, начните диалог с ботом в Telegram'

            return templates.TemplateResponse(
                "auth/login_form.html",
                {"request": request,
                 "error": f"Не удалось отправить сообщение.<br>{extra}",
                 "next": next}
            )

        # 2fa form / cid cookie
        resp = templates.TemplateResponse("auth/2fa_form.html", {"request": request, "error": None, "next": next})
        resp.set_cookie(
            CHALLENGE_COOKIE, cid,
            max_age=int(CODE_TTL.total_seconds()),
            httponly=False, secure=True, samesite="strict",
            path="/"
        )
        return resp
    else:
        plain, digest, csrf = create_session_tokens()
        new_sess = DBSession(
            user_id=user.id,
            token_hash=digest,
            csrf_token=csrf,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:256],
        )
        db.add(new_sess)
        await db.commit()

        # response: session-cookie set, challenge-cookie removed
        resp = HTMLResponse("OK")
        safe_next = sanitize_next(next, user=user)
        resp.headers["HX-Redirect"] = safe_next
        resp.delete_cookie(CHALLENGE_COOKIE, path="/")
        resp.set_cookie(
            SESSION_COOKIE, plain,
            max_age=14 * 24 * 60 * 60,
            httponly=True, secure=True, samesite="strict",
            path="/"
        )
        return resp


@router.post("/2fa", response_class=HTMLResponse)
async def twofa_post(
        request: Request,
        code: str = Form(...),
        next: str = Form(default="/"),
        db: AsyncSession = Depends(get_session)
):
    _cleanup_expired()

    cid = request.cookies.get(CHALLENGE_COOKIE)
    ch = CHALLENGES.get(cid) if cid else None

    if not ch:
        return redirect_login("expired", next=next)

    # incorrect code
    if not bcrypt.checkpw(code.encode(), ch.hash.encode()):
        ch.tries += 1
        if ch.tries >= MAX_TRIES:
            CHALLENGES.pop(cid, None)
            return redirect_login("maxtries", next=next)
        return templates.TemplateResponse("auth/2fa_form.html",
                                          {"request": request, "error": "Неверный код", "next": next})

    # DBSession created
    plain, digest, csrf = create_session_tokens()
    new_sess = DBSession(
        user_id=ch.uid,
        token_hash=digest,
        csrf_token=csrf,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:256],
    )
    db.add(new_sess)
    await db.commit()

    # cleanup
    CHALLENGES.pop(cid, None)

    # response: session-cookie set, challenge-cookie removed
    resp = HTMLResponse("OK")
    user = await db.get(User, ch.uid)
    resp.headers["HX-Redirect"] = sanitize_next(next, user=user)
    resp.delete_cookie(CHALLENGE_COOKIE, path="/")
    resp.set_cookie(
        SESSION_COOKIE, plain,
        max_age=14 * 24 * 60 * 60,
        httponly=True, secure=True, samesite="strict",
        path="/"
    )
    return resp


@router.get("/logout")
async def logout(
        request: Request,
        db: AsyncSession = Depends(get_session)
):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        tok_hash = hashlib.sha256(token.encode()).hexdigest()
        await db.execute(
            update(DBSession).where(DBSession.token_hash == tok_hash).values(is_active=False)
        )
        await db.commit()

    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
