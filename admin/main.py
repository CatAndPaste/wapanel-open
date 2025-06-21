import fastapi
from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.status import HTTP_401_UNAUTHORIZED

from admin.lifespan import lifespan
from admin.routes import register_all_routers
from admin.utils.config import settings

# initialize templates
from admin import templating

from admin.middleware.DBSessionMiddleware import DBSessionMiddleware

admin_app = FastAPI(title="Green Connect", lifespan=lifespan)


def _html_error(request: Request, detail: str, code: int):
    return templating.templates.TemplateResponse(
        "error.html",
        {"request": request, "message": f"{detail}"},
        status_code=code,
    )


# 404 HANDLER
@admin_app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    if exc.status_code == 404:
        accept = request.headers.get("accept", "")
        if "application/json" in accept or request.headers.get("HX-Request"):
            return await fastapi.exception_handlers.http_exception_handler(request, exc)

        return _html_error(request, str(exc.detail or "Неизвестная ошибка"), exc.status_code)

    return await fastapi.exception_handlers.http_exception_handler(request, exc)


admin_app.add_middleware(DBSessionMiddleware)
admin_app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


admin_app.mount("/static", StaticFiles(directory="admin/static"), name="static")


# REST / websockets
register_all_routers(admin_app)
