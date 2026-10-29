"""Load API keys from a `.env` file when present (KEY=VALUE lines; never committed)."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def key_for(provider: str) -> str | None:
    return os.environ.get({"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[provider])
