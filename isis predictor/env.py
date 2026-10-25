"""Load KEY=value lines from .env into os.environ (without overriding real env vars)."""

from pathlib import Path
import os

ENV_FILE = Path(__file__).resolve().parent / ".env"


def load_env():
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_env()
