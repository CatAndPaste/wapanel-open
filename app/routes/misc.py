from aiohttp import web

routes = web.RouteTableDef()


@routes.get("/healthz")
async def health_check(_: web.Request) -> web.Response:
    return web.Response(text='OK', status=200)
