import hashlib
from datetime import datetime

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from admin.utils.db import async_session_maker
from shared.crud.session import get_session_by_hash, touch_session
from shared.models import DBSession


class DBSessionMiddleware(BaseHTTPMiddleware):
    COOKIE = "g-session"

    async def dispatch(self, request: Request, call_next):
        token = request.cookies.get(self.COOKIE)
        request.state.user = None
        request.state.csrf = None

        if token:
            dhash = hashlib.sha256(token.encode()).hexdigest()
            async with async_session_maker() as db:  # type: AsyncSession
                sess: DBSession | None = await get_session_by_hash(
                    db,
                    token_hash=dhash
                )

                if sess and sess.is_active and not sess.is_expired():
                    await touch_session(db, sess)
                    request.state.user = sess.user
                    request.state.csrf = sess.csrf_token

        response: Response = await call_next(request)
        return response
