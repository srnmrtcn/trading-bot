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


_KAPALI = {"0", "false", "no", "off", "kapali", "hayir"}


def scenario_path_enabled() -> bool:
    """Saatlik senaryo/ogrenme/paper yolu acik mi? Varsayilan ACIK.

    29 Eylul 2026: bu yol haftalardir sifir sinyal uretiyor ve saatlik isin
    ~21 dakikasini 1h mum cekmeye harciyordu. Uretimde SCENARIO_PATH_ENABLED=0
    ile kapatilir; kod yolu silinmez, anahtar geri acilinca aynen calisir.
    Momentum defteri (gunluk futures mumu + funding gecmisi) bundan etkilenmez.
    """
    deger = os.environ.get("SCENARIO_PATH_ENABLED")
    if deger is None:
        return True
    return deger.strip().lower() not in _KAPALI
