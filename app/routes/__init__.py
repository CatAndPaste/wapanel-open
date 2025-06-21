from aiohttp import web
from .admin import routes as admin_routes
from .misc import routes as misc_routes
from app.green_api.webhook import routes as webhook_routes
from .admin_qr import routes as admin_qr_routes


def setup_routes(app: web.Application) -> None:
    app.add_routes(admin_routes)
    app.add_routes(admin_qr_routes)
    app.add_routes(misc_routes)
    app.add_routes(webhook_routes)
