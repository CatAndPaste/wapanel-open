from datetime import datetime, date, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from babel.dates import format_date, format_time

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from starlette.datastructures import URL

templates = Jinja2Templates(directory="admin/templates")

MONTH_FMT = "d MMMM"
TZ_ADMIN = ZoneInfo("Europe/Moscow")

@pass_context
def urlx_for(context: dict, name: str, **path_params: Any, ) -> URL:
    request: Request = context['request']
    http_url = request.url_for(name, **path_params)
    if scheme := request.headers.get('x-forwarded-proto'):
        return http_url.replace(scheme=scheme)
    return http_url


def human_date(ts: datetime, locale: str = "ru") -> str:
    d = ts.astimezone(TZ_ADMIN).date()
    today = date.today()
    if d == today:
        return "Сегодня"
    if d == today - timedelta(days=1):
        return "Вчера"
    return format_date(d, MONTH_FMT, locale=locale)


def local_time(dt, locale="ru", tz=TZ_ADMIN):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(tz)
    return format_time(dt, "HH:mm", locale=locale)


templates.env.globals['utcnow'] = lambda: datetime.utcnow() + timedelta(hours=3)
templates.env.globals['url_for'] = urlx_for
templates.env.filters["hdate"] = human_date
templates.env.filters["localtime"] = local_time
