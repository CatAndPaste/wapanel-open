from urllib.parse import urlparse

from admin.utils.config import settings
from shared.models import User


def default_user_page(user: User) -> str:
    """
    Select default (home) page for user depends on their permissions.
    """
    from admin.utils.security import can_manage_instances, can_manage_users

    if can_manage_instances(user):
        return "/"
    if can_manage_users(user):
        return "/users"
    return "/chats"


def sanitize_next(next_url: str, *, user: User | None = None) -> str:
    """
    Returns safe non-absolute Path without domain/protocol, no external redirects.
    """
    if not next_url or next_url == "/":
        return default_user_page(user) if user else "/"
    parsed = urlparse(next_url)
    if parsed.netloc and parsed.netloc != settings.WEBHOOK_HOST:
        return default_user_page(user) if user else "/"
    return parsed.path or default_user_page(user) if user else "/"
