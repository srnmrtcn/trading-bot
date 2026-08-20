from __future__ import annotations

import os


class ConfigError(Exception):
    pass


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ConfigError("DATABASE_URL environment variable is not set")
    return url
