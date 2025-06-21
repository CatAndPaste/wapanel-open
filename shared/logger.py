import logging
import logging.handlers as lh
import os, sys
from pathlib import Path
from typing import Union

_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_datetime = "%d-%m %H:%M:%S"

formatter = logging.Formatter(_format, _datetime)

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(formatter)
stdout_handler.setLevel(logging.INFO)

LOG_DIR = Path(os.getenv("LOG_DIR", "/app/logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
# 1mb, 10 files rotating handler
file_handler = lh.RotatingFileHandler(
    LOG_DIR / "app.log",
    maxBytes=1_000_000,
    backupCount=10,
    encoding="utf-8",
)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(stdout_handler)
root.addHandler(file_handler)
root.propagate = False

# shutup, libraries! (clears up logs a little)
_libs = {
    "aiohttp.access":        logging.WARNING,
    "aiohttp.server":        logging.WARNING,
    "asyncio":               logging.WARNING,
    "sqlalchemy.engine":     logging.WARNING,
    "sqlalchemy.pool":       logging.WARNING,
    "alembic":               logging.WARNING,
    "uvicorn.access":        logging.WARNING,
    "aiogram":               logging.WARNING,
    "aiogram.event":         logging.WARNING,
}
for module, level in _libs.items():
    logging.getLogger(module).setLevel(level)


def get_logger(name: Union[str, None] = None) -> logging.Logger:
    return logging.getLogger(name)
