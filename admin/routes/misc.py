import pathlib

from fastapi import APIRouter, HTTPException
from starlette import status
from starlette.responses import FileResponse

router = APIRouter()


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("admin/static/favicon.ico", media_type="image/x-icon")


@router.get("/healthz")
async def healthz():
    """
        healthcheck:
          test: [ "CMD", "curl", "-f", "http://localhost:8002/healthz" ]
          interval: 10s
          retries: 5
    """
    return {"status": "ok"}