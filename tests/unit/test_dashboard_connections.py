"""Test unitari della scelta delle credenziali della dashboard
(`marketmind_frontend.connections`): nessuna connessione aperta, l'engine
nasce solo alla prima query."""

from __future__ import annotations

import pytest

from marketmind_db.access import APP, READ_ONLY
from marketmind_frontend.connections import dashboard_databases


@pytest.fixture
def env(monkeypatch):
    for name in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT",
                 "POSTGRES_DB", "MARKETMIND_READONLY_PASSWORD", "MARKETMIND_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "marketmind")
    monkeypatch.setenv("POSTGRES_PASSWORD", "admin")
    return monkeypatch


def test_each_role_with_its_own_credentials_and_policy(env):
    env.setenv("MARKETMIND_READONLY_PASSWORD", "ro")
    env.setenv("MARKETMIND_APP_PASSWORD", "app")

    dbs = dashboard_databases()

    assert dbs.reader.database.policy is READ_ONLY
    assert dbs.reader.database.settings.url.startswith("postgresql+psycopg://marketmind_readonly:ro@")
    assert not dbs.reader.fallback
    assert dbs.admin.database.policy is APP
    assert dbs.admin.database.settings.url.startswith("postgresql+psycopg://marketmind_app:app@")


def test_missing_role_password_falls_back_but_keeps_the_policy(env):
    dbs = dashboard_databases()

    assert dbs.reader.fallback and dbs.admin.fallback
    assert dbs.reader.database.settings.url.startswith("postgresql+psycopg://marketmind:admin@")
    assert dbs.reader.database.policy is READ_ONLY
