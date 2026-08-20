import pytest
from src import config


def test_get_database_url_reads_env_var(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://localhost/testdb")
    assert config.get_database_url() == "postgresql+psycopg2://localhost/testdb"


def test_get_database_url_raises_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(config.ConfigError):
        config.get_database_url()
