import pytest
from src import config


def test_get_database_url_reads_env_var(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://localhost/testdb")
    assert config.get_database_url() == "postgresql+psycopg2://localhost/testdb"


def test_get_database_url_raises_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(config.ConfigError):
        config.get_database_url()


def test_get_basic_auth_credentials_reads_env_vars(monkeypatch):
    monkeypatch.setenv("BASIC_AUTH_USER", "admin")
    monkeypatch.setenv("BASIC_AUTH_PASS_HASH", "pbkdf2:sha256:600000$abc$def")
    assert config.get_basic_auth_credentials() == ("admin", "pbkdf2:sha256:600000$abc$def")


def test_get_basic_auth_credentials_raises_when_user_unset(monkeypatch):
    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.setenv("BASIC_AUTH_PASS_HASH", "hash")
    with pytest.raises(config.ConfigError):
        config.get_basic_auth_credentials()


def test_get_basic_auth_credentials_raises_when_pass_hash_unset(monkeypatch):
    monkeypatch.setenv("BASIC_AUTH_USER", "admin")
    monkeypatch.delenv("BASIC_AUTH_PASS_HASH", raising=False)
    with pytest.raises(config.ConfigError):
        config.get_basic_auth_credentials()
