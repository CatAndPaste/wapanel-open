from fastapi import Request, Form, Depends, HTTPException
from starlette.datastructures import URL

from shared.models import User, Instance


async def verify_csrf(
    request: Request,
    csrf: str = Form(...)
):
    sess_token = getattr(request.state, "csrf", None)
    if not sess_token or csrf != sess_token:
        raise HTTPException(400, "Bad CSRF token")


def require_admin(request: Request) -> User:
    user: User | None = request.state.user
    if user:
        return user

    next_url = URL("/login").include_query_params(next=str(request.url), e="noauth")
    raise HTTPException(status_code=302, headers={"Location": str(next_url), "HX-Redirect": str(next_url)})


def can_manage_users(u: User) -> bool:
    return bool(u.is_owner or u.can_manage_users)


def can_manage_instances(user: User) -> bool:
    return bool(user.is_owner or user.can_manage_instances)


def has_instance_access(user: User, inst: Instance) -> bool:
    if user.full_access or user.is_owner:
        return True

    allowed_ids = {i.id for i in getattr(user, "instances", [])}
    return inst.id in allowed_ids
