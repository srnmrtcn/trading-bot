from __future__ import annotations

import os


class ConfigError(Exception):
    pass


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ConfigError("DATABASE_URL environment variable is not set")
    return url


def get_basic_auth_credentials() -> tuple[str, str]:
    user = os.environ.get("BASIC_AUTH_USER")
    pass_hash = os.environ.get("BASIC_AUTH_PASS_HASH")
    if not user or not pass_hash:
        raise ConfigError("BASIC_AUTH_USER and BASIC_AUTH_PASS_HASH environment variables must be set")
    return user, pass_hash
